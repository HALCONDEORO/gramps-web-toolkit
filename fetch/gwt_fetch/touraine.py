"""Archives d'Indre-et-Loire (archives.touraine.fr): search registers, list the pages of one, download a few.

Read only, one request at a time. It works for the digitised civil registers (cotes 6NUM). The E-depot
fonds (EDEP) answer "Group with index 0 not found" and need a real browser: this module says so and stops.
It never downloads a whole register: `pages` refuses more than --max images (default 30).
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


class Edepot(Exception):
    """The register is on the E-depot viewer, which the visualizer API does not serve."""


def clean_cell(text: str) -> str:
    """One TSV cell: no tab, newline or leading spreadsheet-formula character."""
    out = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", text or ""))).strip()
    return "'" + out if out[:1] in ("=", "+", "-", "@") else out


def parse_results(page: str) -> list[dict]:
    """The registers on one results page: ark, title, dates, cote, context."""
    rows = []
    for block in re.findall(r'<li class="element-list">(.*?)</li>', page, re.S):
        link = re.search(r'href="(?:https://archives\.touraine\.fr)?/?ark:/%s/([A-Za-z0-9]+)[^"]*"' % NAAN, block)
        if not link:
            continue

        def field(pattern):
            m = re.search(pattern, block, re.S)
            return clean_cell(m.group(1)) if m else ""

        rows.append({
            "ark": link.group(1),
            "title": field(r"<h2>\s*<span>(.*?)</span>"),
            "date": field(r"<h3>Date</h3>\s*<p><span>(.*?)</span>"),
            "cote": field(r'<p class="referenceCodes">(.*?)</p>'),
        })
    return rows


def search(client, query: str, start: int = 0, per_page: int = 80) -> list[dict]:
    params = {"q": query, "scope": "archival", "resultsPerPage": per_page}
    if start:
        params["start"] = start
    r = client.get(BASE + "/search/results", params=params)
    r.raise_for_status()
    return parse_results(r.text)


def _batch(client, ark: str, start: int, end: int):
    """Pages start..end as {number: url}, or None when the range runs past the end of the register."""
    r = client.get(f"{BASE}/visualizer/api", params={"naan": NAAN, "arkName": ark, "start": start,
                                                      "end": end, "group": 0})
    text = r.text.strip()
    if "Group with index 0 not found" in text:
        raise Edepot(ark)
    if text.startswith('"'):  # the site answers HTTP 400 with an error serialized as a JSON string
        return None
    r.raise_for_status()
    data = json.loads(text)
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


def run(args, client) -> int:
    client.delay = max(getattr(client, "delay", 0.0), 1.0)  # one request per second at most, whatever --delay says
    if args.action == "search":
        rows = search(client, args.query, args.start)
        print("ark\ttitle\tdate\tcote")
        for r in rows[: args.limit]:
            print("\t".join(r[k] for k in ("ark", "title", "date", "cote")))
        return 0
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
    if len(chosen) > args.max:
        print(f"gwt-fetch: {len(chosen)} images selected, more than --max {args.max}; "
              "narrow --pages (this tool does not download whole registers)", file=sys.stderr)
        return 2
    store = Store(args.out)
    manifest_url = f"{BASE}/ark:/{NAAN}/{args.ark}"
    failed = 0
    for n in chosen:
        u = uuid_of(pages[n])
        stem = f"{args.ark}_p{n:04d}"
        image_url = f"{BASE}/images/{u}.jpg"
        if store.done(stem, f"page {n}", image_url, manifest_url):
            continue
        r = client.get(image_url, stream=True)
        ctype = r.headers.get("Content-Type", "")
        if r.ok and ctype.startswith("image/"):
            print(store.write(stem, r.iter_content(1 << 16), ctype, f"page {n}", image_url, manifest_url), file=sys.stderr)
        else:
            failed += 1
            print(f"gwt-fetch: page {n} failed: {image_url} -> {r.status_code} {ctype}", file=sys.stderr)
            r.close()
    return 1 if failed else 0
