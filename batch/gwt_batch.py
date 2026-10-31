#!/usr/bin/env python3
"""gwt-batch: turn open gwt-queue tasks into one public lookup URL each, and fetch them politely.

A template file says, per source, how a search URL is built. One TSV row per source: source, url (with {query}
and {label} placeholders), delay (seconds between two requests to that host, default 8), note.

  gwt-batch --queue cola.tsv --templates sources.tsv [--source KEY] [--limit 20]            # print id, source, URL
  gwt-batch --queue cola.tsv --templates sources.tsv --fetch --out raw/ [--delay 10]        # fetch, one at a time

Manners, not negotiable: robots.txt is read first and a disallowed URL is never fetched; one request at a time
per host with at least `delay` seconds between them; a descriptive User-Agent; a host that answers 403, 429 or
shows a captcha or a "too many requests" page is left alone for the rest of the run; no form submission, no
login, no captcha solving. The page is saved as OUT/<task id>.html for the reader.

A captcha or rate limit is not "nothing found". Every fetch is listed in OUT/outcomes.tsv, and the ones a person
has to do by hand (captcha, 403/429, host left alone) are also in OUT/manual.tsv with their URL. With --mark the
queue row of such a task becomes done with result "blocked" (robots.txt: "denied") and a note with the reason, so
it counts as not searched (a re-seed does not skip it) and shows in `gwt-queue list --result blocked`. Without
--mark the queue is never changed. A 200 answer that is an error (empty, a JSON error object, a page titled
error or not found) is not a result: nothing is saved, it shows as result "error" in outcomes.tsv and the task stays open. A page that came back ok is left to the reader (`gwt-queue done`).

Exit 0 when at least one URL was built, 1 when no open task has a template, 2 on a bad file.
"""
import argparse
import csv
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "queue"))
import gwt_queue as Q  # noqa: E402

AGENT = "gwt-batch/1.0 (genealogy research of deceased people; polite, single requests)"
BLOCK_MARKERS = ("captcha", "too many requests", "demasiadas solicitudes", "acceso denegado", "access denied")


ERROR_TITLES = ("404", "not found", "no encontrad", "introuvable", "error", "erreur", "service unavailable", "bad gateway")


def bad_body(body):
    """Why a 200 answer is not a result (a reason), or '' when it looks like a real page or data.
    Empty, a JSON error object, or an HTML page whose title says error / not found."""
    text = body.decode("utf-8", "replace").strip()
    if not text:
        return "empty body"
    if text[:1] in "{[":
        try:
            data = json.loads(text)
        except ValueError:
            return ""
        if isinstance(data, dict) and (data.get("error") or data.get("errors") or data.get("status") in ("error", "fail")):
            return "json error: %s" % str(data.get("error") or data.get("errors") or data.get("status"))[:80]
        return ""
    m = re.search(r"<title[^>]*>(.*?)</title>", text[:20000], re.I | re.S)
    if m and any(w in m.group(1).lower() for w in ERROR_TITLES):
        return "error page: %s" % " ".join(m.group(1).split())[:80]
    return ""


def load_templates(path):
    out = {}
    with open(path, encoding="utf-8-sig", newline="") as fh:
        for r in csv.DictReader(fh, delimiter="\t"):
            key = Q.key(r.get("source", ""))
            url = (r.get("url") or "").strip()
            if not key or not url.startswith(("http://", "https://")) or "{query}" not in url:
                raise ValueError("template for %r needs an http(s) url containing {query}" % r.get("source"))
            out[key] = {"url": url, "delay": float(r.get("delay") or 8), "note": r.get("note", "")}
    return out


def build_url(template, row):
    q = urllib.parse.quote_plus(row["query"] or row["person_label"])
    return template["url"].replace("{query}", q).replace("{label}", urllib.parse.quote_plus(row["person_label"]))


def tasks(rows, templates, source=None, limit=0):
    out = []
    for r in sorted((r for r in rows if r["status"] == "open"), key=Q.by_priority):
        t = templates.get(Q.key(r["source"]))
        if not t or (source and Q.key(source) != Q.key(r["source"])):
            continue
        out.append((r, t, build_url(t, r)))
        if limit and len(out) >= limit:
            break
    return out


class Polite:
    def __init__(self, delay_floor=0.0, sleep=time.sleep, clock=time.monotonic):
        self.last, self.robots, self.blocked = {}, {}, set()
        self.floor, self.sleep, self.clock = delay_floor, sleep, clock

    def allowed(self, url):
        host = urllib.parse.urlsplit(url)
        base = "%s://%s" % (host.scheme, host.netloc)
        if base not in self.robots:
            rp = urllib.robotparser.RobotFileParser(base + "/robots.txt")
            try:
                req = urllib.request.Request(base + "/robots.txt", headers={"User-Agent": AGENT})
                rp.parse(urllib.request.urlopen(req, timeout=20).read().decode("utf-8", "replace").splitlines())
            except urllib.error.HTTPError as e:
                rp.parse([] if 400 <= e.code < 500 else ["User-agent: *", "Disallow: /"])  # none: allowed; server error: wait
            except (urllib.error.URLError, OSError):
                rp.parse(["User-agent: *", "Disallow: /"])  # cannot read the rules: do not guess
            self.robots[base] = rp
        return self.robots[base].can_fetch(AGENT, url)

    def fetch(self, url, delay):
        host = urllib.parse.urlsplit(url).netloc
        if host in self.blocked:
            return "skipped (host blocked this run)", b""
        if not self.allowed(url):
            return "skipped (robots.txt)", b""
        wait = max(delay, self.floor) - (self.clock() - self.last.get(host, -1e9))
        if wait > 0:
            self.sleep(wait)
        self.last[host] = self.clock()
        try:
            body = urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": AGENT}), timeout=30).read()
        except urllib.error.HTTPError as e:
            if e.code in (403, 429):
                self.blocked.add(host)
            return "http %d" % e.code, b""
        except (urllib.error.URLError, OSError) as e:
            return "error %s" % e, b""
        if any(m in body[:20000].decode("utf-8", "replace").lower() for m in BLOCK_MARKERS):
            self.blocked.add(host)
            return "blocked (captcha or rate limit page)", b""
        why = bad_body(body)
        if why:
            return "bad body (%s)" % why, b""
        return "ok", body


def outcome(status):
    """'blocked', 'denied' or '' (a page came back, or a plain error to retry later) from a fetch status."""
    if status.startswith("skipped (robots"):
        return "denied"
    if status.startswith(("blocked", "skipped (host blocked", "http 403", "http 429")):
        return "blocked"
    return ""


def mark(queue, results):
    """Queue rows (id -> (result, note)) become done with that result; only rows still open are touched."""
    def fn(rows):
        for r in rows:
            if r["id"] in results and r["status"] == "open":
                r["status"], r["done_at"] = "done", Q.now_iso()
                r["result"], r["note"] = results[r["id"]][0], Q.clean(results[r["id"]][1])
        return rows, 0
    Q.with_queue(queue, fn)


def main(argv=None):
    p = argparse.ArgumentParser(prog="gwt-batch", description=__doc__.split("\n\n")[0])
    p.add_argument("--queue", default=os.environ.get("GWT_QUEUE", "cola.tsv"))
    p.add_argument("--templates", required=True)
    p.add_argument("--source")
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--fetch", action="store_true")
    p.add_argument("--out", default="raw")
    p.add_argument("--mark", action="store_true", help="record blocked/denied tasks in the queue (done, result blocked/denied)")
    p.add_argument("--delay", type=float, default=0.0, help="minimum seconds between requests to one host (never below the template's)")
    a = p.parse_args(argv)
    try:
        templates = load_templates(a.templates)
    except (OSError, ValueError, KeyError) as e:
        print("gwt-batch: %s" % e, file=sys.stderr)
        return 2
    todo = tasks(Q.read_file(a.queue), templates, a.source, a.limit)
    if not todo:
        print("gwt-batch: no open task has a template", file=sys.stderr)
        return 1
    polite = Polite(a.delay)
    if a.fetch:
        os.makedirs(a.out, exist_ok=True)
    log, manual, marks, failed = [], [], {}, 0
    for r, t, url in todo:
        if not a.fetch:
            print("%s\t%s\t%s" % (r["id"], r["source"], url))
            continue
        status, body = polite.fetch(url, t["delay"])
        if body:
            with open(os.path.join(a.out, "%s.html" % r["id"]), "wb") as fh:
                fh.write(body)
        print("%s\t%s\t%s\t%d bytes\t%s" % (r["id"], r["source"], status, len(body), url))
        res = outcome(status)
        log.append((r["id"], r["source"], status, res or ("ok" if body else "error"), url))
        if not res and not body:
            failed += 1
        if res:
            manual.append((r["id"], r["person_label"], r["source"], res, status, url))
            marks[r["id"]] = (res, "%s: %s (%s)" % (res, status, url))
    if a.fetch:
        for name, head, rows in (("outcomes.tsv", "id\tsource\tstatus\tresult\turl", log),
                                 ("manual.tsv", "id\tperson\tsource\tresult\tstatus\turl", manual)):
            with open(os.path.join(a.out, name), "w", encoding="utf-8") as fh:
                fh.write(head + "\n" + "".join("\t".join(x) + "\n" for x in rows))
        if manual:
            print("gwt-batch: %d task(s) need a person (captcha, block or robots): see %s" % (len(manual), os.path.join(a.out, "manual.tsv")), file=sys.stderr)
        if failed:
            print("gwt-batch: %d fetch(es) gave no usable page (error, bad body): they stay open, see outcomes.tsv" % failed, file=sys.stderr)
        if a.mark and marks:
            mark(a.queue, marks)
    return 0


if __name__ == "__main__":
    sys.exit(main())
