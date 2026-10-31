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

    def test_semicolon_cp1252_file_and_surname_particles(self):
        open(self.idx, "wb").write("Nom;Prénom;Année;Commune;Cote\nDE DUPRÉ;Marie;1850;Saint-Arnoult;R9\n".encode("cp1252"))
        code, out, _ = self.run_(self.idx, "--dump", self.dump)
        self.assertEqual(code, 0)
        self.assertIn("R9", out)

    def test_utf16_with_bom(self):
        open(self.idx, "wb").write(("Nom\tPrénom\tAnnée\nDupré\tMarie\t1850\n").encode("utf-16"))
        self.assertEqual(self.run_(self.idx, "--dump", self.dump, "--min-score", "7")[0], 0)

    def test_twenty_thousand_rows_are_fast(self):
        import time
        rows = "Nom\tPrénom\tAnnée\tCommune\tCote\n" + "".join("Surname%d\tAnna\t1850\tX\tR%d\n" % (i, i) for i in range(20000))
        open(self.idx, "w").write(rows + "Dupre\tMarie\t1850\tSaint-Arnoult\tRZ\n")
        t = time.time()
        code, out, _ = self.run_(self.idx, "--dump", self.dump)
        self.assertEqual(code, 0)
        self.assertLess(time.time() - t, 10)

    def test_limit(self):
        open(self.idx, "w").write("Nom\tPrénom\nDupre\tMarie\nDupre\tMarie\n")
        self.assertEqual(len(self.run_(self.idx, "--dump", self.dump, "--limit", "1")[1].splitlines()), 1)

    def test_qualified_headers_and_phonetic_spelling(self):
        d = json.loads(json.dumps(DUMP))
        d["objects"]["people"][0]["primary_name"]["surname_list"] = [{"surname": "Vidal"}, {"surname": "Gil"}]
        json.dump(d, open(self.dump, "w"))
        open(self.idx, "w").write("ID\tNombre\tApellidos_nacimiento\tFecha_nacimiento\nX1\tMarie\tBidal Jil\t1850-01-01\n")
        code, out, _ = self.run_(self.idx, "--dump", self.dump)
        self.assertEqual(code, 0)
        self.assertIn("I1", out)
        self.assertIn("fonetico", out)

    def test_ties_list_every_person(self):
        d = json.loads(json.dumps(DUMP))
        d["objects"]["people"].append(dict(d["objects"]["people"][0], handle="z", gramps_id="I9"))
        json.dump(d, open(self.dump, "w"))
        code, out, _ = self.run_(self.idx, "--dump", self.dump)
        self.assertIn("I1,I9", out)

    def test_no_death_keeps_only_people_without_a_death_event(self):
        d = json.loads(json.dumps(DUMP))
        d["objects"]["events"].append({"handle": "e2", "type": "Death", "date": {"dateval": [1, 1, 1900, False]}})
        d["objects"]["people"][0]["event_ref_list"].append({"ref": "e2"})
        json.dump(d, open(self.dump, "w"))
        self.assertEqual(self.run_(self.idx, "--dump", self.dump, "--no-death")[0], 1)
        self.assertEqual(self.run_(self.idx, "--dump", self.dump)[0], 0)

    def test_insee_file_is_read_without_conversion_and_filtered_by_surname(self):
        def line(name, sex, birth, bplace, death, act):
            return (name.ljust(80) + sex + birth + "75056" + bplace.ljust(30) + " " * 30 + death + "75056" + act.ljust(9) + " " * 25 + "\r\n")
        ins = os.path.join(self.d.name, "deces-1950.txt")
        open(ins, "w", encoding="latin-1").write(
            line("DUPRE*MARIE ANNE/", "2", "18500101", "SAINT-ARNOULT", "19500302", "12")
            + line("ZZZZ*PAUL/", "1", "19000101", "PARIS", "19500303", "13"))
        d = json.loads(json.dumps(DUMP))
        d["objects"]["people"][0]["event_ref_list"] = [{"ref": "e1"}]
        json.dump(d, open(self.dump, "w"))
        code, out, err = self.run_(ins, "--dump", self.dump, "--no-death", "--min-score", "10")
        self.assertEqual(code, 0, err)
        self.assertIn("act 12", out)
        self.assertNotIn("ZZZZ", out)


if __name__ == "__main__":
    unittest.main()
