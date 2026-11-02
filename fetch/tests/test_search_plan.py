"""Synthetic planning contracts; no searches or observations are performed."""
import copy
import socket
import unittest
from unittest.mock import patch

from gwt_fetch import names
from gwt_fetch import search_plan


def selection(spelling="Zunexa Molquix", provenance="synthetic selection"):
    return {"spelling": spelling, "provenance": provenance}


def plan(selected=None, sources=None):
    return search_plan.build("Nuvio", selection(), selected or [],
                             ["gazeta"] if sources is None else sources)


class SearchPlanTests(unittest.TestCase):
    def test_exact_queries_and_unexecuted_status_without_io(self):
        with patch.object(socket, "socket", side_effect=AssertionError("network")), \
             patch("builtins.open", side_effect=AssertionError("file access")):
            result = plan()
        self.assertEqual(result["status"], "planned")
        self.assertIs(result["executed"], False)
        self.assertEqual([r["query"] for r in result["entries"]],
                         names.queries("Nuvio", "Zunexa Molquix"))
        for row in result["entries"]:
            self.assertEqual(row["source"], "gazeta")
            self.assertEqual(row["status"], "planned")
            self.assertEqual(row["coverage_status"], "unknown")
            self.assertEqual(row["normalized_query"], names.fold(row["query"]))
            self.assertNotIn("hits", row)
            self.assertNotIn("searched_at", row)

    def test_fold_collision_keeps_every_literal_query_and_provenance(self):
        aliases = [selection("Zúnexa Molquix", "synthetic glyph reading"),
                   selection("ZUNEXA Molquix", "synthetic manual selection"),
                   selection(provenance="synthetic second observation")]
        result = plan(aliases)
        self.assertEqual(len(result["entries"]), 2)
        for index, row in enumerate(result["entries"]):
            self.assertEqual(len(row["contributions"]), 4)
            self.assertEqual({r["spelling"] for r in row["contributions"]},
                             {"Zunexa Molquix", "Zúnexa Molquix", "ZUNEXA Molquix"})
            for r in row["contributions"]:
                self.assertEqual(r["query"], names.queries("Nuvio", r["spelling"])[index])

    def test_explicit_full_compound_and_second_surnames_survive(self):
        result = search_plan.build("Nuvio", selection("Zunexa de Molquix Talvex"),
                                   [selection("Zunexxa de Molquix Talvex")], ["gazeta"])
        self.assertEqual(len(result["entries"]), 4)
        self.assertTrue(all("de Molquix Talvex" in r["query"] for r in result["entries"]))

    def test_source_queries_never_collapse_across_sources(self):
        result = plan(sources=["boe", "gazeta"])
        self.assertEqual(len(result["entries"]), 4)
        self.assertEqual({(r["source"], r["query"]) for r in result["entries"]},
                         {(s, q) for s in ("boe", "gazeta")
                          for q in names.queries("Nuvio", "Zunexa Molquix")})

    def test_permutations_duplicates_and_mutation_are_safe(self):
        aliases = [selection("Zunexxa Molquix"), selection("Zúnexa Molquix")]
        before = copy.deepcopy(aliases)
        result = plan(aliases, ["boe", "gazeta"])
        self.assertEqual(result, plan(list(reversed(aliases)), ["gazeta", "boe"]))
        self.assertEqual(result, plan(aliases + [aliases[0]], ["boe", "gazeta"]))
        result["entries"][0]["contributions"][0]["spelling"] = "ChangedSynthetic"
        self.assertEqual(aliases, before)

    def test_only_selected_literals_no_rules_reverse_or_closure(self):
        result = plan([selection("Zunexxa Molquix")])
        self.assertEqual({r["spelling"] for e in result["entries"] for r in e["contributions"]},
                         {"Zunexa Molquix", "Zunexxa Molquix"})
        self.assertEqual(len(result["entries"]), 4)
        # Cluster results/configuration cannot masquerade as an explicit selection.
        for bad in ({"status": "ambiguous", "suggestions": []},
                    {"Zunexa Molquix": ["Zunexxa Molquix"]}):
            with self.assertRaises(ValueError):
                search_plan.build("Nuvio", selection(), bad, ["gazeta"])

    def test_bounds_reject_instead_of_truncating(self):
        aliases = [selection("Synthetic" + str(i)) for i in range(8)]
        self.assertEqual(len(plan(aliases, ["boe", "gazeta"])["entries"]), 36)
        with self.assertRaises(ValueError):
            plan(aliases + [selection("SyntheticExtra")])
        for bad in ([], ["gazeta", "gazeta"], ["boe", "gazeta", "other"], ["other"], "boe"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                plan(sources=bad)

    def test_invalid_selections_rejected_before_any_plan(self):
        for bad in ({}, {"spelling": "Zunexa"}, selection(provenance=""),
                    dict(selection(), status="confirmed"), "Zunexa", None):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                plan([bad])
        for bad in ("", " ", 'X"Y', "X\nY", "X\ud800", "X" * 129, " X", "X  Y", "\u0301"):
            with self.subTest(bad=repr(bad)), self.assertRaises(ValueError):
                plan([selection(bad)])
            with self.assertRaises(ValueError):
                search_plan.build(bad, selection(), [], ["gazeta"])
        for bad in ("X\nY", "X\ud800", "X" * 513):
            with self.assertRaises(ValueError):
                plan([selection(provenance=bad)])

    def test_existing_query_api_stays_unchanged(self):
        self.assertEqual(names.queries("Nuvio", "Zunexa Molquix", ["Zúnexa"]),
                         ['"Nuvio Zunexa Molquix"', '"Zunexa Molquix, Nuvio"'])


if __name__ == "__main__":
    unittest.main()
