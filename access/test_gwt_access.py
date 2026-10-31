import io, os, sys, tempfile, unittest
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


if __name__ == "__main__":
    unittest.main()
