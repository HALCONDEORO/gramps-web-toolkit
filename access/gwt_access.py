#!/usr/bin/env python3
"""gwt-access: a map of how each archive can be read, so a lane does not rediscover it every time.

One TSV row per archive: archive, kind (departmental, municipal, parish, national), viewer (js, pdf, images,
index-only, none), captcha (yes, no, unknown), download (yes, no, unknown), index (name index online: yes, no,
unknown), writes (they answer a letter or mail: yes, no, unknown), url, checked (date), note, alias (other names for the
same archive, separated by |: the open data file names "archives departementales de X", a lane says "AD41").

  gwt-access --map access.tsv set "AD41" --kind departmental --viewer js --captcha no --download no --url https://...
  gwt-access --map access.tsv show AD41
  gwt-access --map access.tsv list [--captcha no] [--viewer js]
  gwt-access --map access.tsv plan "AD41,AD37,Boulogne"   # which can be read now, which need a letter, which are unknown
  gwt-access corpus ad-corpus.csv --place Orne --type "état civil" [--into]   # what a departement has put online

`corpus` reads the open data file "Principaux corpus d'archives numerises ... par les archives departementales"
(data.gouv.fr, Licence ouverte; columns intitule, institution, id_institution, url_site, type_document, lieu,
theme, periode, observations). It lists the matches; with --into it adds one row per archive that has none yet
(kind departmental, viewer/captcha/download unknown, the url and what is online in the note) and never changes
a row that is already in the map. It says what exists online, not how to read it: `set` that after looking once.

Exit 0 on success, 1 when show finds no row, 2 on a bad argument.
"""
import argparse
import csv
import datetime
import json
import io
from pathlib import Path
import os
import re
import sys

COLS = ("archive", "kind", "viewer", "captcha", "download", "index", "writes", "url", "checked", "note", "alias")
CHOICES = {"kind": ("departmental", "municipal", "parish", "national", "other"),
           "viewer": ("js", "pdf", "images", "index-only", "none", "unknown"),
           "captcha": ("yes", "no", "unknown"), "download": ("yes", "no", "unknown"),
           "index": ("yes", "no", "unknown"), "writes": ("yes", "no", "unknown")}

COVERAGE_COLS = ("archive", "parish", "record_type", "year_from", "year_to",
                 "status", "evidence_ref", "checked")
COVERAGE_STATUSES = ("online", "restricted", "not_digitised", "unknown")
COVERAGE_MIN_YEAR, COVERAGE_MAX_YEAR, COVERAGE_MAX_SPAN = 1000, 2100, 500


def coverage_year(value):
    """Canonical four ASCII digits, within the fixed coverage year domain."""
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}", value):
        raise ValueError("coverage years must be four ASCII digits")
    year = int(value)
    if not COVERAGE_MIN_YEAR <= year <= COVERAGE_MAX_YEAR:
        raise ValueError("coverage years must be between 1000 and 2100")
    return year


def coverage_query_bounds(parish, record_type, year_from, year_to):
    if any(not isinstance(v, str) or not v.strip() for v in (parish, record_type)):
        raise ValueError("coverage needs a nonempty parish and record type")
    if (type(year_from) is not int or type(year_to) is not int
            or not COVERAGE_MIN_YEAR <= year_from <= year_to <= COVERAGE_MAX_YEAR
            or year_to - year_from + 1 > COVERAGE_MAX_SPAN):
        raise ValueError("coverage needs ordered integer years 1000..2100, at most 500 inclusive years")


def coverage_assertion(row):
    if None in row or any(not isinstance(row[c], str) or not row[c].strip() for c in COVERAGE_COLS):
        raise ValueError("every coverage column must be nonempty; row must match header")
    lo, hi = coverage_year(row["year_from"]), coverage_year(row["year_to"])
    if lo > hi:
        raise ValueError("year_from must not exceed year_to")
    if row["status"] not in COVERAGE_STATUSES:
        raise ValueError("status must be online, restricted, not_digitised or unknown")
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", row["checked"]):
        raise ValueError("checked must be an ISO date YYYY-MM-DD")
    datetime.date.fromisoformat(row["checked"])
    return row | {"year_from": lo, "year_to": hi}


def coverage_rows(path):
    # Explicit local input, parsed as data only; no referenced file or URL is opened.
    with Path(path).open(encoding="utf-8", newline="") as stream:
        contents = stream.read()
    reader = csv.DictReader(io.StringIO(contents, newline=""), delimiter="\t", strict=True)
    if (reader.fieldnames is None or len(reader.fieldnames) != len(COVERAGE_COLS)
            or set(reader.fieldnames) != set(COVERAGE_COLS)):
        raise ValueError("coverage TSV needs exactly these columns: " + "/".join(COVERAGE_COLS))
    for row in reader:
        try:
            yield coverage_assertion(row)
        except ValueError as e:
            raise ValueError(f"coverage TSV line {reader.line_num}: {e}") from e


def coverage_status(statuses):
    if not statuses:
        return "unknown"
    return statuses[0] if len(statuses) == 1 else "conflict"


def coverage_report(path, parish, record_type, year_from, year_to):
    """Read explicit local assertions; never derive availability or select a latest source."""
    coverage_query_bounds(parish, record_type, year_from, year_to)
    sources = set()
    for row in coverage_rows(path):
        lo, hi = row["year_from"], row["year_to"]
        if (row["parish"] == parish and row["record_type"] == record_type
                and lo <= year_to and hi >= year_from):
            sources.add(tuple(row[c] for c in COVERAGE_COLS))
    assertions = [dict(zip(COVERAGE_COLS, source)) for source in sorted(sources)]
    years = []
    for year in range(year_from, year_to + 1):
        matches = [r for r in assertions if r["year_from"] <= year <= r["year_to"]]
        statuses = sorted({r["status"] for r in matches})
        years.append({"year": year, "status": coverage_status(statuses),
                      "statuses": statuses, "sources": matches})
    return {"schema_version": 1, "parish": parish, "record_type": record_type,
            "year_from": year_from, "year_to": year_to, "years": years}


def load(path):
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8", newline="") as fh:
        return {r["archive"].strip().lower(): r for r in csv.DictReader(fh, delimiter="\t")}


def find(rows, name):
    """The row for a name: its own archive name, or one of its aliases (names separated by |)."""
    key = " ".join((name or "").lower().split())
    if key in rows:
        return rows[key]
    for r in rows.values():
        if key in [" ".join(x.lower().split()) for x in (r.get("alias") or "").split("|") if x.strip()]:
            return r
    return None


def save(path, rows):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, COLS, delimiter="\t", extrasaction="ignore")
        w.writeheader()
        for k in sorted(rows):
            w.writerow(rows[k])
    os.replace(tmp, path)


def readable_now(r):
    """Readable without a human: an image or pdf viewer or a download, and no captcha."""
    return r.get("captcha") == "no" and (r.get("download") == "yes" or r.get("viewer") in ("pdf", "images", "js"))


def corpus(a):
    try:
        with open(a.csv, encoding="utf-8-sig", newline="") as fh:
            found = list(csv.DictReader(fh))
    except OSError as e:
        print(f"gwt-access: {e}", file=sys.stderr)
        return 2
    if not found or "type_document" not in found[0] or "institution" not in found[0]:
        print("gwt-access: not the corpus file (needs institution and type_document columns)", file=sys.stderr)
        return 2
    fold = lambda t: " ".join((t or "").lower().split())
    hits = [r for r in found if (not a.place or fold(a.place) == fold(r.get("lieu")))
            and (not a.doctype or fold(a.doctype) in fold(r.get("type_document")))]
    for r in hits:
        print("\t".join((r["institution"], r["lieu"], r["type_document"], r["periode"].replace("|", ", "), r["url_site"])))
    if not hits:
        return 1
    if a.into:
        rows = load(a.map)
        by_arch = {}
        for r in hits:
            by_arch.setdefault(r["institution"].strip(), []).append(r)
        added = 0
        for name, rs in sorted(by_arch.items()):
            if find(rows, name):
                continue
            what = "; ".join("%s (%s)" % (x["type_document"], x["periode"].replace("|", ", ")) for x in rs)
            rows[name.lower()] = {c: "unknown" for c in COLS} | {"alias": "",
                "archive": name, "kind": "departmental", "url": rs[0]["url_site"], "checked": datetime.date.today().isoformat(),
                "note": "en linea segun data.gouv: " + what}
            added += 1
        save(a.map, rows)
        print("gwt-access: %d archive(s) added to %s" % (added, a.map), file=sys.stderr)
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="gwt-access", description=__doc__.split("\n\n")[0])
    p.add_argument("--map", default=os.environ.get("GWT_ACCESS", "access.tsv"))
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("set")
    s.add_argument("archive")
    for k in CHOICES:
        s.add_argument("--" + k, choices=CHOICES[k])
    s.add_argument("--url", default=None)
    s.add_argument("--note", default=None)
    s.add_argument("--alias", default=None, help="another name for the same archive; several with |")
    sh = sub.add_parser("show")
    sh.add_argument("archive")
    ls = sub.add_parser("list")
    for k in ("captcha", "viewer", "download", "writes", "index"):
        ls.add_argument("--" + k, choices=CHOICES[k])
    pl = sub.add_parser("plan")
    pl.add_argument("archives", help="comma-separated archive names")
    co = sub.add_parser("corpus", help="what departmental archives have put online, from the data.gouv open data file")
    co.add_argument("csv")
    co.add_argument("--place", help="departement, as the file spells it (e.g. Orne)")
    co.add_argument("--type", dest="doctype", help="document type (e.g. 'état civil', 'matricules militaires')")
    co.add_argument("--into", action="store_true", help="add a row to the map for each archive that has none")
    cv = sub.add_parser("coverage", help="report supplied parish/year access assertions from a local TSV")
    cv.add_argument("tsv", help="explicit local coverage TSV (read only)")
    cv.add_argument("--parish", required=True, help="exact parish text")
    cv.add_argument("--type", dest="record_type", required=True, help="exact record_type text")
    cv.add_argument("--year-from", required=True, type=coverage_year)
    cv.add_argument("--year-to", required=True, type=coverage_year)
    a = p.parse_args(argv)
    if a.cmd == "coverage":
        try:
            report = coverage_report(a.tsv, a.parish, a.record_type, a.year_from, a.year_to)
        except (OSError, ValueError, csv.Error) as e:
            print(f"gwt-access: {e}", file=sys.stderr)
            return 2
        print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2))
        return 0
    if a.cmd == "corpus":
        return corpus(a)
    rows = load(a.map)
    if a.cmd == "set":
        key = a.archive.strip().lower()
        if not key:
            p.error("archive is empty")
        r = rows.setdefault(key, {c: "unknown" for c in COLS} | {"archive": a.archive.strip(), "url": "", "note": "", "alias": ""})
        for k in list(CHOICES) + ["url", "note", "alias"]:
            if getattr(a, k) is not None:
                r[k] = " ".join(str(getattr(a, k)).split())  # no tabs or newlines in a TSV cell
        r["checked"] = datetime.date.today().isoformat()
        save(a.map, rows)
        print("\t".join(r.get(c, "") for c in COLS))
        return 0
    if a.cmd == "show":
        r = find(rows, a.archive)
        if not r:
            print("no row for %s" % a.archive, file=sys.stderr)
            return 1
        for c in COLS:
            print("%s: %s" % (c, r.get(c, "")))
        return 0
    if a.cmd == "list":
        for k in sorted(rows):
            r = rows[k]
            if all(getattr(a, f) in (None, r.get(f)) for f in ("captcha", "viewer", "download", "writes", "index")):
                print("\t".join(r.get(c, "") for c in COLS[:8]))
        return 0
    names = [n.strip() for n in a.archives.split(",") if n.strip()]
    if not names:
        p.error("plan needs at least one archive")
    for n in names:
        r = find(rows, n)
        if not r:
            verdict = "unknown: look at the site once and `set` it"
        elif readable_now(r):
            verdict = "read now (%s viewer, no captcha)" % r.get("viewer")
        elif r.get("captcha") == "unknown" and r.get("viewer") not in ("none", "index-only"):
            verdict = "check: captcha not recorded, open the site once and `set --captcha`"
        elif r.get("writes") == "yes":
            verdict = "write for the act (captcha or no viewer)"
        else:
            verdict = "blocked: captcha or no route; needs a person"
        print("%s\t%s" % (n, verdict))
    return 0


if __name__ == "__main__":
    sys.exit(main())
