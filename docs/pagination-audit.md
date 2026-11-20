# Pagination audit

`read/gwt_pagination_audit.py` audits a pagination **manifest** offline. It
reads one local JSON file, makes no network calls, and writes nothing. It
checks the manifest only; it does not validate any live source.

```
python3 read/gwt_pagination_audit.py manifest.json
```

Exit codes: `0` complete, `2` STOP, `1` unreadable input.

## Manifest

```json
{"expected_page_size": 2, "declared_total": 3,
 "pages": [
  {"page_id": "p1", "index": 1, "source_url": "https://example.invalid/p1",
   "declared_total": 3, "ids": ["a", "b"], "expected_page_size": 2,
   "terminal": false, "wall": false, "truncated": false,
   "next_page_id": "p2"},
  {"page_id": "p2", "index": 2, "source_url": "https://example.invalid/p2",
   "ids": ["c"], "terminal": true, "wall": false, "truncated": false}
 ]}
```

Every page needs a non-empty string `source_url` (`MISSING_SOURCE_URL`).
A page-level `declared_total` must agree with the others. The top-level
`expected_page_size` is **required** and must be a positive integer; it is the
only size used for checks (`NO_PAGE_SIZE` otherwise). A page-level
`expected_page_size` is never an override: invalid is `NO_PAGE_SIZE`, any
value differing from the top-level one is `SIZE_CONFLICT`. Duplicate keys in
any JSON object make the file unreadable (exit `1`; detection is linear).
Ids that differ only by case or Unicode compatibility form are never merged:
`AMBIGUOUS_ID` STOP. Ids and page ids that are empty, whitespace-only, padded
with whitespace, or contain control/format/non-ASCII-space characters (e.g.
zero-width) are rejected (`BAD_IDS`/`BAD_PAGE_ID`). Index gaps are listed at
most once per page present, then summarized, so a huge index cannot cause
huge work or output.

**Explicit chain required.** No order is inferred. Starting at the lowest
index, every non-terminal page must have a string `next_page_id` naming an
existing page at `index + 1`, and the chain must end at the terminal page
having visited every page. A non-terminal page with `next_page_id` null or
missing is `MISSING_LINK` (never `complete`); dangling links are
`MISSING_PAGE`, loops `CYCLE`, wrong successor `OUT_OF_ORDER`, pages not on
the chain `MISSING_LINK`. A single terminal page needs no link.

## Output

JSON with `verdict` (`complete` or `STOP`), `coverage` (unique ids divided by
declared total, `null` if unknown or zero; only ids from pages with `wall`
and `truncated` both `false` count, see `verified_ids`), `unique_ids`,
`returned_ids`,
`repeats` (id → page ids where seen, first page first), per-page `pages`
evidence (index, URL, ids returned, flags, stop codes), and `stops`.

Repeated ids (within or across pages) are counted once in `unique_ids`, always
listed in `repeats`, and always cause a `REPEATED_ID` STOP. Booleans are never
accepted as integers (sizes, totals, indices). `wall`, `truncated` and
`terminal` must be real booleans; missing or non-bool values STOP
(`BAD_FLAG`/`UNKNOWN_TERMINAL`). `page_id` and ids must be non-empty strings.
The declared total must equal both unique and returned id counts. If any page
sets `next_page_id`, every page must be reachable from the first index, and a
terminal page must not link onward. Invalid manifests report the same JSON
schema (zero counts, `null` coverage). Untrusted values are never echoed raw
into output; the CLI exits `1` if output cannot be built (`RecursionError`).

## STOP codes

`BAD_MANIFEST`, `NO_PAGES`, `BAD_PAGE_ID`, `BAD_IDS`, `REPEATED_ID`,
`BAD_FLAG`, `WALL`, `TRUNCATED`,
`UNKNOWN_TERMINAL` (flag not a bool, or no terminal page), `EARLY_TERMINAL`,
`NO_PAGE_SIZE`, `OVERSIZE`, `SHORT_PAGE` (short non-terminal page),
`DUPLICATE_PAGE`, `BAD_INDEX`, `MISSING_PAGE` (index gap, first index not
0/1, or dangling `next_page_id`), `CYCLE`, `BAD_TOTAL`, `NO_DECLARED_TOTAL`,
`TOTAL_CONFLICT`, `TOTAL_MISMATCH`, `MISSING_SOURCE_URL`, `SIZE_CONFLICT`,
`AMBIGUOUS_ID`, `MISSING_LINK` (non-terminal page without `next_page_id`, or
page off the chain), `OUT_OF_ORDER`.

An empty or incomplete result is never "none found". Zero ids is `complete`
only if a terminal page and a declared total of `0` agree and nothing else
triggers a STOP.

## Limits

Unique-id counts depend on the manifest being accurate. A `complete` verdict
means the manifest is internally consistent, not that the source is complete.
Tests use synthetic fixtures only: `python3 -m unittest read/test_pagination_audit.py`.

## Demonstration

`read/demo_pagination_audit.py` runs the audit against built-in synthetic
manifests only. It makes no accuracy claim about the auditor or any real
source; it only shows the documented STOP and `complete` behaviors on
invented data.

```
python3 read/demo_pagination_audit.py
```

Expected result: `27/27` demonstration cases pass.
