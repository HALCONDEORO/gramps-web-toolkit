import http.server, io, os, sys, tempfile, threading, unittest
from contextlib import redirect_stderr, redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gwt_batch as B
import gwt_queue as Q

HITS = []


class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        HITS.append(self.path)
        if self.path == "/robots.txt":
            body = b"User-agent: *\nDisallow: /private\n"
        elif self.path.startswith("/private"):
            body = b"no"
        elif "limit" in self.path:
            self.send_response(429); self.end_headers(); return
        elif "captcha" in self.path:
            body = b"<html>please solve the CAPTCHA</html>"
        else:
            body = b"<html>result</html>"
        self.send_response(200); self.end_headers(); self.wfile.write(body)


class BatchTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        cls.base = "http://127.0.0.1:%d" % cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        HITS.clear()
        self.d = tempfile.TemporaryDirectory()
        self.q = os.path.join(self.d.name, "cola.tsv")
        self.t = os.path.join(self.d.name, "t.tsv")

    def tearDown(self):
        self.d.cleanup()

    def queue(self, *rows):
        Q.write_rows(self.q, [Q.new_row([], "P%d" % i, "Ana %d" % i, source=s, query=qu, priority=pr)
                              | {"id": "q%d" % i} for i, (s, qu, pr) in enumerate(rows, 1)])

    def templates(self, *rows):
        open(self.t, "w").write("source\turl\tdelay\n" + "".join("%s\t%s\t0\n" % r for r in rows))

    def run_(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = B.main(["--queue", self.q, "--templates", self.t, *argv])
        return code, out.getvalue(), err.getvalue()

    def test_urls_are_built_by_priority_for_sources_that_have_a_template(self):
        self.queue(("a", "ana peiro 1850", 1), ("a", "luis 1900", 9), ("nope", "x", 5))
        self.templates(("a", "https://x.example/s?q={query}"))
        code, out, _ = self.run_()
        self.assertEqual(code, 0)
        self.assertEqual([l.split("\t")[0] for l in out.splitlines()], ["q2", "q1"])
        self.assertIn("q=ana+peiro+1850", out)
        self.assertEqual(HITS, [])  # printing fetches nothing

    def test_fetch_saves_pages_respects_robots_and_stops_on_a_block(self):
        self.queue(("a", "one", 9), ("a", "two", 8), ("b", "x", 7), ("c", "y", 6), ("c", "z", 5))
        self.templates(("a", self.base + "/private?q={query}"), ("b", self.base + "/ok?q={query}"),
                       ("c", self.base + "/limit?q={query}"))
        code, out, _ = self.run_("--fetch", "--out", os.path.join(self.d.name, "raw"))
        lines = {l.split("\t")[0]: l.split("\t")[2] for l in out.splitlines()}
        self.assertEqual(lines["q1"], "skipped (robots.txt)")
        self.assertEqual(lines["q3"], "ok")
        self.assertTrue(os.path.exists(os.path.join(self.d.name, "raw", "q3.html")))
        self.assertEqual(lines["q4"], "http 429")
        self.assertEqual(lines["q5"], "skipped (host blocked this run)")
        self.assertEqual(sum(1 for h in HITS if h.startswith("/limit")), 1)  # one try, then left alone
        self.assertFalse(any(h.startswith("/private") for h in HITS))

    def test_captcha_page_blocks_the_host(self):
        self.queue(("a", "captcha one", 9), ("a", "two", 8))
        self.templates(("a", self.base + "/captcha?q={query}"))
        _, out, _ = self.run_("--fetch", "--out", os.path.join(self.d.name, "raw"))
        self.assertIn("blocked (captcha", out)
        self.assertIn("skipped (host blocked", out)

    def test_blocked_is_listed_and_marked_not_confused_with_none(self):
        self.queue(("a", "captcha one", 9), ("a", "two", 8), ("b", "x", 7), ("c", "y", 6))
        self.templates(("a", self.base + "/captcha?q={query}"), ("b", self.base.replace("127.0.0.1", "localhost") + "/ok?q={query}"),
                       ("c", self.base.replace("127.0.0.1", "localhost") + "/private?q={query}"))
        raw = os.path.join(self.d.name, "raw")
        _, _, err = self.run_("--fetch", "--out", raw, "--mark")
        self.assertIn("3 task(s)", err)
        manual = open(os.path.join(raw, "manual.tsv")).read().splitlines()
        self.assertEqual(sorted(l.split("\t")[0] for l in manual[1:]), ["q1", "q2", "q4"])
        rows = {r["id"]: r for r in Q.read_file(self.q)}
        self.assertEqual((rows["q1"]["status"], rows["q1"]["result"]), ("done", "blocked"))
        self.assertEqual(rows["q2"]["result"], "blocked")
        self.assertEqual(rows["q4"]["result"], "denied")
        self.assertEqual(rows["q3"]["status"], "open")  # a page that came back is the reader's call
        self.assertIn("captcha", rows["q1"]["note"])
        self.assertIn("ok", open(os.path.join(raw, "outcomes.tsv")).read())

    def test_without_mark_the_queue_is_untouched(self):
        self.queue(("a", "captcha one", 9))
        self.templates(("a", self.base + "/captcha?q={query}"))
        self.run_("--fetch", "--out", os.path.join(self.d.name, "raw"))
        self.assertEqual(Q.read_file(self.q)[0]["status"], "open")

    def test_bad_template_exits_2_and_no_task_exits_1(self):
        self.queue(("a", "q", 1))
        open(self.t, "w").write("source\turl\na\tftp://x/{query}\n")
        self.assertEqual(self.run_()[0], 2)
        self.templates(("zzz", "https://x.example/?q={query}"))
        self.assertEqual(self.run_()[0], 1)

    def test_delay_is_kept_between_requests_to_one_host(self):
        slept = []
        t = [0.0]
        pol = B.Polite(sleep=lambda s: (slept.append(s), t.__setitem__(0, t[0] + s)), clock=lambda: t[0])
        pol.fetch(self.base + "/ok?q=1", 5)
        pol.fetch(self.base + "/ok?q=2", 5)
        self.assertEqual(len(slept), 1)
        self.assertAlmostEqual(slept[0], 5)


if __name__ == "__main__":
    unittest.main()
