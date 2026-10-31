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


if __name__ == "__main__":
    unittest.main()
