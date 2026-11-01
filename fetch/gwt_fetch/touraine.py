"""Archives d'Indre-et-Loire (archives.touraine.fr): search registers, list the pages of one, download a few.

Read only, one request at a time. It works for the digitised civil registers (cotes 6NUM). The E-depot
fonds (EDEP) answer "Group with index 0 not found" and need a real browser: this module says so and stops.
It selects at most 30 images per run (`--max`, never above 30), and it stops the whole run, not just one page,
on any refusal: a redirect, a 4xx or 5xx status, a page that is not an image or the expected JSON.
"""
from __future__ import annotations

import html
import json
import re
import sys

from .store import Store

BASE = "https://archives.touraine.fr"
NAAN = "37621"
BATCH = 100  # the visualizer returns at most this many pages per request
ARK = re.compile(r"[A-Za-z0-9]{6,40}")
UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
MAX_IMAGES = 30
END_OF_REGISTER = "Invalid request: group index or ArkName or start/end boundaries values are not valid"
NOT_EDEPOT = "Group with index 0 not found"


class Blocked(Exception):
    """The site refused, redirected or answered with something else than expected: stop, never go around it."""


class Edepot(Exception):
    """The register is on the E-depot viewer, which the visualizer API does not serve."""


def clean_cell(text: str) -> str:
    """One TSV cell: no tab, newline or leading spreadsheet-formula character."""
    out = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", text or ""))).strip()
    return "'" + out if out[:1] in ("=", "+", "-", "@") else out


def _field(block: str, pattern: str) -> str:
    m = re.search(pattern, block, re.S)
    return clean_cell(m.group(1)) if m else ""


def parse_results(page: str) -> list[dict]:
    """The registers on one results page: ark, title, dates, cote."""
    rows = []
    link_pattern = rf'href="(?:https://archives\.touraine\.fr)?/?ark:/{NAAN}/([A-Za-z0-9]+)[^"]*"'
    for block in re.findall(r'<li class="element-list">(.*?)</li>', page, re.S):
        link = re.search(link_pattern, block)
        if not link:
            continue
        rows.append({
            "ark": link.group(1),
            "title": _field(block, r"<h2>\s*<span>(.*?)</span>"),
            "date": _field(block, r"<h3>Date</h3>\s*<p><span>(.*?)</span>"),
            "cote": _field(block, r'<p class="referenceCodes">(.*?)</p>'),
        })
    return rows


TABLE_SIZES = (20, 40, 80)  # the results per page the site offers; any other value is answered with a redirect
TABLE_COLUMNS = ["ark", "cote", "date", "place", "kind", "medias", "title"]


def _cell(block: str, label: str) -> str:
    """The text of the cell that carries `label`: a div with the text, or the bare text after the label."""
    text = _field(block, rf'<span class="label">{label}</span>(.*?)</td>')
    return "" if text == "'-" else text  # the site writes "-" for none, which clean_cell would make a formula


def parse_table(page: str) -> list[dict]:
    """The rows of a results page in table mode: ark, cote, date, place, kind, number of media, title.

    The cote and the ark stand in the same row, so a whole commune can be listed without opening each record.
    """
    rows = []
    link_pattern = rf'href="(?:https://archives\.touraine\.fr)?/?ark:/{NAAN}/([A-Za-z0-9]+)[^"]*"'
    for block in re.findall(r'<tr class="result record">(.*?)</tr>', page, re.S):
        link = re.search(link_pattern, block)
        if not link:
            continue
        cote = re.sub(r"\s*\(Cote\)$", "", _cell(block, "Cote"))
        medias = re.search(r"(\d+)\s+medias?", block)
        rows.append({
            "ark": link.group(1),
            "cote": cote,
            "date": _cell(block, "Date"),
            "place": _cell(block, "Lieu"),
            "kind": _cell(block, "Type de document"),
            "medias": medias.group(1) if medias else "",
            "title": _field(block, r"<h2>\s*<span>(.*?)</span>"),
        })
    return rows


def search_table(client, query: str, page: int = 1, per_page: int = 20) -> list[dict]:
    """One results page in table mode, sorted by cote, so the registers of a place come in order."""
    params = {"q": query, "scope": "archival", "mode": "table", "sort": "referencecode_asc",
              "resultsPerPage": per_page, "page": page}
    r = _get(client, BASE + "/search/results", params=params)
    _ok(r, BASE + "/search/results", ("text/html",))
    return parse_table(r.text)


def _get(client, url: str, **kw):
    """One GET that follows no redirect, so every request goes through the client's pacing."""
    return client.get(url, allow_redirects=False, **kw)


def _ok(r, url: str, kinds: tuple = ("text/", "application/json")) -> None:
    ctype = (r.headers.get("Content-Type", "") or "").lower()
    if r.status_code != 200 or not ctype.startswith(kinds):
        raise Blocked(f"HTTP {r.status_code} {ctype or 'no content type'} from {url}")


def search(client, query: str, start: int = 0, per_page: int = 80) -> list[dict]:
    params = {"q": query, "scope": "archival", "resultsPerPage": per_page}
    if start:
        params["start"] = start
    r = _get(client, BASE + "/search/results", params=params)
    _ok(r, BASE + "/search/results", ("text/html",))
    return parse_results(r.text)


def _batch(client, ark: str, start: int, end: int):
    """Pages start..end as {number: url}, or None when the range runs past the end of the register."""
    url = f"{BASE}/visualizer/api"
    r = _get(client, url, params={"naan": NAAN, "arkName": ark, "start": start, "end": end, "group": 0})
    text = r.text.strip()
    if r.status_code == 400:  # the site's own two answers: past the end of the register, and an E-depot fonds
        try:
            message = json.loads(text)
        except ValueError:
            message = None
        if message == NOT_EDEPOT:
            raise Edepot(ark)
        if message == END_OF_REGISTER:
            return None
        raise Blocked(f"HTTP 400 with an unknown message from {url}")
    _ok(r, url)
    try:
        data = json.loads(text)
    except ValueError:
        raise Blocked(f"not JSON from {url}") from None
    if not isinstance(data, dict):
        raise Blocked(f"unexpected JSON from {url}")
    return {int(k): v["url"] for k, v in data.items() if k.isdigit() and isinstance(v, dict) and "url" in v}


def list_pages(client, ark: str) -> dict[int, str]:
    """Every page of a register, found by batches of 100; the last batch is found by bisection."""
    pages: dict[int, str] = {}
    start = 1
    while True:
        got = _batch(client, ark, start, start + BATCH - 1)
        if got is None:
            lo, hi = start - 1, start + BATCH - 1  # lo is known valid, hi is known invalid
            while hi - lo > 1:
                mid = (lo + hi) // 2
                if _batch(client, ark, start, mid) is None:
                    hi = mid
                else:
                    lo = mid
            got = _batch(client, ark, start, lo) if lo >= start else {}
        pages.update(got)
        if len(got) < BATCH:
            return pages
        start += BATCH


def uuid_of(url: str) -> str:
    u = url.rstrip("/").rsplit("/", 1)[-1]
    if not UUID.fullmatch(u):
        raise ValueError(f"not a page uuid: {u!r}")
    return u


def parse_range(spec: str | None, total: int) -> list[int]:
    """1-based page numbers: 1-5,9,12- (open end = last page). Empty or None means all."""
    if not spec:
        return list(range(1, total + 1))
    out: set[int] = set()
    for part in spec.split(","):
        m = re.fullmatch(r"(\d*)-(\d*)|(\d+)", part.strip())
        if not m or not part.strip().strip("-"):
            raise ValueError(f"bad page range: {part!r}")
        if m.group(3):
            a = b = int(m.group(3))
        else:
            a, b = int(m.group(1) or 1), int(m.group(2) or total)
        out.update(range(a, b + 1))
    return sorted(n for n in out if 1 <= n <= total)


def problem(args) -> str:
    """What is wrong with the options of a search, or an empty string; checked before any request."""
    if getattr(args, "action", "") != "search":
        return ""
    table, page, per_page = (getattr(args, k, d) for k, d in (("table", False), ("page", 1), ("per_page", 20)))
    if table and page < 1:
        return "--page counts from 1"
    if not table and (page != 1 or per_page != 20):
        return "--page and --per-page belong to --table"
    if table and (getattr(args, "start", 0) or getattr(args, "limit", 80) != 80):
        return "--start and --limit belong to the list mode; --table pages with --page"
    return ""


def run(args, client) -> int:
    client.delay = max(getattr(client, "delay", 0.0), 1.0)  # one request per second at most, whatever --delay says
    client.retries = 0  # a refusal is not retried
    bad = problem(args)
    if bad:
        print(f"gwt-fetch: {bad}", file=sys.stderr)
        return 2
    try:
        if args.action == "search":
            return _run_search(args, client)
        return _run_pages(args, client)
    except Blocked as e:
        print(f"gwt-fetch: stopped, the site did not answer as expected ({e}); nothing further was requested", file=sys.stderr)
        return 1


def _run_table(args, client) -> int:
    rows = search_table(client, args.query, args.page, args.per_page)
    print("\t".join(TABLE_COLUMNS))
    for r in rows:
        print("\t".join(r[k] for k in TABLE_COLUMNS))
    print(f"gwt-fetch: {len(rows)} row(s) on page {args.page}; the next page is --page {args.page + 1}", file=sys.stderr)
    return 0


def _run_search(args, client) -> int:
    if getattr(args, "table", False):
        return _run_table(args, client)
    rows = search(client, args.query, args.start)
    print("ark\ttitle\tdate\tcote")
    for r in rows[: args.limit]:
        print("\t".join(r[k] for k in ("ark", "title", "date", "cote")))
    return 0


def _run_pages(args, client) -> int:
    if not ARK.fullmatch(args.ark):
        print(f"gwt-fetch: {args.ark!r} is not an ark name", file=sys.stderr)
        return 2
    try:
        pages = list_pages(client, args.ark)
    except Edepot:
        print("gwt-fetch: this register is on the E-depot viewer, which needs a browser; not supported", file=sys.stderr)
        return 1
    if not pages:
        print(f"gwt-fetch: no pages for {args.ark}", file=sys.stderr)
        return 1
    chosen = [n for n in parse_range(args.pages, max(pages)) if n in pages]
    print(f"gwt-fetch: {args.ark}: {len(pages)} page(s), {len(chosen)} selected", file=sys.stderr)
    if args.list:
        for n in chosen:
            print(f"{n}\t{pages[n]}\t{uuid_of(pages[n])}")
        return 0
    if len(chosen) > min(args.max, MAX_IMAGES):
        print(f"gwt-fetch: {len(chosen)} images selected, more than --max {args.max}; "
              "narrow --pages (this tool does not download whole registers)", file=sys.stderr)
        return 2
    return _download(args, client, pages, chosen)


def _download(args, client, pages, chosen) -> int:
    store = Store(args.out)
    manifest_url = f"{BASE}/ark:/{NAAN}/{args.ark}"
    for n in chosen:
        image_url = f"{BASE}/images/{uuid_of(pages[n])}.jpg"
        stem = f"{args.ark}_p{n:04d}"
        if store.done(stem, f"page {n}", image_url, manifest_url):
            continue
        r = _get(client, image_url, stream=True)
        ctype = r.headers.get("Content-Type", "")
        if r.status_code != 200 or not ctype.startswith("image/"):
            r.close()
            raise Blocked(f"page {n}: HTTP {r.status_code} {ctype or 'no content type'} from {image_url}")
        print(store.write(stem, r.iter_content(1 << 16), ctype, f"page {n}", image_url, manifest_url), file=sys.stderr)
    return 0
