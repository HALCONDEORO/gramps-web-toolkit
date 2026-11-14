"""Synthetic regressions for focused ports of GeiserX #253 and #262.

Original test coverage for this fork, based on the published bug contracts.
No tree, archive, credentials, network or existing research queue is used.
"""
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

import gwt_queue as Q


class UpstreamQueueSafetyTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'queue.tsv'

    def call(self, *args, text=''):
        stream = io.StringIO(text)
        old = sys.stdin
        sys.stdin = stream
        try:
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                code = Q.main(['--queue', str(self.path), *args])
            return code, stream.closed
        finally:
            sys.stdin = old

    def batch(self, rows):
        return self.call('add', '--jsonl', '-', text=''.join(json.dumps(r) + '\n' for r in rows))

    def snapshot(self):
        stat = self.path.stat()
        return (self.path.read_bytes(), stat.st_ino, stat.st_mtime_ns, stat.st_mode)

    def test_float_and_bool_priorities_refuse_entire_batch_without_queue(self):
        for value in (2.9, 2.0, True, False):
            with self.subTest(priority=value):
                code, _ = self.batch([{'person': 'I0'}, {'person': 'I1', 'priority': value}])
                self.assertEqual(code, 2)
                self.assertFalse(self.path.exists())

    def test_valid_integer_forms_keep_their_values(self):
        for value, expected in ((5, '5'), (0, '0'), (-3, '-3'), ('+5', '5'), ('-3', '-3'), (None, '0')):
            with self.subTest(priority=value):
                self.assertEqual(self.batch([{'person': 'I1', 'priority': value}])[0], 0)
                self.assertEqual(Q.read_file(str(self.path))[-1]['priority'], expected)

    def test_invalid_priority_types_are_refusals_not_exceptions_or_writes(self):
        for value in ('', '2.0', [], {}):
            with self.subTest(priority=value):
                self.assertEqual(self.batch([{'person': 'I1', 'priority': value}])[0], 2)
                self.assertFalse(self.path.exists())

    def test_bad_batch_preserves_existing_bytes_inode_time_and_mode(self):
        self.assertEqual(self.batch([{'person': 'I0', 'note': 'keep this row'}])[0], 0)
        self.path.chmod(0o600)
        before = self.snapshot()
        code, _ = self.batch([{'person': 'I1'}, {'person': 'I2', 'kind': 'not-a-kind'}])
        self.assertEqual(code, 2)
        self.assertEqual(self.snapshot(), before)

    def test_each_wrong_token_refusal_preserves_existing_queue(self):
        self.batch([{'person': 'I0'}])
        self.call('claim', '--lane', 'synthetic-lane')
        for command, extra in (('renew', []), ('done', ['--result', 'none']), ('fail', []), ('release', [])):
            with self.subTest(command=command):
                before = self.snapshot()
                code, _ = self.call(command, '--id', 'q1', '--token', 'wrong-token', *extra)
                self.assertEqual(code, 2)
                self.assertEqual(self.snapshot(), before)

    def test_stdin_stays_open_on_success_and_refusal(self):
        for text, expected in ((json.dumps({'person': 'I0'}) + '\n', 0), ('not JSON\n', 2)):
            with self.subTest(expected=expected):
                code, closed = self.call('add', '--jsonl', '-', text=text)
                self.assertEqual(code, expected)
                self.assertFalse(closed)

    def test_sidecar_may_exist_after_refusal_but_no_queue_tsv(self):
        self.assertEqual(self.batch([{'person': 'I0', 'kind': 'not-a-kind'}])[0], 2)
        self.assertFalse(self.path.exists())
        self.assertEqual({p.name for p in self.path.parent.iterdir()}, {'queue.tsv.lock'})

    def test_no_due_task_exit_one_still_saves_reopened_stale_claim(self):
        self.batch([{'person': 'I0'}])
        self.call('claim', '--lane', 'old-lane')
        rows = Q.read_file(str(self.path))
        rows[0]['lease_until'] = '2000-01-01T00:00:00Z'
        rows[0]['tries'] = str(Q.DEFAULT_MAX_TRIES)
        Q.write_rows(str(self.path), rows)
        self.assertEqual(self.call('claim', '--lane', 'next-lane')[0], 1)
        self.assertEqual(Q.read_file(str(self.path))[0]['status'], 'failed')


if __name__ == '__main__':
    unittest.main()
