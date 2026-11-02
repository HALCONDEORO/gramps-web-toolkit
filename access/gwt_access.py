"""Read-only reports of explicitly supplied parish/year access assertions."""
import argparse
import csv
import datetime
import io
import json
from pathlib import Path
import re
import sys

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



def main(argv=None):
    parser = argparse.ArgumentParser(prog="gwt-access")
    sub = parser.add_subparsers(dest="cmd", required=True)
    report = sub.add_parser("coverage")
    report.add_argument("tsv")
    report.add_argument("--parish", required=True)
    report.add_argument("--type", dest="record_type", required=True)
    report.add_argument("--year-from", required=True, type=coverage_year)
    report.add_argument("--year-to", required=True, type=coverage_year)
    args = parser.parse_args(argv)
    try:
        value = coverage_report(args.tsv, args.parish, args.record_type, args.year_from, args.year_to)
    except (OSError, ValueError, csv.Error) as exc:
        print(f"gwt-access: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
