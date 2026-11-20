"""Demonstration runner for gwt_pagination_audit (synthetic, offline).

Implements the fixtures from contributions/reviews/pagination-fixture-spec.md
and calls the real ``audit()`` on each. Asserts verdict and ordered stop codes.
Stdlib only; no files written, no network. Default output is a brief summary.

Usage: python3 -I read/demo_pagination_audit.py [-v]
Exit: 0 all expectations held, 1 otherwise.
"""
import copy
import sys

from gwt_pagination_audit import audit


def check(condition, detail):
    """Do not drop demonstration checks under Python -O."""
    if not condition:
        raise AssertionError(detail)


def page(n, ids, terminal=False, **over):
    p = {"page_id": f"p{n}", "index": n,
         "source_url": f"https://example.invalid/p{n}", "ids": list(ids),
         "terminal": terminal, "wall": False, "truncated": False,
         "next_page_id": None if terminal else f"p{n + 1}"}
    p.update(over)
    return p


def man(pages, total=None, size=3, **extra):
    m = {"expected_page_size": size, "pages": pages}
    if total is not None:
        m["declared_total"] = total
    m.update(extra)
    return m


def tc1():
    return man([page(1, "abc"), page(2, "de", True)], 5)


def _w3():
    m = tc1()
    del m["pages"][0]["wall"]
    return m


def _tc1_with(**over):
    m = tc1()
    m["pages"][0].update(over)
    return m


# (id, manifest, verdict, ordered stop codes, optional extra checks dict)
CASES = [
    ("TC1", tc1(), "complete", [],
     {"unique_ids": 5, "returned_ids": 5, "verified_ids": 5, "coverage": 1.0}),
    ("TC2", man([page(1, "", True)], 0), "complete", [],
     {"unique_ids": 0, "verified_ids": 0, "coverage": None}),
    ("FZ1", man([], 0), "STOP", ["NO_PAGES"], {"coverage": None}),
    ("FZ2", {"expected_page_size": 3, "declared_total": 0}, "STOP",
     ["BAD_MANIFEST"], {"coverage": None}),
    ("FZ3", man([page(1, "", False, next_page_id=None)], 0), "STOP",
     ["SHORT_PAGE", "UNKNOWN_TERMINAL", "MISSING_LINK"], {}),
    ("FZ4", man([page(1, "", True)]), "STOP", ["NO_DECLARED_TOTAL"], {}),
    ("FZ5", man([page(1, "", True)], 5), "STOP", ["TOTAL_MISMATCH"], {}),
    ("FZ6", man([page(1, "", True, wall=True)], 0), "STOP", ["WALL"], {}),
    ("MP1", man([page(1, "abc"), page(3, "g", True)], 7), "STOP",
     ["MISSING_PAGE", "MISSING_PAGE", "TOTAL_MISMATCH"],
     {"coverage": 0.5714}),
    ("MP2", man([page(2, "ab", True)], 2), "STOP", ["MISSING_PAGE"], {}),
    ("MP3", man([page(1, "abc", False, next_page_id=None),
                 page(2, "de", True)], 5), "STOP", ["MISSING_LINK"], {}),
    ("W1", man([page(1, "abc", wall=True), page(2, "de", True)], 5), "STOP",
     ["WALL"], {"coverage": 0.4, "verified_ids": 2}),
    ("W2", _tc1_with(truncated=True), "STOP", ["TRUNCATED"], {}),
    ("W3", _w3(), "STOP", ["BAD_FLAG"], {}),
    ("W4", _tc1_with(wall="false"), "STOP", ["BAD_FLAG"], {}),
    ("R1", man([page(1, "abc"), page(2, "ce", True)], 5), "STOP",
     ["REPEATED_ID", "TOTAL_MISMATCH"], {"repeats": {"c": ["p1", "p2"]}}),
    ("R2", man([page(1, "aab", True)], 3), "STOP",
     ["REPEATED_ID", "REPEATED_ID", "TOTAL_MISMATCH"], {}),
    ("C1", man([page(1, "abc"), page(2, "def", next_page_id="p1"),
                page(3, "g", True)], 7), "STOP", ["OUT_OF_ORDER", "CYCLE"], {}),
    ("C2", man([page(1, "abc"), page(2, "de", True, next_page_id="p1")], 5),
     "STOP", ["EARLY_TERMINAL", "CYCLE"], {}),
    ("C3", man([page(1, "abc", False, next_page_id="p1")], 3), "STOP",
     ["UNKNOWN_TERMINAL", "OUT_OF_ORDER", "CYCLE"], {}),
    # Index/ids/size/total fixtures (hand-traced against audit()).
    ("D1", man([page(1, "abc"), page(2, "de", True, index=1)], 5), "STOP",
     ["DUPLICATE_PAGE", "OUT_OF_ORDER"], {}),
    ("I1", man([page(1, "abc"), page(2, "de", True, index="2")], 5), "STOP",
     ["BAD_INDEX", "EARLY_TERMINAL"], {}),
    ("B1", man([page(1, ["a", "b", " c"]), page(2, "de", True)], 5), "STOP",
     ["BAD_IDS", "TOTAL_MISMATCH"], {"unique_ids": 4, "returned_ids": 5}),
    ("A1", man([page(1, "abc"), page(2, "Ae", True)], 5), "STOP",
     ["AMBIGUOUS_ID"], {"unique_ids": 5}),
    ("S1", _tc1_with(expected_page_size=4), "STOP", ["SIZE_CONFLICT"], {}),
    ("O1", man([page(1, "abcd"), page(2, "e", True)], 5), "STOP",
     ["OVERSIZE"], {}),
    ("T1", _tc1_with(declared_total=6), "STOP", ["TOTAL_CONFLICT"],
     {"coverage": None, "declared_total": None}),
]


def run_case(cid, manifest, verdict, codes, extra):
    report = audit(copy.deepcopy(manifest))
    got = [s["code"] for s in report["stops"]]
    check((report["verdict"] == verdict), (f"{cid}: verdict {report['verdict']}"))
    check((got == codes), (f"{cid}: stops {got} != {codes}"))
    for k, v in extra.items():
        check((report[k] == v), (f"{cid}: {k} {report[k]!r} != {v!r}"))
    # R2 spec: per-page evidence stops
    if cid == "R2":
        check((report["pages"][0]["stops"] == ["REPEATED_ID"]), (cid))
    return report


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    verbose = "-v" in args
    failed = 0
    for case in CASES:
        cid = case[0]
        try:
            report = run_case(*case)
            if verbose:
                print(f"PASS {cid}: {report['verdict']} "
                      f"{[s['code'] for s in report['stops']]}")
        except AssertionError as exc:
            failed += 1
            print(f"FAIL {exc}")
    print(f"pagination audit demo: {len(CASES) - failed}/{len(CASES)} "
          f"fixtures matched ({'OK' if not failed else 'MISMATCH'}); "
          "synthetic, offline")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
