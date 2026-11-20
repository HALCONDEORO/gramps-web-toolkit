import json, os, sys, tempfile, unittest, io, contextlib
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gwt_pagination_audit as A


def page(pid, idx, ids, terminal=False, nxt=None, **kw):
    d = {"page_id": pid, "index": idx, "source_url": f"https://example.invalid/{pid}",
         "ids": ids, "terminal": terminal, "wall": False, "truncated": False,
         "next_page_id": nxt}
    d.update(kw)
    return d


def good():
    return {"expected_page_size": 2, "declared_total": 3, "pages": [
        page("p1", 1, ["a", "b"], nxt="p2"),
        page("p2", 2, ["c"], terminal=True)]}


def codes(m):
    return {s["code"] for s in A.audit(m)["stops"]}


class AuditTests(unittest.TestCase):
    def test_complete(self):
        r = A.audit(good())
        self.assertEqual(r["verdict"], "complete")
        self.assertEqual(r["coverage"], 1.0)
        self.assertEqual(len(r["pages"]), 2)

    def test_missing_page(self):
        m = good()
        m["pages"][1]["index"] = 3
        m["pages"][0]["next_page_id"] = None
        self.assertIn("MISSING_PAGE", codes(m))

    def test_dangling_next(self):
        m = good()
        m["pages"][0]["next_page_id"] = "zzz"
        self.assertIn("MISSING_PAGE", codes(m))

    def test_cycle(self):
        m = good()
        m["pages"][1]["next_page_id"] = "p1"
        self.assertIn("CYCLE", codes(m))

    def test_cycle_terminal_links_back(self):
        m = good()
        m["pages"][-1]["next_page_id"] = m["pages"][0]["page_id"]
        c = codes(m)
        self.assertIn("CYCLE", c)
        self.assertIn("EARLY_TERMINAL", c)

    def test_unknown_terminal(self):
        m = good()
        m["pages"][1]["terminal"] = None
        c = codes(m)
        self.assertIn("UNKNOWN_TERMINAL", c)

    def test_total_mismatch(self):
        m = good()
        m["declared_total"] = 4
        self.assertIn("TOTAL_MISMATCH", codes(m))

    def test_repeats_reported_not_hidden(self):
        m = good()
        m["pages"][1]["ids"] = ["b"]
        m["declared_total"] = 2
        r = A.audit(m)
        self.assertEqual(r["repeats"], {"b": ["p1", "p2"]})
        self.assertEqual(r["unique_ids"], 2)
        self.assertEqual(r["returned_ids"], 3)

    def test_wall_and_truncation(self):
        m = good()
        m["pages"][0]["wall"] = True
        m["pages"][1]["truncated"] = True
        self.assertTrue({"WALL", "TRUNCATED"} <= codes(m))

    def test_short_nonterminal_and_oversize(self):
        m = good()
        m["pages"][0]["ids"] = ["a"]
        self.assertIn("SHORT_PAGE", codes(m))
        m = good()
        m["pages"][0]["ids"] = ["a", "b", "x"]
        self.assertIn("OVERSIZE", codes(m))

    def test_empty_is_not_none_found(self):
        self.assertEqual(A.audit({"pages": []})["verdict"], "STOP")
        m = {"expected_page_size": 2, "pages": [page("p1", 1, [], True)]}
        self.assertIn("NO_DECLARED_TOTAL", codes(m))

    def test_confirmed_empty(self):
        m = {"expected_page_size": 2, "declared_total": 0,
             "pages": [page("p1", 1, [], True)]}
        self.assertEqual(A.audit(m)["verdict"], "complete")

    def test_early_terminal_and_duplicate_page(self):
        m = good()
        m["pages"][0]["terminal"] = True
        self.assertIn("EARLY_TERMINAL", codes(m))
        m = good()
        m["pages"][1]["page_id"] = "p1"
        self.assertIn("DUPLICATE_PAGE", codes(m))

    def test_bad_manifest(self):
        self.assertIn("BAD_MANIFEST", codes([]))

    def test_repeat_ids_stop_even_if_total_matches(self):
        m = good()
        m["pages"][1]["ids"] = ["b"]
        m["declared_total"] = 2
        r = A.audit(m)
        self.assertEqual(r["verdict"], "STOP")
        self.assertIn("REPEATED_ID", codes(m))
        m = good()
        m["pages"][0]["ids"] = ["a", "a"]
        self.assertIn("REPEATED_ID", codes(m))

    def test_bool_as_int_rejected(self):
        for key in ("expected_page_size", "declared_total"):
            m = good()
            m[key] = True
            self.assertEqual(A.audit(m)["verdict"], "STOP")
        m = good()
        m["pages"][0]["index"] = True
        m["pages"][1]["index"] = 2
        self.assertIn("BAD_INDEX", codes(m))
        m = good()
        m["pages"][0]["index"] = True
        m["pages"][1]["index"] = 1
        self.assertIn("BAD_INDEX", codes(m))

    def test_missing_unknown_totals(self):
        m = good()
        del m["declared_total"]
        self.assertIn("NO_DECLARED_TOTAL", codes(m))
        for bad in ("3", 3.0, -1, [3]):
            m = good()
            m["declared_total"] = bad
            self.assertIn("BAD_TOTAL", codes(m))
        m = good()
        m["pages"][0]["declared_total"] = 4
        self.assertIn("TOTAL_CONFLICT", codes(m))

    def test_false_zero_and_empty_pages(self):
        m = good()
        m["declared_total"] = 0
        self.assertIn("TOTAL_MISMATCH", codes(m))
        m = {"expected_page_size": 2, "declared_total": 0,
             "pages": [page("p1", 1, [], False, nxt="p2"),
                       page("p2", 2, [], True)]}
        self.assertIn("SHORT_PAGE", codes(m))
        m = {"expected_page_size": 2, "declared_total": 3,
             "pages": [page("p1", 1, ["a", "b", "c"][:2], nxt="p2"),
                       page("p2", 2, [], True)]}
        self.assertIn("TOTAL_MISMATCH", codes(m))

    def test_malformed_types_do_not_crash(self):
        for pid in ([], {}, "", None, 5):
            m = good()
            m["pages"][0]["page_id"] = pid
            self.assertEqual(A.audit(m)["verdict"], "STOP")
        for ids in ([["x"], "b"], [1, 2], ["a", None], "ab", None):
            m = good()
            m["pages"][0]["ids"] = ids
            self.assertEqual(A.audit(m)["verdict"], "STOP")
        for nxt in ([], {}, 7):
            m = good()
            m["pages"][0]["next_page_id"] = nxt
            self.assertEqual(A.audit(m)["verdict"], "STOP")
        m = good()
        for p in m["pages"]:
            p["index"] = "x"
        self.assertIn("BAD_INDEX", codes(m))

    def test_flags_must_be_bool(self):
        for key in ("wall", "truncated"):
            for val in (0, "", None, "false"):
                m = good()
                m["pages"][0][key] = val
                self.assertEqual(A.audit(m)["verdict"], "STOP")
        m = good()
        del m["pages"][0]["wall"]
        self.assertIn("BAD_FLAG", codes(m))

    def test_duplicate_page_not_masked(self):
        m = good()
        m["pages"][1]["page_id"] = "p1"
        m["pages"][0]["wall"] = True
        r = A.audit(m)
        self.assertIn("WALL", codes(m))
        self.assertIn("WALL", r["pages"][0]["stops"])

    def test_unreachable_and_terminal_link(self):
        m = good()
        m["pages"][0]["next_page_id"] = None
        m["pages"][1]["next_page_id"] = "p1"
        self.assertIn("MISSING_LINK", codes(m))
        self.assertIn("EARLY_TERMINAL", codes(m))
        m = good()
        m["pages"][1]["next_page_id"] = "p2"
        self.assertIn("EARLY_TERMINAL", codes(m))
        m = good()
        m["pages"].append(page("p3", 3, ["d"], True))
        m["declared_total"] = 4
        m["pages"][1]["terminal"] = False
        m["pages"][1]["ids"] = ["c", "x"]
        m["declared_total"] = 5
        self.assertIn("MISSING_LINK", codes(m))

    def test_page_size_lies(self):
        m = good()
        m["pages"][0]["expected_page_size"] = True
        self.assertIn("NO_PAGE_SIZE", codes(m))
        m = good()
        m["pages"][0]["expected_page_size"] = 1
        self.assertIn("SIZE_CONFLICT", codes(m))

    def test_missing_source_url(self):
        for val in (None, "", 5, []):
            m = good()
            m["pages"][0]["source_url"] = val
            self.assertIn("MISSING_SOURCE_URL", codes(m))
        m = good()
        del m["pages"][1]["source_url"]
        r = A.audit(m)
        self.assertEqual(r["verdict"], "STOP")
        self.assertIn("MISSING_SOURCE_URL", r["pages"][1]["stops"])

    def test_page_size_override_conflict(self):
        m = good()
        m["pages"][0]["expected_page_size"] = 5  # cannot hide anything
        self.assertIn("SIZE_CONFLICT", codes(m))
        m = good()
        m["pages"][0]["ids"] = ["a"]
        m["pages"][0]["expected_page_size"] = 1  # would mask SHORT_PAGE
        c = codes(m)
        self.assertTrue({"SIZE_CONFLICT", "SHORT_PAGE"} <= c)
        m = good()
        m["pages"][0]["expected_page_size"] = 2  # same value is fine
        self.assertEqual(A.audit(m)["verdict"], "complete")

    def test_huge_index_gap_is_bounded(self):
        m = good()
        m["pages"][1]["index"] = 10 ** 15
        m["pages"][0]["next_page_id"] = None
        r = A.audit(m)
        self.assertIn("MISSING_PAGE", codes(m))
        self.assertLess(len(r["stops"]), 20)
        self.assertTrue(any("truncated" in s["detail"] for s in r["stops"]))

    def test_near_identical_ids_stop(self):
        for other in ("A", "ａ", "Ａ"):
            m = good()
            m["pages"][1]["ids"] = [other]
            m["declared_total"] = 3
            self.assertIn("AMBIGUOUS_ID", codes(m))
        for other in ("a ","ａ", "á"[:1] + " "):
            m = good()
            m["pages"][1]["ids"] = [other]
            m["declared_total"] = 3
            # padded ids are rejected (BAD_IDS); compat forms are ambiguous
            self.assertTrue(codes(m) & {"AMBIGUOUS_ID", "BAD_IDS"})
        self.assertNotIn("AMBIGUOUS_ID", codes(good()))

    def test_duplicate_json_keys(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "m.json")
            with open(p, "w") as fh:
                fh.write('{"declared_total": 1, "declared_total": 3, '
                         '"pages": []}')
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(A.main([p]), 1)
            with self.assertRaises(ValueError):
                A.load_manifest(p)

    def test_cli(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "m.json")
            with open(p, "w") as fh:
                json.dump(good(), fh)
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(A.main([p]), 0)
                m = good()
                m["declared_total"] = 9
                with open(p, "w") as fh:
                    json.dump(m, fh)
                self.assertEqual(A.main([p]), 2)
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(A.main([os.path.join(d, "nope")]), 1)


class FinalReviewTests(unittest.TestCase):
    def test_nonterminal_without_link_stops(self):
        for drop in (False, True):
            m = good()
            if drop:
                del m["pages"][0]["next_page_id"]
            else:
                m["pages"][0]["next_page_id"] = None
            r = A.audit(m)
            self.assertEqual(r["verdict"], "STOP")
            self.assertIn("MISSING_LINK", codes(m))
        m = good()  # no links anywhere: unproven order
        m["pages"][1]["terminal"] = True
        for p in m["pages"]:
            p["next_page_id"] = None
        self.assertIn("MISSING_LINK", codes(m))

    def test_out_of_order_chain(self):
        m = good()
        m["pages"].insert(1, page("p3", 3, ["d"], True))
        m["pages"][0]["next_page_id"] = "p3"
        m["pages"][2]["terminal"] = False
        m["pages"][2]["next_page_id"] = None
        self.assertIn("OUT_OF_ORDER", codes(m))

    def test_page_size_needs_manifest_default(self):
        m = good()
        del m["expected_page_size"]
        for p in m["pages"]:
            p["expected_page_size"] = 2
        self.assertIn("NO_PAGE_SIZE", codes(m))
        m = good()
        m["pages"][0]["ids"] = ["a"]
        m["declared_total"] = 2
        for p in m["pages"]:
            p["expected_page_size"] = 1
        del m["expected_page_size"]
        self.assertIn("NO_PAGE_SIZE", codes(m))
        for bad in (0, -1, "2", True, None, 2.0):
            m = good()
            m["expected_page_size"] = bad
            self.assertIn("NO_PAGE_SIZE", codes(m))

    def test_format_only_ids_rejected(self):
        for bad in (" ", "a ", " a", "\t", "​", "a​", " ",
                    "　", "a\nb", "﻿"):
            m = good()
            m["pages"][1]["ids"] = [bad]
            self.assertIn("BAD_IDS", codes(m), repr(bad))
        for bad in (" ", "p1 ", "​"):
            m = good()
            m["pages"][0]["page_id"] = bad
            self.assertIn("BAD_PAGE_ID", codes(m))

    def test_wall_truncated_not_verified_coverage(self):
        m = good()
        m["pages"][0]["wall"] = True
        r = A.audit(m)
        self.assertIn("WALL", codes(m))
        self.assertEqual(r["verified_ids"], 1)
        self.assertEqual(r["coverage"], round(1 / 3, 4))
        m = good()
        m["pages"][1]["truncated"] = True
        r = A.audit(m)
        self.assertIn("TRUNCATED", codes(m))
        self.assertEqual(r["verified_ids"], 2)
        self.assertLess(r["coverage"], 1.0)
        self.assertEqual(A.audit(good())["verified_ids"], 3)

    def test_schema_bad_matches_normal(self):
        keys = set(A.audit(good()))
        for m in ([], {"pages": []}, {"pages": [1]}):
            r = A.audit(m)
            self.assertEqual(set(r), keys)
            self.assertEqual(r["verdict"], "STOP")

    def test_nested_values_do_not_leak_or_recurse(self):
        deep = []
        for _ in range(50000):
            deep = [deep]
        m = good()
        m["pages"][0]["next_page_id"] = deep
        m["pages"][1]["source_url"] = deep
        m["pages"][1]["terminal"] = deep
        m["declared_total"] = deep
        r = A.audit(m)
        self.assertIn("BAD_TOTAL", codes(m))
        json.dumps(r)  # must not raise RecursionError

    def test_main_catches_recursion_in_output(self):
        orig = A.audit
        def boom(_m):
            raise RecursionError
        A.audit = boom
        try:
            with tempfile.TemporaryDirectory() as d:
                p = os.path.join(d, "m.json")
                with open(p, "w") as fh:
                    json.dump(good(), fh)
                with contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(A.main([p]), 1)
        finally:
            A.audit = orig

    def test_dup_keys_linear_and_reported(self):
        import time
        pairs = [(str(i), i) for i in range(200000)]
        t = time.time()
        self.assertEqual(len(A._no_dup_keys(pairs)), 200000)
        self.assertLess(time.time() - t, 2)
        with self.assertRaises(ValueError):
            A._no_dup_keys([("a", 1), ("b", 2), ("a", 3)])


class MutantFocusTests(unittest.TestCase):
    def test_false_zero_has_no_coverage(self):
        m = good()
        m["declared_total"] = 0
        r = A.audit(m)
        self.assertIn("TOTAL_MISMATCH", codes(m))
        self.assertIsNone(r["coverage"])
        m = {"expected_page_size": 2, "declared_total": 0,
             "pages": [page("p1", 1, [], True)]}
        r = A.audit(m)
        self.assertEqual(r["verdict"], "complete")
        self.assertIsNone(r["coverage"])

    def test_total_mismatch_when_only_returned_differs(self):
        m = good()
        m["pages"][1]["ids"] = ["b"]
        m["declared_total"] = 2  # equals unique, not returned (3)
        self.assertIn("TOTAL_MISMATCH", codes(m))

    def test_total_conflict_two_values(self):
        m = good()
        m["pages"][1]["declared_total"] = 4
        self.assertIn("TOTAL_CONFLICT", codes(m))

    def test_duplicate_id_within_page_reported_as_such(self):
        m = good()
        m["pages"][0]["ids"] = ["a", "a"]
        details = [s["detail"] for s in A.audit(m)["stops"]
                   if s["code"] == "REPEATED_ID"]
        self.assertTrue(any("within page" in d for d in details))

    def test_truthy_nonbool_flags_are_bad_flag_not_wall(self):
        for key, real in (("wall", "WALL"), ("truncated", "TRUNCATED")):
            for val in (1, "false", "yes"):
                m = good()
                m["pages"][0][key] = val
                c = codes(m)
                self.assertIn("BAD_FLAG", c)
                self.assertNotIn(real, c)

    def test_source_url_missing_per_page(self):
        for val in (None, "", 0, {}):
            m = good()
            m["pages"][1]["source_url"] = val
            r = A.audit(m)
            self.assertEqual(r["verdict"], "STOP")
            self.assertIn("MISSING_SOURCE_URL", r["pages"][1]["stops"])
            self.assertNotIn("MISSING_SOURCE_URL", r["pages"][0]["stops"])

    def test_duplicate_index_with_distinct_page_ids(self):
        m = good()
        m["pages"][1]["index"] = 1
        self.assertIn("DUPLICATE_PAGE", codes(m))

    def test_index_start_limits(self):
        m = good()
        m["pages"][0]["index"], m["pages"][1]["index"] = 0, 1
        self.assertEqual(A.audit(m)["verdict"], "complete")
        for lo in (2, -1):
            m = good()
            m["pages"][0]["index"], m["pages"][1]["index"] = lo, lo + 1
            self.assertIn("MISSING_PAGE", codes(m))

    def test_page_size_boundaries(self):
        m = good()
        m["pages"][0]["ids"] = ["a", "b"]  # exactly size
        self.assertNotIn("OVERSIZE", codes(m))
        m["pages"][1]["ids"] = ["c", "d"]  # terminal exactly full
        m["declared_total"] = 4
        self.assertEqual(A.audit(m)["verdict"], "complete")
        m = good()  # terminal page shorter than size is fine
        self.assertNotIn("SHORT_PAGE", codes(m))
        m["pages"][1]["ids"] = ["c", "d", "e"]
        self.assertIn("OVERSIZE", codes(m))


if __name__ == "__main__":
    unittest.main()
