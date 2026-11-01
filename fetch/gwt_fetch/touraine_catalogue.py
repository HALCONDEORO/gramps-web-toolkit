"""Conservative catalogue of search evidence, not a claim that archive records exist or are lost."""
from __future__ import annotations

import re
import sys
import unicodedata
from html.parser import HTMLParser
from urllib.parse import urlencode

import requests

from . import touraine
from .coverage import ranges

MAX_PAGES = 100
COLUMNS = ["year", "cote", "ark", "date", "place", "kind", "medias", "title",
           "conflict", "query", "record_url", "source_url", "source_page", "source_row"]


class _PageEvidence(HTMLParser):
    """Read HTML attributes as tokens and heading text across nested inline tags."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.next_page = False
        self.heading = None
        self.headings = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if "next" in (attrs.get("rel") or "").lower().split():
            self.next_page = True
        if tag in ("h1", "title"):
            self.heading = []

    def handle_data(self, data):
        if self.heading is not None:
            self.heading.append(data)

    def handle_endtag(self, tag):
        if tag in ("h1", "title") and self.heading is not None:
            self.headings.append(" ".join("".join(self.heading).lower().split()))
            self.heading = None


def _place_identity(place: str) -> str | None:
    """Exact Unicode/case/whitespace identity; never guess aliases or split a list."""
    identity = " ".join(unicodedata.normalize("NFC", place).casefold().split())
    if not identity or identity in {"-", "unknown", "inconnu"}:
        return None
    if re.search(r"[,;/|]|\b(?:et|and|ou|or)\b", identity):
        return None
    return identity


def years(date: str) -> range | None:
    """Only unqualified ASCII years or inclusive year ranges are evidence."""
    match = re.fullmatch(r"([0-9]{4})(?:\s*[-–]\s*([0-9]{4}))?", date.strip())
    if not match:
        return None
    first, last = int(match[1]), int(match[2] or match[1])
    return range(first, last + 1) if 1 <= first <= last <= 9999 else None


def _record_blocks(blocks):
    """Account for every header, record and optional context row."""
    records = []
    previous_record = False
    for attrs, block in blocks:
        attr_parser = _RowAttributes()
        attr_parser.feed("<tr " + attrs + ">")
        classes = attr_parser.classes
        header = bool(re.search(r'<th\b', block, re.I)) and not re.search(r'<td\b', block, re.I)
        if classes == {"result", "record"}:
            records.append(block)
            previous_record = True
        elif classes == {"context"} and previous_record:
            # The site's separate context row belongs to the preceding record.
            previous_record = False
        elif header:
            previous_record = False
        else:
            raise ValueError("unaccounted non-header results row")
    return records


def _checked_rows(page: str, per_page: int) -> list[dict]:
    """A missing/unreadable table is not a zero-result page."""
    signals = _PageEvidence()
    signals.feed(page)
    signals.close()
    if any("verify you are human" in heading for heading in signals.headings):
        raise touraine.Blocked("human verification challenge")
    tables = re.findall(r'<table\b[^>]*class=[\'"][^\'"]*\bresults-table\b[^\'"]*[\'"][^>]*>(.*?)</table>',
                        page, re.S)
    if len(tables) != 1:
        raise ValueError("unrecognised results table")
    blocks = re.findall(r'<tr\b([^>]*)>(.*?)</tr\s*>', tables[0], re.S | re.I)
    if len(blocks) != len(re.findall(r'<tr\b', tables[0], re.I)):
        raise ValueError("unclosed results row")
    records = _record_blocks(blocks)
    expected = len(records)
    rows = touraine.parse_table(tables[0])
    if len(rows) != expected or len(rows) > per_page:
        raise ValueError("unparsed or oversized results table")
    if not rows and signals.next_page:
        raise ValueError("empty table still advertises a next page")
    _mark_place_scope(rows, records)
    return rows


def _mark_place_scope(rows, records):
    for row, block in zip(rows, records):
        places = re.findall(r'<span class="label">Lieu</span>(.*?)</td>', block, re.S)
        row["place_scope"] = (len(places) == 1 and block.count('<span class="label">Lieu</span>') == 1
                              and not re.search(r'<(?:br|li)\b', places[0], re.I)
                              and len(re.findall(r'<a\b', places[0], re.I)) <= 1)


class _RowAttributes(HTMLParser):
    classes: set[str]

    def handle_starttag(self, tag, attrs):
        self.classes = set((dict(attrs).get("class") or "").split())


def _provenance(rows: list[dict], query: str, page: int, per_page: int) -> None:
    params = {"q": query, "scope": "archival", "mode": "table", "sort": "referencecode_asc",
              "resultsPerPage": per_page, "page": page}
    source = touraine.BASE + "/search/results?" + urlencode(params)
    for n, row in enumerate(rows, 1):
        row.update(query=query, source_page=page, source_row=n, source_url=source,
                   record_url=f'{touraine.BASE}/ark:/{touraine.NAAN}/{row["ark"]}')


def _mark_conflicts(rows: list[dict]) -> None:
    by_cote: dict[str, set[str]] = {}
    by_ark: dict[str, set[tuple]] = {}
    for row in rows:
        by_cote.setdefault(row["cote"], set()).add(row["ark"])
        by_ark.setdefault(row["ark"], set()).add(tuple(row[k] for k in touraine.TABLE_COLUMNS))
    for row in rows:
        conflict = len(by_cote[row["cote"]]) > 1 or len(by_ark[row["ark"]]) > 1
        row["conflict"] = "yes" if conflict else "no"


def collect(client, query: str, per_page: int = 20, max_pages: int = MAX_PAGES) -> dict:
    """Keep partial evidence, and stop at the first refusal, error, repeat or page ceiling."""
    if not query.strip() or per_page not in touraine.TABLE_SIZES or not 1 <= max_pages <= MAX_PAGES:
        raise ValueError("give a nonblank place, --per-page 20/40/80 and --max-pages 1 to 100")
    result = {"rows": [], "complete": False, "reason": "page limit reached"}
    seen = set()
    for page in range(1, max_pages + 1):
        try:
            text = touraine.table_page(client, query, page, per_page)
            rows = _checked_rows(text, per_page)
        except (touraine.Blocked, requests.RequestException, ValueError) as exc:
            result["reason"] = f"refused or unreadable page {page}: {exc}"
            break
        if not rows:
            # No repository capture establishes the full legitimate terminal layout
            # and a site-owned terminal/no-results marker. Table emptiness and
            # absence of known challenge text cannot establish completion.
            result["reason"] = (f"completeness UNKNOWN on page {page}: no captured positive "
                                "terminal layout and site-owned marker contract")
            break
        signature = tuple(tuple(row[k] for k in touraine.TABLE_COLUMNS) for row in rows)
        _provenance(rows, query, page, per_page)
        result["rows"].extend(rows)
        if signature in seen:
            result["reason"] = f"repeated results on page {page}"
            break
        seen.add(signature)
    _mark_conflicts(result["rows"])
    return result


def year_coverage(result: dict) -> dict:
    """Gaps apply only within the dated span of a complete, unambiguous result set."""
    report = {"complete": False, "span": "", "uncovered": None}
    rows = result["rows"]
    if not result["complete"] or not rows:
        return report
    if any(years(row["date"]) is None or row["conflict"] == "yes" or not row["cote"] for row in rows):
        return report
    places = {_place_identity(row["place"]) for row in rows}
    if None in places or len(places) != 1 or not all(row.get("place_scope", False) for row in rows):
        return report
    covered = {year for row in rows for year in years(row["date"])}
    first, last = min(covered), max(covered)
    missing = sorted(set(range(first, last + 1)) - covered)
    return {"complete": True, "span": str(first) if first == last else f"{first}-{last}",
            "uncovered": ranges(missing)}


def _print_rows(rows: list[dict]) -> None:
    expanded = [{**row, "year": year} for row in rows for year in (years(row["date"]) or ["unknown"])]
    expanded.sort(key=lambda row: (10000 if row["year"] == "unknown" else row["year"],
                                   row["cote"], row["ark"], row["source_page"], row["source_row"]))
    print("\t".join(COLUMNS))
    for row in expanded:
        print("\t".join(touraine.clean_cell(str(row[key])) for key in COLUMNS))


def run(args, client) -> int:
    client.delay = max(getattr(client, "delay", 0.0), 1.0)
    client.retries = 0
    try:
        result = collect(client, args.query, args.per_page, args.max_pages)
    except ValueError as exc:
        print(f"gwt-fetch: {exc}", file=sys.stderr)
        return 2
    _print_rows(result["rows"])
    report = year_coverage(result)
    status = "complete" if result["complete"] else "incomplete"
    coverage = "complete" if report["complete"] else "incomplete"
    detail = (f'; span={report["span"]}; uncovered={report["uncovered"] or "none"}'
              if report["complete"] else "; unknown dates, missing cotes, conflicts, unverified place scope or incomplete pagination prevent gap claims")
    print(f'gwt-fetch: pagination={status}; completeness={"known" if result["complete"] else "UNKNOWN"}; coverage={coverage}; {result["reason"]}{detail}', file=sys.stderr)
    return 0 if report["complete"] else 1
