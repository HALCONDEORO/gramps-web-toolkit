import io, os, sys, tempfile, unittest
import csv
import json
import subprocess
from pathlib import Path
from unittest.mock import patch
from contextlib import redirect_stderr, redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gwt_access as A


class AccessTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.m = os.path.join(self.d.name, "a.tsv")

    def tearDown(self):
        self.d.cleanup()

    def run_(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            try:
                code = A.main(["--map", self.m, *argv])
            except SystemExit as e:
                code = e.code
        return code, out.getvalue(), err.getvalue()

    def test_alias_finds_the_row_and_stops_corpus_adding_a_duplicate(self):
        self.run_("set", "AD99", "--viewer", "js", "--captcha", "no", "--alias", "archives departementales de Zed")
        self.assertEqual(self.run_("show", "Archives Departementales de Zed")[0], 0)
        self.assertIn("read now", self.run_("plan", "archives departementales de Zed")[1])
        c = os.path.join(self.d.name, "c.csv")
        open(c, "w", encoding="utf-8").write(
            "intitule,institution,id_institution,url_site,type_document,lieu,theme,periode,observations\n"
            "X,archives departementales de Zed,FRAD999,https://zed.example/,etat civil,Zed,,19e,\n")
        self.run_("corpus", c, "--into")
        self.assertEqual(len(A.load(self.m)), 1)

    def test_corpus_lists_filters_and_seeds_without_overwriting(self):
        c = os.path.join(self.d.name, "c.csv")
        open(c, "w", encoding="utf-8").write(
            "intitule,institution,id_institution,url_site,type_document,lieu,theme,periode,observations\n"
            "X. Etat civil,archives departementales de Zed,FRAD999,https://zed.example/,\u00e9tat civil,Zed,,18e si\u00e8cle|19e si\u00e8cle,\n"
            "X. Matricules,archives departementales de Zed,FRAD999,https://zed.example/,matricules militaires,Zed,,19e si\u00e8cle,\n"
            "Y. Etat civil,archives departementales de Wye,FRAD998,https://wye.example/,\u00e9tat civil,Wye,,19e si\u00e8cle,\n")
        code, out, _ = self.run_("corpus", c, "--place", "zed", "--type", "\u00e9tat civil")
        self.assertEqual(code, 0)
        self.assertEqual(len(out.splitlines()), 1)
        self.assertIn("https://zed.example/", out)
        self.run_("set", "archives departementales de Zed", "--viewer", "images", "--captcha", "no")
        code, _, err = self.run_("corpus", c, "--into")
        self.assertIn("1 archive(s) added", err)  # Wye only; Zed was already known
        _, shown, _ = self.run_("show", "archives departementales de Zed")
        self.assertIn("viewer: images", shown)
        _, shown, _ = self.run_("show", "archives departementales de Wye")
        self.assertIn("viewer: unknown", shown)
        self.assertIn("19e", shown)
        self.assertEqual(self.run_("corpus", c, "--place", "nowhere")[0], 1)
        open(c, "w").write("a,b\n1,2\n")
        self.assertEqual(self.run_("corpus", c)[0], 2)

    def test_set_updates_only_what_is_given_and_show_reads_it(self):
        self.run_("set", "Arch A", "--viewer", "images", "--captcha", "no")
        self.run_("set", "arch a", "--download", "yes")
        code, out, _ = self.run_("show", "ARCH A")
        self.assertEqual(code, 0)
        self.assertIn("viewer: images", out)
        self.assertIn("download: yes", out)
        self.assertEqual(self.run_("show", "nope")[0], 1)

    def test_plan_separates_read_now_write_blocked_and_unknown(self):
        self.run_("set", "A", "--viewer", "images", "--captcha", "no")
        self.run_("set", "B", "--viewer", "js", "--captcha", "yes", "--writes", "yes")
        self.run_("set", "C", "--viewer", "none", "--captcha", "yes", "--writes", "no")
        _, out, _ = self.run_("plan", "A,B,C,D")
        lines = dict(l.split("\t") for l in out.splitlines())
        self.assertTrue(lines["A"].startswith("read now"))
        self.assertTrue(lines["B"].startswith("write"))
        self.assertTrue(lines["C"].startswith("blocked"))
        self.assertTrue(lines["D"].startswith("unknown"))

    def test_list_filters_and_bad_choice_exits_2(self):
        self.run_("set", "A", "--captcha", "no")
        self.run_("set", "B", "--captcha", "yes")
        _, out, _ = self.run_("list", "--captcha", "no")
        self.assertEqual(len(out.splitlines()), 1)
        self.assertEqual(self.run_("set", "A", "--captcha", "maybe")[0], 2)

    def test_unknown_captcha_asks_to_look_and_tabs_are_cleaned(self):
        self.run_("set", "E", "--viewer", "js", "--note", "a\tb\nc")
        self.assertTrue(self.run_("plan", "E")[1].split("\t")[1].startswith("check"))
        self.assertIn("note: a b c", self.run_("show", "E")[1])


class CoverageTest(unittest.TestCase):
    run_ = AccessTest.run_
    tearDown = AccessTest.tearDown

    def setUp(self):
        AccessTest.setUp(self)
        self.tsv = os.path.join(self.d.name, "coverage.tsv")
        self.columns = ("archive", "parish", "record_type", "year_from", "year_to",
                        "status", "evidence_ref", "checked")

    def row(self, **changes):
        return dict(zip(self.columns, ("Archive A", "Saint Test", "baptisms", "1800", "1801",
                                      "online", "local:inventory/7", "2020-02-29"))) | changes

    def write_rows(self, rows, columns=None):
        with open(self.tsv, "w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, columns or self.columns, delimiter="\t")
            writer.writeheader()
            writer.writerows(rows)

    def query(self, *extra):
        return self.run_("coverage", self.tsv, "--parish", "Saint Test", "--type", "baptisms",
                         "--year-from", "1799", "--year-to", "1803", *extra)

    def test_coverage_inclusive_gaps_exact_matches_and_provenance(self):
        self.write_rows([self.row(), self.row(archive="Archive B", year_from="1802", year_to="1802",
                                              status="restricted", evidence_ref="https://example.invalid/catalogue"),
                         self.row(year_from="1803", year_to="1803", status="not_digitised"),
                         self.row(parish="saint test", status="not_digitised"),
                         self.row(parish="Saint Test Annex", status="not_digitised"),
                         self.row(record_type="Baptisms", status="not_digitised"),
                         self.row(record_type="baptisms index", status="not_digitised")])
        code, out, err = self.query()
        self.assertEqual((code, err), (0, ""))
        report = json.loads(out)
        self.assertEqual((report["parish"], report["record_type"], report["year_from"], report["year_to"]),
                         ("Saint Test", "baptisms", 1799, 1803))
        self.assertEqual([y["year"] for y in report["years"]], list(range(1799, 1804)))
        self.assertEqual([y["status"] for y in report["years"]],
                         ["unknown", "online", "online", "restricted", "not_digitised"])
        self.assertEqual(report["years"][0]["sources"], [])
        self.assertEqual(report["years"][0]["statuses"], [])
        self.assertEqual(report["years"][1]["sources"],
                         [self.row() | {"year_from": 1800, "year_to": 1801}])
        self.assertEqual(report["years"][3]["sources"][0]["archive"], "Archive B")
        self.assertEqual(report["years"][3]["sources"][0]["evidence_ref"],
                         "https://example.invalid/catalogue")

    def test_coverage_conflicts_duplicates_order_and_dates(self):
        rows = [self.row(), self.row(archive="Archive B", status="restricted", checked="2025-01-01"),
                self.row(evidence_ref="local:another", checked="2010-01-01")]
        self.write_rows(rows)
        first = self.query()[1]
        year = json.loads(first)["years"][1]
        self.assertEqual(year["status"], "conflict")
        self.assertEqual(year["statuses"], ["online", "restricted"])
        self.assertEqual(len(year["sources"]), 3)
        self.assertEqual({r["checked"] for r in year["sources"]},
                         {"2020-02-29", "2025-01-01", "2010-01-01"})
        self.write_rows(list(reversed(rows)) + [rows[0], rows[1]])
        self.assertEqual(self.query()[1], first)
        self.write_rows([self.row(status="unknown"), self.row()])
        self.assertEqual(json.loads(self.query()[1])["years"][1]["status"], "conflict")
        self.write_rows([self.row(status="unknown")])
        year = json.loads(self.query()[1])["years"][1]
        self.assertEqual((year["status"], year["statuses"], len(year["sources"])),
                         ("unknown", ["unknown"], 1))
        self.write_rows([self.row(), self.row(archive="Archive B", evidence_ref="local:other")])
        year = json.loads(self.query()[1])["years"][1]
        self.assertEqual((year["status"], year["statuses"], len(year["sources"])),
                         ("online", ["online"], 2))
        first = self.query()[1]
        self.write_rows([self.row(), self.row(archive="Archive B", evidence_ref="local:other")],
                        columns=tuple(reversed(self.columns)))
        self.assertEqual(self.query()[1], first)

    def test_coverage_invalid_rows_rejected_even_outside_query(self):
        cases = [{field: ""} for field in self.columns]
        cases += [{"status": value} for value in ("notfound", "images", "captcha", "probably online", "ONLINE")]
        cases += [{"year_from": value} for value in ("1800.0", "+1800", " 1800", "١٨٠٠", "999", "2101", "1802")]
        cases += [{"year_to": "2101"}, {"year_to": "1799"}, {"checked": "2023-02-29"},
                  {"checked": "2020-2-29"}, {"checked": "20200229"},
                  {"checked": "2020-01-01T00:00:00"}, {"evidence_ref": "  "}]
        for changes in cases:
            with self.subTest(changes=changes):
                self.write_rows([self.row(**({"parish": "Unrelated"} | changes))])
                code, out, err = self.query()
                self.assertEqual((code, out), (2, ""))
                self.assertIn("gwt-access:", err)

    def test_coverage_bad_structure_missing_input_and_bounds(self):
        self.assertEqual(self.query()[0:2], (2, ""))
        self.assertFalse(os.path.exists(self.tsv))
        for text in ("archive\tparish\nA\tP\n", "\t".join(self.columns + ("archive",)) + "\n",
                     "\t".join(self.columns) + "\nA\tP\n",
                     "\t".join(self.columns) + "\n" + "\t".join(self.row().values()) + "\textra\n",
                     "\t".join(self.columns) + '\n"unterminated'):
            with open(self.tsv, "w", encoding="utf-8") as fh:
                fh.write(text)
            self.assertEqual(self.query()[0:2], (2, ""))
        self.write_rows([])
        for extra in (("--year-from", "1804"), ("--year-from", "999"),
                      ("--year-to", "2101"), ("--year-from", "1000", "--year-to", "1500"),
                      ("--year-from", "+1800"), ("--year-to", "1800.0"), ("--parish", " ")):
            with self.subTest(extra=extra):
                self.assertEqual(self.query(*extra)[0:2], (2, ""))
        self.assertEqual(self.run_("coverage")[0], 2)
        code, out, _ = self.query("--year-from", "1000", "--year-to", "1499")
        self.assertEqual(code, 0)
        self.assertEqual(len(json.loads(out)["years"]), 500)

    def test_coverage_function_validation_and_no_legacy_map_io(self):
        self.write_rows([self.row()])
        before = Path(self.tsv).read_bytes()
        with patch.object(A, "load", side_effect=AssertionError("legacy map read")), \
             patch.object(A, "save", side_effect=AssertionError("write")):
            self.assertEqual(self.query()[0], 0)
        report = A.coverage_report(self.tsv, "Saint Test", "baptisms", 1801, 1801)
        self.assertEqual([y["status"] for y in report["years"]], ["online"])
        for lo, hi in ((True, 1801), (1800.0, 1801), (1800, "1801"), (1801, 1800), (1000, 1500)):
            with self.subTest(bounds=(lo, hi)), self.assertRaises(ValueError):
                A.coverage_report(self.tsv, "Saint Test", "baptisms", lo, hi)
        self.assertEqual(Path(self.tsv).read_bytes(), before)
        self.assertFalse(os.path.exists(self.m))

    def test_coverage_actual_cli_is_read_only_and_stable(self):
        self.write_rows([self.row()])
        before = {name: Path(self.d.name, name).read_bytes() for name in os.listdir(self.d.name)}
        args = [sys.executable, "-B", A.__file__, "--map", self.m, "coverage", self.tsv,
                "--parish", "Saint Test", "--type", "baptisms", "--year-from", "1800", "--year-to", "1801"]
        result = subprocess.run(args, capture_output=True, text=True, cwd=self.d.name)
        self.assertEqual((result.returncode, result.stderr), (0, ""))
        self.assertEqual(json.loads(result.stdout), A.coverage_report(self.tsv, "Saint Test", "baptisms", 1800, 1801))
        self.assertEqual(subprocess.run(args, capture_output=True, text=True, cwd=self.d.name).stdout, result.stdout)
        missing_args = args.copy()
        missing_args[6] = os.path.join(self.d.name, "missing.tsv")
        missing = subprocess.run(missing_args, capture_output=True, text=True, cwd=self.d.name)
        self.assertEqual((missing.returncode, missing.stdout), (2, ""))
        self.assertIn("gwt-access:", missing.stderr)
        self.assertEqual({name: Path(self.d.name, name).read_bytes() for name in os.listdir(self.d.name)}, before)


if __name__ == "__main__":
    unittest.main()
