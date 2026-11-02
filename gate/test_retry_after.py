"""Synthetic tests for gwt_retry_after (offline, temp dirs only)."""
import os
import stat
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gwt_retry_after as ra  # noqa: E402

NOW = 1_800_000_000.0  # synthetic
DATE_NOW = "Fri, 15 Jan 2027 08:00:00 GMT"
DATE_NOW_EPOCH = 1_799_999_999.0 + 1  # placeholder replaced below


def _epoch_of(s):
    return ra._epoch(ra._parse_date(s))


class ParseTests(unittest.TestCase):
    def test_delta(self):
        r = ra.parse_retry_after("120", NOW)
        self.assertTrue(r.ok)
        self.assertEqual((r.delay_seconds, r.kind), (120.0, "delta"))

    def test_delta_whitespace_and_zero(self):
        self.assertEqual(ra.parse_retry_after(" 0 ", NOW).delay_seconds, 0.0)

    def test_malformed(self):
        for v in [None, 5, "", "  ", "abc", "1.5", "1e3", "0x10", "١٢٣",
                  "12 34", "x" * 500, "Not a date", "99999999999999"[:0] + "+5"]:
            self.assertFalse(ra.parse_retry_after(v, NOW).ok, v)

    def test_negative(self):
        r = ra.parse_retry_after("-5", NOW)
        self.assertFalse(r.ok)
        self.assertEqual(r.reason, "negative delta")

    def test_extreme_clamped(self):
        r = ra.parse_retry_after("999999999999", NOW)
        self.assertTrue(r.ok and r.clamped)
        self.assertEqual(r.delay_seconds, ra.MAX_DELAY_SECONDS)

    def test_too_many_digits_rejected(self):
        self.assertFalse(ra.parse_retry_after("1" * 40, NOW).ok)

    def test_date_future(self):
        base = _epoch_of(DATE_NOW)
        r = ra.parse_retry_after(DATE_NOW, base - 90)
        self.assertEqual((r.ok, r.kind, r.delay_seconds), (True, "date", 90.0))

    def test_date_slightly_past_is_zero(self):
        base = _epoch_of(DATE_NOW)
        r = ra.parse_retry_after(DATE_NOW, base + 30)
        self.assertTrue(r.ok)
        self.assertEqual(r.delay_seconds, 0.0)

    def test_date_far_past_rejected(self):
        base = _epoch_of(DATE_NOW)
        self.assertFalse(ra.parse_retry_after(DATE_NOW, base + 86400).ok)

    def test_date_far_future_clamped(self):
        base = _epoch_of(DATE_NOW)
        r = ra.parse_retry_after(DATE_NOW, base - 10 * 86400)
        self.assertTrue(r.clamped)
        self.assertEqual(r.delay_seconds, ra.MAX_DELAY_SECONDS)

    def test_clock_skew_uses_server_date(self):
        base = _epoch_of(DATE_NOW)
        # local clock is 2 hours fast; server says it is DATE_NOW minus 60s
        server = "Fri, 15 Jan 2027 07:59:00 GMT"
        r = ra.parse_retry_after(DATE_NOW, base + 7200, server_date=server)
        self.assertEqual(r.delay_seconds, 60.0)

    def test_bad_server_date(self):
        self.assertFalse(
            ra.parse_retry_after(DATE_NOW, NOW, server_date="garbage").ok)

    def test_absurd_date_year(self):
        self.assertFalse(
            ra.parse_retry_after("Mon, 01 Jan 99999 00:00:00 GMT", NOW).ok)


class ProposalTests(unittest.TestCase):
    def test_propose(self):
        p = ra.propose_block("Example.COM.", "host", "30", NOW)
        self.assertEqual((p.host, p.scope, p.blocked_until),
                         ("example.com", "host", NOW + 30))
        self.assertTrue(ra.is_blocked(p, NOW + 29))
        self.assertFalse(ra.is_blocked(p, NOW + 30))

    def test_none_on_unusable(self):
        self.assertIsNone(ra.propose_block("a.test", "host", "-1", NOW))
        self.assertIsNone(ra.propose_block("a.test", "host", "0", NOW))

    def test_invalid_host_scope(self):
        for h in ["", "a b", "a..b", "-x", "http://x", None]:
            with self.assertRaises(ra.RetryAfterError):
                ra.propose_block(h, "host", "1", NOW)
        for s in ["", None, "a b", "x" * 200]:
            with self.assertRaises(ra.RetryAfterError):
                ra.propose_block("a.test", s, "1", NOW)

    def test_merge(self):
        a = ra.propose_block("a.test", "host", "10", NOW)
        b = ra.propose_block("a.test", "host", "50", NOW)
        self.assertIs(ra.merge_proposals(a, b), b)
        self.assertIs(ra.merge_proposals(None, a), a)
        self.assertIsNone(ra.merge_proposals(None, None))
        c = ra.propose_block("a.test", "other", "10", NOW)
        with self.assertRaises(ra.RetryAfterError):
            ra.merge_proposals(a, c)


class PersistTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.path = os.path.join(self._td.name, "scratch.json")
        self.p = ra.propose_block("a.test", "host", "30", NOW)

    def test_create_mode_and_roundtrip(self):
        rev = ra.persist_proposal(self.p, self.path)
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o600)
        self.assertEqual(ra.read_revision(self.path), rev)
        self.assertEqual(ra.load_proposal(self.path), self.p)
        self.assertEqual(sorted(os.listdir(self._td.name)), ["scratch.json"])

    def test_create_conflict_if_exists(self):
        ra.persist_proposal(self.p, self.path)
        with self.assertRaises(ra.ConflictError):
            ra.persist_proposal(self.p, self.path)

    def test_update_with_revision(self):
        rev = ra.persist_proposal(self.p, self.path)
        q = ra.propose_block("a.test", "host", "90", NOW)
        rev2 = ra.persist_proposal(q, self.path, rev)
        self.assertNotEqual(rev, rev2)
        self.assertEqual(ra.load_proposal(self.path), q)

    def test_stale_revision_conflict(self):
        rev = ra.persist_proposal(self.p, self.path)
        q = ra.propose_block("a.test", "host", "90", NOW)
        ra.persist_proposal(q, self.path, rev)
        with self.assertRaises(ra.ConflictError):
            ra.persist_proposal(self.p, self.path, rev)
        self.assertEqual(ra.load_proposal(self.path), q)

    def test_update_missing_file_conflict(self):
        with self.assertRaises(ra.ConflictError):
            ra.persist_proposal(self.p, self.path, "deadbeef")

    def test_lock_held_conflict(self):
        open(self.path + ".lock", "w").close()
        with self.assertRaises(ra.ConflictError):
            ra.persist_proposal(self.p, self.path)
        self.assertFalse(os.path.exists(self.path))

    def test_requires_path(self):
        with self.assertRaises(ra.RetryAfterError):
            ra.persist_proposal(self.p, "")

    def test_corrupt_load(self):
        with open(self.path, "w") as fh:
            fh.write("{nope")
        with self.assertRaises(ra.RetryAfterError):
            ra.load_proposal(self.path)

    def test_load_missing(self):
        self.assertIsNone(ra.load_proposal(self.path))


class AdversarialTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.dir = self._td.name
        self.path = os.path.join(self.dir, "scratch.json")

    def test_bool_and_nonfinite_timestamps(self):
        for bad in (True, False, float("nan"), float("inf"), None, "1"):
            self.assertFalse(ra.parse_retry_after("5", bad).ok, bad)
            with self.assertRaises(ra.RetryAfterError):
                ra.propose_block("a.test", "host", "5", bad)
        p = ra.propose_block("a.test", "host", "5", NOW)
        with self.assertRaises(ra.RetryAfterError):
            ra.is_blocked(p, float("nan"))
        for kw in ({"max_delay": True}, {"max_delay": float("nan")},
                   {"max_delay": -1}, {"skew_tolerance": float("inf")},
                   {"skew_tolerance": -1}):
            self.assertFalse(ra.parse_retry_after("5", NOW, **kw).ok, kw)

    def test_negative_large_and_skewed(self):
        for v in ("-0", "- 5", "-99999999999999999999"):
            self.assertFalse(ra.parse_retry_after(v, NOW).ok, v)
        r = ra.parse_retry_after("999999999999", NOW, max_delay=10)
        self.assertEqual((r.delay_seconds, r.clamped), (10.0, True))

    def test_host_validation(self):
        for h in ("examp_le.com", "a" * 64 + ".test", "a.-b.test", "a-.test",
                  "a...", ".", "é.test", "a..b", "x\x00.test"):
            with self.assertRaises(ra.RetryAfterError, msg=h):
                ra.normalize_host(h)
        self.assertEqual(ra.normalize_host(" A.Test. "), "a.test")

    def test_uppercase_hostname_normalization(self):
        # Letter K is a valid DNS character; uppercase must normalize, not fail.
        self.assertEqual(ra.normalize_host("exampKe.com"), "exampke.com")
        self.assertEqual(ra.normalize_host("EXAMPLE.COM"), "example.com")
        self.assertEqual(ra.normalize_host("Www.ExampKe.Com."), "www.exampke.com")

    def test_symlink_scratch_rejected(self):
        target = os.path.join(self.dir, "target.json")
        with open(target, "w") as fh:
            fh.write("keep")
        os.symlink(target, self.path)
        p = ra.propose_block("a.test", "host", "30", NOW)
        for call in (lambda: ra.persist_proposal(p, self.path),
                     lambda: ra.read_revision(self.path),
                     lambda: ra.load_proposal(self.path)):
            with self.assertRaises(ra.RetryAfterError):
                call()
        with open(target) as fh:
            self.assertEqual(fh.read(), "keep")
        self.assertTrue(os.path.islink(self.path))
        self.assertFalse(os.path.exists(self.path + ".lock"))

    def test_dangling_symlink_and_directory_not_followed(self):
        os.symlink(os.path.join(self.dir, "nowhere"), self.path)
        p = ra.propose_block("a.test", "host", "30", NOW)
        with self.assertRaises(ra.RetryAfterError):
            ra.persist_proposal(p, self.path)
        self.assertFalse(os.path.exists(os.path.join(self.dir, "nowhere")))
        d = os.path.join(self.dir, "sub")
        os.mkdir(d)
        with self.assertRaises(ra.RetryAfterError):
            ra.persist_proposal(p, d)

    def test_lock_symlink_not_followed(self):
        victim = os.path.join(self.dir, "victim")
        os.symlink(victim, self.path + ".lock")
        p = ra.propose_block("a.test", "host", "30", NOW)
        with self.assertRaises(ra.ConflictError):
            ra.persist_proposal(p, self.path)
        self.assertFalse(os.path.exists(victim))

    def test_dotdot_path_keeps_tmp_beside_target(self):
        os.mkdir(os.path.join(self.dir, "real"))
        os.symlink(os.path.join(self.dir, "real"), os.path.join(self.dir, "ln"))
        path = os.path.join(self.dir, "ln", "..", "ln", "s.json")
        p = ra.propose_block("a.test", "host", "30", NOW)
        ra.persist_proposal(p, path)
        self.assertEqual(os.listdir(os.path.join(self.dir, "real")), ["s.json"])
        self.assertEqual(sorted(os.listdir(self.dir)), ["ln", "real"])

    def test_update_never_shortens_block(self):
        long_p = ra.propose_block("a.test", "host", "900", NOW)
        short_p = ra.propose_block("a.test", "host", "10", NOW)
        rev = ra.persist_proposal(long_p, self.path)
        rev2 = ra.persist_proposal(short_p, self.path, rev)
        self.assertEqual(ra.load_proposal(self.path), long_p)
        self.assertEqual(ra.read_revision(self.path), rev2)

    def test_update_other_scope_replaces(self):
        a = ra.propose_block("a.test", "host", "900", NOW)
        b = ra.propose_block("a.test", "other", "10", NOW)
        rev = ra.persist_proposal(a, self.path)
        ra.persist_proposal(b, self.path, rev)
        self.assertEqual(ra.load_proposal(self.path), b)

    def test_two_writers_same_revision_one_loses(self):
        base = ra.propose_block("a.test", "host", "10", NOW)
        rev = ra.persist_proposal(base, self.path)
        w1 = ra.propose_block("a.test", "host", "20", NOW)
        w2 = ra.propose_block("a.test", "host", "30", NOW)
        ra.persist_proposal(w1, self.path, rev)
        with self.assertRaises(ra.ConflictError):
            ra.persist_proposal(w2, self.path, rev)
        self.assertEqual(ra.load_proposal(self.path), w1)

    def test_no_leftovers_and_0600_after_update_and_conflict(self):
        p = ra.propose_block("a.test", "host", "30", NOW)
        rev = ra.persist_proposal(p, self.path)
        os.chmod(self.path, 0o644)
        ra.persist_proposal(p, self.path, rev)
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o600)
        with self.assertRaises(ra.ConflictError):
            ra.persist_proposal(p, self.path, "bad")
        self.assertEqual(os.listdir(self.dir), ["scratch.json"])

    def test_delta_digit_limit_boundary(self):
        self.assertTrue(ra.parse_retry_after("1" * 12, NOW).ok)
        self.assertFalse(ra.parse_retry_after("1" * 13, NOW).ok)
        self.assertFalse(ra.parse_retry_after("0" * 13, NOW).ok)

    def test_clamp_boundary_is_not_clamped(self):
        r = ra.parse_retry_after("100", NOW, max_delay=100)
        self.assertEqual((r.delay_seconds, r.clamped), (100.0, False))
        r = ra.parse_retry_after("101", NOW, max_delay=100)
        self.assertEqual((r.delay_seconds, r.clamped), (100.0, True))
        base = _epoch_of(DATE_NOW)
        r = ra.parse_retry_after(DATE_NOW, base - 100, max_delay=100)
        self.assertEqual((r.delay_seconds, r.clamped), (100.0, False))

    def test_skew_tolerance_boundary_inclusive(self):
        base = _epoch_of(DATE_NOW)
        tol = ra.DEFAULT_SKEW_TOLERANCE
        r = ra.parse_retry_after(DATE_NOW, base + tol)
        self.assertEqual((r.ok, r.delay_seconds), (True, 0.0))
        self.assertFalse(ra.parse_retry_after(DATE_NOW, base + tol + 1).ok)

    def test_server_date_overrides_local_now(self):
        base = _epoch_of(DATE_NOW)
        # local clock far in the future would make the date stale; server date wins
        r = ra.parse_retry_after(DATE_NOW, base + 10 * 86400,
                                 server_date="Fri, 15 Jan 2027 07:59:30 GMT")
        self.assertEqual((r.ok, r.delay_seconds), (True, 30.0))

    def test_kelvin_sign_host_rejected_ascii_k_accepted(self):
        with self.assertRaises(ra.RetryAfterError):
            ra.normalize_host("exampKe.com")  # KELVIN SIGN, not ASCII
        self.assertEqual(ra.normalize_host("exampKe.com"), "exampke.com")

    def test_merge_tie_keeps_first(self):
        a = ra.propose_block("a.test", "host", "10", NOW)
        b = ra.propose_block("a.test", "host", "10", NOW)
        self.assertIsNot(a, b)
        self.assertIs(ra.merge_proposals(a, b), a)

    def test_load_rejects_bad_types(self):
        good = ('{"host":"a.test","scope":"host","blocked_until":%s,'
                '"delay_seconds":1,"kind":"delta","clamped":false,"created_at":1}')
        for bu in ("true", "NaN", "Infinity", '"x"'):
            with open(self.path, "w") as fh:
                fh.write(good % bu)
            with self.assertRaises(ra.RetryAfterError, msg=bu):
                ra.load_proposal(self.path)
        with open(self.path, "w") as fh:
            fh.write("[1]")
        with self.assertRaises(ra.RetryAfterError):
            ra.load_proposal(self.path)

    def test_huge_int_is_not_num_and_never_overflows(self):
        huge = 10 ** 400
        self.assertFalse(ra._is_num(huge))
        self.assertFalse(ra._is_num(-huge))
        self.assertFalse(ra.parse_retry_after("5", huge).ok)
        self.assertFalse(ra.parse_retry_after("5", NOW, max_delay=huge).ok)
        self.assertFalse(ra.parse_retry_after("5", NOW, skew_tolerance=huge).ok)
        with self.assertRaises(ra.RetryAfterError):
            ra.propose_block("a.test", "host", "5", huge)
        p = ra.propose_block("a.test", "host", "5", NOW)
        with self.assertRaises(ra.RetryAfterError):
            ra.is_blocked(p, huge)

    def _write(self, **over):
        d = {"host": "a.test", "scope": "host", "blocked_until": NOW + 30,
             "delay_seconds": 30, "kind": "delta", "clamped": False,
             "created_at": NOW}
        d.update(over)
        import json
        with open(self.path, "w") as fh:
            fh.write(json.dumps(d))

    def test_load_huge_int_is_corrupt(self):
        with open(self.path, "w") as fh:
            fh.write('{"host":"a.test","scope":"host","blocked_until":1%s,'
                     '"delay_seconds":1,"kind":"delta","clamped":false,'
                     '"created_at":1}' % ("0" * 400))
        with self.assertRaises(ra.RetryAfterError):
            ra.load_proposal(self.path)

    def test_load_uses_normalized_host(self):
        self._write(host=" A.Test. ")
        self.assertEqual(ra.load_proposal(self.path).host, "a.test")

    def test_load_rejects_negative_zero_and_incoherent(self):
        for over in ({"blocked_until": -1}, {"created_at": -5},
                     {"delay_seconds": -30}, {"delay_seconds": 0},
                     {"blocked_until": NOW + 999}, {"kind": "bogus"}):
            self._write(**over)
            with self.assertRaises(ra.RetryAfterError, msg=over):
                ra.load_proposal(self.path)

    def test_load_huge_epoch_mismatch_not_accepted(self):
        # Relative tolerance 1e-9 would accept ~1e7 s of skew at 1e16.
        big = 1e16
        self._write(created_at=big, delay_seconds=30, blocked_until=big + 64)
        with self.assertRaises(ra.RetryAfterError):
            ra.load_proposal(self.path)
        self._write(created_at=NOW, delay_seconds=30, blocked_until=NOW + 30.01)
        with self.assertRaises(ra.RetryAfterError):
            ra.load_proposal(self.path)
        self._write(created_at=NOW, delay_seconds=30, blocked_until=NOW + 30.0005)
        self.assertEqual(ra.load_proposal(self.path).delay_seconds, 30)

    def test_load_zero_delay_coherent_is_rejected(self):
        self._write(delay_seconds=0, blocked_until=NOW)
        with self.assertRaises(ra.RetryAfterError):
            ra.load_proposal(self.path)

    def test_corrupt_scratch_not_auto_reset(self):
        with open(self.path, "w") as fh:
            fh.write("{nope")
        p = ra.propose_block("a.test", "host", "30", NOW)
        with self.assertRaises(ra.RetryAfterError):
            ra.load_proposal(self.path)
        with self.assertRaises(ra.ConflictError):
            ra.persist_proposal(p, self.path)
        with open(self.path) as fh:
            self.assertEqual(fh.read(), "{nope")

    def test_stale_lock_fails_closed_and_is_kept(self):
        open(self.path + ".lock", "w").close()
        p = ra.propose_block("a.test", "host", "30", NOW)
        with self.assertRaises(ra.ConflictError):
            ra.persist_proposal(p, self.path)
        self.assertTrue(os.path.exists(self.path + ".lock"))


class CliTests(unittest.TestCase):
    def run_cli(self, *argv):
        import contextlib
        import io
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = ra.main(list(argv))
            except SystemExit as exc:
                code = exc.code
        return code, out.getvalue(), err.getvalue()

    def base(self, value="120", **over):
        a = {"--value": value, "--now": str(NOW), "--host": "Example.com",
             "--scope": "api"}
        a.update(over)
        return [x for kv in a.items() for x in kv]

    def test_help_exits_zero(self):
        code, out, _ = self.run_cli("--help")
        self.assertEqual(code, 0)
        self.assertIn("gwt-retry-after", out)

    def test_proposal_json(self):
        import json
        code, out, _ = self.run_cli(*self.base())
        self.assertEqual(code, 0)
        p = json.loads(out)["proposal"]
        self.assertEqual((p["host"], p["delay_seconds"], p["blocked_until"]),
                         ("example.com", 120.0, NOW + 120))

    def test_server_date(self):
        import json
        base = _epoch_of(DATE_NOW)
        code, out, _ = self.run_cli(*self.base(
            DATE_NOW, **{"--now": str(base + 1000), "--server-date": DATE_NOW}))
        self.assertEqual(code, 0)
        self.assertIsNone(json.loads(out)["proposal"])

    def test_invalid_value_nonzero(self):
        for v in ("abc", "-5", ""):
            code, _out, _ = self.run_cli(*self.base(v))
            self.assertEqual(code, 1, v)

    def test_invalid_input_exit_2(self):
        for argv in (self.base(**{"--now": "nan"}),
                     self.base(**{"--now": "x"}),
                     self.base(**{"--host": "bad host"}),
                     self.base(**{"--scope": ""}),
                     ["--value", "1"]):
            code, out, _ = self.run_cli(*argv)
            self.assertEqual(code, 2, argv)
            self.assertEqual(out, "")

    def test_no_files_written(self):
        with tempfile.TemporaryDirectory() as d:
            cwd = os.getcwd()
            os.chdir(d)
            try:
                self.run_cli(*self.base())
                self.assertEqual(os.listdir(d), [])
            finally:
                os.chdir(cwd)


if __name__ == "__main__":
    unittest.main()
