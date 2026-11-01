"""Offline contract tests: every identifier and reader below is invented."""
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

import gwt_ledger as L


class ReadingIndexTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'shared.tsv'

    def cli(self, *args, lanes=''):
        out = io.StringIO()
        with redirect_stdout(out):
            code = L.main(['--ledger', str(self.path), '--ledger-dir', lanes, *args])
        self.assertEqual(code, 0)
        return out.getvalue()

    def mark(self, start=1, end=15, reader='ReaderA', ark='ark:/fake/register-A', result='negative', lanes=''):
        self.cli('mark', '--ark', ark, '--image-from', str(start), '--image-to', str(end),
                 '--reader', reader, '--date', '2026-11-01', '--result', result,
                 '--ref', 'invented-note.txt', lanes=lanes)

    def query(self, *args, ark='ark:/fake/register-A', lanes=''):
        return json.loads(self.cli('query', '--ark', ark, *args, lanes=lanes))

    def test_holes_and_exact_source(self):
        self.mark()
        self.mark(17, 19, result='found')
        self.mark(16, 30, ark='ark:/fake/register-B')
        q = self.query('--total', '30')
        self.assertEqual(q['covered'], [[1, 15], [17, 19]])
        self.assertEqual(q['unread'], [[16, 16], [20, 30]])
        self.assertEqual(q['total'], 30)
        self.assertFalse(q['unknown_tail'])
        self.assertEqual(len(q['rows']), 2)

    def test_independent_readings_preserved_with_overlap(self):
        self.mark(5, 10)
        self.mark(8, 12, reader='ReaderB', result='partial')
        q = self.query()
        self.assertEqual(q['double_read'], [[8, 10]])
        self.assertEqual(q['covered'], [[5, 12]])
        self.assertEqual(q['unread'], [[1, 4]])
        self.assertIsNone(q['total'])
        self.assertTrue(q['unknown_tail'])
        self.assertEqual([(r['reader'], r['image_from'], r['image_to'], r['result']) for r in q['rows']],
                         [('ReaderA', 5, 10, 'negative'), ('ReaderB', 8, 12, 'partial')])
        self.assertEqual(q['rows'][0]['ref'], 'invented-note.txt')
        self.assertEqual(q['rows'][0]['date'], '2026-11-01')
        self.mark(6, 9)  # the same reader twice is not a second independent reader
        self.assertEqual(self.query()['double_read'], [[8, 10]])

    def test_empty_unknown_tail_and_large_ranges(self):
        self.assertEqual(self.query()['unread'], [])
        self.assertTrue(self.query()['unknown_tail'])
        self.assertEqual(self.query('--total', '30')['unread'], [[1, 30]])
        self.mark(1, 10**12)
        self.assertEqual(self.query()['covered'], [[1, 10**12]])

    def test_invalid_marks_exit_2_without_changing_file(self):
        self.mark()
        before = self.path.read_bytes()
        for flag, value in [('--image-from', '0'), ('--image-to', '0'), ('--image-from', '16'),
                            ('--result', 'maybe'), ('--date', '2026-02-30'), ('--reader', '  '),
                            ('--ark', ''), ('--image-to', '-1')]:
            args = ['mark', '--ark', 'ark:/fake/register-A', '--image-from', '1', '--image-to', '15',
                    '--reader', 'ReaderA', '--date', '2026-11-01', '--result', 'negative', '--ref', 'fake']
            args[args.index(flag) + 1] = value
            with self.subTest(flag=flag, value=value), redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as exc:
                    self.cli(*args)
                self.assertEqual(exc.exception.code, 2)
                self.assertEqual(self.path.read_bytes(), before)

    def test_total_cannot_hide_logged_images(self):
        self.mark()
        for total in ('0', '-1', '14'):
            with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as exc:
                self.query('--total', total)
            self.assertEqual(exc.exception.code, 2)

    def test_legacy_rows_and_lane_files_share_one_index(self):
        self.path.write_text('\t'.join(L.COLUMNS[:-1]) + '\n' +
                             'fake\tFake\tfake\tfake query\t2026-01-01\tnone\t\tlane-old\n')
        self.mark()
        lanes = str(Path(self.tmp.name) / 'lanes')
        self.mark(17, 19, reader='ReaderB', lanes=lanes)
        self.cli('add', '--person', 'fake2', '--source', 'fake', '--query', 'new query', '--result', 'hit')
        self.assertEqual(self.query('--total', '30', lanes=lanes)['unread'], [[16, 16], [20, 30]])
        self.assertIn('rows\t2', self.cli('stats', lanes=lanes))
        self.assertIn('new query', self.cli('check', '--query', 'new query'))
        self.assertEqual(len(L.read_rows(str(self.path))), 3)
        self.assertEqual(len(L.find(L.read_rows(str(self.path)))), 2)
        self.assertTrue((Path(lanes) / 'readerb.tsv').exists())

    def test_invalid_mark_creates_no_ledger_or_lock(self):
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as exc:
            self.mark(0, 15)
        self.assertEqual(exc.exception.code, 2)
        self.assertEqual(list(self.path.parent.iterdir()), [])

    def test_launcher_help_and_query(self):
        launcher = str(Path(L.__file__).with_name('gwt-ledger'))
        help_run = subprocess.run([sys.executable, launcher, 'query', '--help'], capture_output=True, text=True)
        self.assertEqual(help_run.returncode, 0)
        self.assertIn('--total', help_run.stdout)
        self.assertIn('unknown', help_run.stdout)
        self.mark()
        result = subprocess.run([sys.executable, launcher, '--ledger', str(self.path), '--ledger-dir', '',
                                 'query', '--ark', 'ark:/fake/register-A'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)['unknown_tail'])

    def test_failed_replace_leaves_old_file_and_no_temp(self):
        self.mark()
        before = self.path.read_bytes()
        with patch.object(L.os, 'replace', side_effect=OSError('invented write failure')):
            with redirect_stderr(io.StringIO()):
                code = L.main(['--ledger', str(self.path), 'mark', '--ark', 'ark:/fake/register-A',
                               '--image-from', '16', '--image-to', '19', '--reader', 'ReaderB',
                               '--result', 'negative', '--ref', 'fake'])
        self.assertEqual(code, 2)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(sorted(p.name for p in self.path.parent.iterdir()), ['shared.tsv', 'shared.tsv.lock'])

    def test_both_writers_hold_sidecar_lock_through_read_and_replace(self):
        real_flock, real_read, real_replace = L.fcntl.flock, L.read_rows, L.os.replace
        held = []

        def flock(file, operation):
            self.assertEqual(file.name, str(self.path) + '.lock')
            if operation == L.fcntl.LOCK_EX:
                held.append(True)
            elif operation == L.fcntl.LOCK_UN:
                held.pop()
            real_flock(file, operation)

        def read(path):
            self.assertEqual(held, [True], 'read-modify-write must start under the lock')
            return real_read(path)

        def replace(src, dst):
            self.assertEqual(held, [True], 'lock must survive the rename')
            self.assertEqual(Path(src).parent, self.path.parent)
            real_replace(src, dst)

        with patch.object(L.fcntl, 'flock', side_effect=flock), patch.object(L, 'read_rows', side_effect=read), \
                patch.object(L.os, 'replace', side_effect=replace):
            self.mark()
            self.cli('add', '--person', 'fake', '--source', 'fake', '--query', 'fake', '--result', 'none')
        self.assertEqual(held, [])

    def test_concurrent_search_and_reading_writers_preserve_all_rows(self):
        script = '''import sys
import gwt_ledger as L
for i in range(12):
    if int(sys.argv[2]) % 2:
        L.main(['--ledger', sys.argv[1], 'add', '--person', 'fake', '--source', 'fake', '--query', str(i), '--result', 'none'])
    else:
        L.main(['--ledger', sys.argv[1], 'mark', '--ark', 'ark:/fake/register-A', '--image-from', str(i+1), '--image-to', str(i+1), '--reader', 'Reader'+sys.argv[2], '--result', 'negative', '--ref', 'fake'])
'''
        procs = [subprocess.Popen([sys.executable, '-c', script, str(self.path), str(i)],
                                  env={**os.environ, 'PYTHONPATH': str(Path(L.__file__).parent)}) for i in range(8)]
        self.assertEqual([p.wait(timeout=30) for p in procs], [0]*8)
        self.assertEqual(len(L.read_rows(str(self.path))), 96)
        q = self.query()
        self.assertEqual(len(q['rows']), 48)
        self.assertEqual(q['double_read'], [[1, 12]])
        self.assertIn('rows\t48', self.cli('stats'))


if __name__ == '__main__':
    unittest.main()
