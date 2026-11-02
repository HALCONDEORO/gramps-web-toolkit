# Parish/year access coverage

`gwt-access coverage` reports access assertions supplied in an explicit local TSV.
It reads that file only, does not load the archive access map, fetch references,
contact services, create a missing input, or write a report file. The existing
`--map`, `set`, `show`, `list`, `plan`, and `corpus` behavior is unchanged.

```sh
python3 -B access/gwt_access.py coverage supplied.tsv \
  --parish 'Saint Test' --type baptisms --year-from 1799 --year-to 1803
```

The TSV must have exactly these eight unique column names, in any order. Every
cell must be present and contain non-whitespace text. UTF-8 and standard TSV
quoting are supported. A header-only file is valid and produces unknown years.

| Column | Meaning |
| --- | --- |
| `archive` | Archive/source identity, preserved verbatim. |
| `parish` | Exact parish name. |
| `record_type` | Exact record type, such as `baptisms`. |
| `year_from` | First included year, four ASCII digits, 1000–2100. |
| `year_to` | Last included year, four ASCII digits, 1000–2100; must be at least `year_from`. |
| `status` | Exactly `online`, `restricted`, `not_digitised`, or `unknown`. |
| `evidence_ref` | Explicit nonempty source reference: e.g. a local inventory identifier, citation, or URL. Opaque text; never opened or fetched. |
| `checked` | Supplied valid calendar date, exactly `YYYY-MM-DD`. Preserved without freshness ranking. |

Synthetic example (the separators below are literal tabs):

```tsv
archive	parish	record_type	year_from	year_to	status	evidence_ref	checked
Archive A	Saint Test	baptisms	1800	1801	online	local:inventory/7	2020-02-29
Archive B	Saint Test	baptisms	1801	1802	restricted	local:catalogue/9	2025-01-01
Archive A	Saint Test	baptisms	1803	1803	not_digitised	local:inventory/10	2020-02-29
```

Parish and record type match literally, including case, accents, and whitespace.
There is no alias, substring, geographic, or record-type expansion. Year bounds
are inclusive. Queries require both bounds, within 1000–2100, and a maximum of
500 included years. This fixed domain bounds parish-register queries; it does
not claim coverage outside that domain. Source ranges use the same year domain
and may exceed the query span; they are preserved rather than clipped.

The command emits one JSON object on stdout, with sorted keys, two-space
indentation, UTF-8 text and a final newline. Schema version 1 has `schema_version`,
`parish`, `record_type`, `year_from`, `year_to`, and `years`. Each ascending year
entry contains `year`, `status`, `statuses`, and `sources`:

- No matching assertions: `status: "unknown"`, `statuses: []`, `sources: []`.
- One distinct supplied status: that status, with every supporting assertion.
- Multiple distinct supplied statuses: `status: "conflict"`, with all distinct
  statuses sorted lexically and every assertion retained. An explicit `unknown`
  alongside `online` also conflicts. A later checked date never overrides another
  assertion, even from the same archive.

Each source contains all eight input fields, with year bounds represented as
integers. Sources sort lexically by the documented column sequence, using integer
years. Only identical eight-field assertions are deduplicated. Input order,
column order and repeated identical rows do not affect the report. Distinct
archives, references, dates, ranges or statuses remain distinct, even when the
overall status agrees.

The example yields unknown for 1799, online for 1800, conflict for 1801,
restricted for 1802, and not_digitised for 1803. These are supplied assertions
with their checked dates and provenance, **not automatic claims of current
availability**. Missing entries, viewer settings, CAPTCHA, free text, image
search failures and “not found” results never establish an access status.
Image readability or image-reading negatives are outside this feature.

Success exits 0, including all-unknown results. Missing/unreadable input,
malformed UTF-8/TSV, missing/duplicate/extra columns, incomplete rows, invalid
statuses/dates/ranges and invalid queries exit 2 with a diagnostic on stderr
and no partial JSON. Every row is validated, including rows outside the query.
The Python function `coverage_report(path, parish, record_type, year_from,
year_to)` returns the same object; bounds must be actual integers (not booleans,
floats or numeric strings). It raises validation/read errors for callers to handle.

Local synthetic regressions, including subprocess CLI execution:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest discover -s access -p 'test*.py'
PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest discover -s access -p test_gwt_access.py -k coverage
```

Validation on 2026-11-02: before implementation, the new regressions produced
4 failures and 1 error against the original CLI; all 6 legacy tests passed.
After implementation, all 12 tests passed (6 legacy, 6 coverage), as did the
6-test focused run. Ten in-memory mutants were detected: exclusive range end,
case-folded parish matching, ignored record type, hidden conflicts, online gaps,
discarded provenance, retained duplicates, disabled span limit, invalid calendar
dates, and empty evidence. No mutant files were written. The legacy tests still
emit their existing unclosed-file ResourceWarnings.
