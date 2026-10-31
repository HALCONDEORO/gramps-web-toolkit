import io, json, os, sys, tempfile, unittest
from contextlib import redirect_stderr, redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gwt_cross as C

DUMP = {"objects": {
    "people": [
        {"handle": "a", "gramps_id": "I1", "primary_name": {"first_name": "Marie Anne", "surname_list": [{"surname": "Dupré"}]},
         "parent_family_list": [], "event_ref_list": [{"ref": "e1"}]},
        {"handle": "b", "gramps_id": "I2", "primary_name": {"first_name": "Jean", "surname_list": [{"surname": "Martin"}]},
         "parent_family_list": ["f"], "event_ref_list": []}],
    "events": [{"handle": "e1", "type": "Birth", "date": {"dateval": [1, 1, 1850, False]}, "place": "p1"}],
    "places": [{"handle": "p1", "name": {"value": "Saint-Arnoult"}}], "families": []}}

INDEX = ("Nom\tPrénoms\tAnnée\tCommune\tCote\n"
         "DUPRE\tMarie\t1851\tSaint Arnoult\tR1\n"
         "Dupre\tPaul\t1700\tParis\tR2\n"
         "Durand\tMarie\t1850\tSaint-Arnoult\tR3\n"
         "Martin\tPierre\t1800\tX\tR4\n")


class CrossTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.idx, self.dump = (os.path.join(self.d.name, n) for n in ("i.tsv", "d.json"))
        open(self.idx, "w", encoding="utf-8").write(INDEX)
        json.dump(DUMP, open(self.dump, "w"))

    def tearDown(self):
        self.d.cleanup()

    def run_(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = C.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_accents_case_and_place_punctuation_still_match_and_wrong_surname_is_dropped(self):
        code, out, _ = self.run_(self.idx, "--dump", self.dump)
        self.assertEqual(code, 0)
        lines = out.splitlines()
        self.assertEqual(len(lines), 1)
        score, person, why, ref = lines[0].split("\t")[:4]
        self.assertEqual((person, ref), ("I1", "R1"))
        self.assertEqual(int(score), 4 + 3 + 2 + 2)

    def test_min_score_and_parentless_filter(self):
        self.assertEqual(self.run_(self.idx, "--dump", self.dump, "--min-score", "4")[0], 0)
        code, out, _ = self.run_(self.idx, "--dump", self.dump, "--min-score", "1", "--only-parentless")
        self.assertNotIn("I2", out)
        code, out, _ = self.run_(self.idx, "--dump", self.dump, "--min-score", "1")
        self.assertIn("I2", out)

    def test_bad_input_exits_2_and_no_hit_exits_1(self):
        self.assertEqual(self.run_("/nonexistent", "--dump", self.dump)[0], 2)
        open(self.idx, "w").write("Foo\tBar\n1\t2\n")
        self.assertEqual(self.run_(self.idx, "--dump", self.dump)[0], 2)
        open(self.idx, "w").write("Nom\tAnnée\nZZZ\t1800\n")
        self.assertEqual(self.run_(self.idx, "--dump", self.dump)[0], 1)


if __name__ == "__main__":
    unittest.main()
