"""Direct tests for the shared binary-name policy. Offline: no network, no real git (git() is mocked),
only a throwaway temp directory for the expand() case."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gwt_rawstore_kinds as K
import gwt_rawstore as R

REQUIRED = ['.jpg', '.jpeg', '.png', '.gif', '.tif', '.tiff', '.webp', '.bmp', '.heic', '.jp2', '.pdf',
            '.zip', '.tar', '.gz', '.tgz', '.7z', '.mp3', '.m4a', '.ogg', '.wav', '.mp4', '.mov', '.djvu']


class BinaryExtPolicy(unittest.TestCase):
    def test_required_types_are_in_set(self):
        self.assertTrue(set(REQUIRED) <= K.BINARY_EXT, sorted(set(REQUIRED) - K.BINARY_EXT))

    def test_set_is_exactly_the_required_one(self):
        # Deliberate pin: adding an extension later must update this test on purpose.
        self.assertEqual(sorted(K.BINARY_EXT), sorted(REQUIRED))

    def test_all_entries_lowercase_with_dot(self):
        for e in K.BINARY_EXT:
            self.assertTrue(e.startswith('.') and e == e.lower(), e)

    def test_every_required_type_is_binary_name(self):
        for ext in REQUIRED:
            self.assertTrue(K.is_binary_name('scan' + ext), ext)
            self.assertTrue(K.is_binary_name('dir/sub/SCAN' + ext.upper()), ext)

    def test_last_suffix_only_and_directory_ignored(self):
        self.assertFalse(K.is_binary_name('page.jpg.txt'))
        self.assertTrue(K.is_binary_name('notes.txt.pdf'))
        self.assertFalse(K.is_binary_name('scans.jpg/readme'))

    def test_text_like_names_not_binary(self):
        for name in ('rawstore.tsv', 'notes.md', 'README', '.gitignore', 'jpg', '', 'a.json', 'a.svg'):
            self.assertFalse(K.is_binary_name(name), name)

    def test_module_alias_is_same_object(self):
        self.assertIs(R.BINARY_EXT, K.BINARY_EXT)


class ByteKindBinarySource(unittest.TestCase):
    """The decision is by name only: file bytes never matter for is_binary_name / expand."""

    def test_expand_directory_takes_binary_by_extension_not_content(self):
        with tempfile.TemporaryDirectory() as d:
            for name, data in (('fake.png', b'plain ascii text'), ('real.dat', b'\x00\x01\x02\xff'),
                               ('note.txt', b'hello')):
                with open(os.path.join(d, name), 'wb') as f:
                    f.write(data)
            got = [os.path.basename(p) for p in R.expand([d])]
            self.assertEqual(got, ['fake.png'])
            got_all = sorted(os.path.basename(p) for p in R.expand([d], everything=True))
            self.assertEqual(got_all, ['fake.png', 'note.txt', 'real.dat'])

    def test_expand_named_file_is_taken_regardless_of_extension(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, 'note.txt')
            with open(p, 'wb') as f:
                f.write(b'x')
            self.assertEqual(R.expand([p]), [p])


class StagingKnownTextVsBinary(unittest.TestCase):
    """check_staged with git() mocked: numstat '-' = git sees binary; digits = git sees text."""

    def run_check(self, numstat, sizes, max_kb=1, allow=()):
        out = '\0'.join(numstat) + '\0'
        calls = []

        def fake_git(*args, **kw):
            calls.append(args)
            if args[0] == 'diff':
                return out
            if args[0] == 'cat-file':
                return str(sizes[args[2][1:]])
            raise AssertionError(args)

        orig, R.git = R.git, fake_git
        logs = []
        try:
            n = R.check_staged(max_kb, list(allow), log=lambda *a, **k: logs.append(a[0]))
        finally:
            R.git = orig
        return n, logs, calls

    def test_text_named_text_content_is_ignored(self):
        n, _, calls = self.run_check(['3\t0\tnotes.txt'], {'notes.txt': 10 ** 7})
        self.assertEqual(n, 0)
        self.assertFalse([c for c in calls if c[0] == 'cat-file'])

    def test_binary_extension_refused_even_when_git_sees_text(self):
        n, logs, _ = self.run_check(['3\t0\traw/fake.png'], {'raw/fake.png': 5000})
        self.assertEqual(n, 1)
        self.assertIn('raw/fake.png', logs[0])

    def test_git_binary_with_unlisted_extension_refused(self):
        n, _, _ = self.run_check(['-\t-\traw/blob.dat'], {'raw/blob.dat': 5000})
        self.assertEqual(n, 1)

    def test_small_binary_allowed_and_boundary(self):
        n, _, _ = self.run_check(['-\t-\ta.jpg', '-\t-\tb.jpg'], {'a.jpg': 1024, 'b.jpg': 1025})
        self.assertEqual(n, 1)   # exactly max_kb*1024 is allowed; one byte over is refused

    def test_allow_glob_exempts(self):
        n, _, _ = self.run_check(['-\t-\tkeep/a.pdf'], {'keep/a.pdf': 9999}, allow=['keep/*'])
        self.assertEqual(n, 0)


class StagingExactOutput(unittest.TestCase):
    """Exact refusal text and entry filtering; log receives (message, file=stderr) so capture both."""

    def run_logged(self, numstat, sizes, max_kb=1):
        out = '\0'.join(numstat) + '\0'
        logs = []

        def fake_git(*args, **kw):
            if args[0] == 'diff':
                return out
            return str(sizes[args[2][1:]])

        orig, R.git = R.git, fake_git
        try:
            n = R.check_staged(max_kb, [], log=lambda *a, **k: logs.append((a, k)))
        finally:
            R.git = orig
        return n, logs

    def test_exact_messages_and_stderr_target(self):
        n, logs = self.run_logged(['-\t-\traw/big.pdf'], {'raw/big.pdf': 3 * 1024 + 5})
        self.assertEqual(n, 1)
        self.assertEqual(logs, [
            (('gwt-rawstore: raw/big.pdf is a 3 KB binary; binaries over 1 KB go to the raw store',),
             {'file': sys.stderr}),
            (('Run: git restore --staged -- <file> && gwt-rawstore put <file>, then commit the rawstore.tsv '
              'beside it.',), {'file': sys.stderr}),
        ])

    def test_nothing_refused_logs_nothing(self):
        n, logs = self.run_logged(['3\t0\tnotes.txt', '-\t-\tsmall.png'], {'small.png': 10})
        self.assertEqual((n, logs), (0, []))

    def test_empty_and_malformed_numstat_entries_skipped(self):
        n, logs = self.run_logged(['', 'garbage', '-\t-\t'], {})
        self.assertEqual((n, logs), (0, []))

    def test_each_refused_file_logged_then_single_hint(self):
        n, logs = self.run_logged(['-\t-\ta.jpg', '-\t-\tb.zip'], {'a.jpg': 4096, 'b.zip': 8192})
        self.assertEqual(n, 2)
        self.assertEqual(len(logs), 3)
        self.assertIn('a.jpg', logs[0][0][0])
        self.assertIn('b.zip', logs[1][0][0])
        self.assertTrue(logs[2][0][0].startswith('Run: git restore --staged'))


class ExpandEdges(unittest.TestCase):
    def test_missing_path_exits_with_exact_message(self):
        with tempfile.TemporaryDirectory() as d:
            missing = os.path.join(d, 'nope.png')
            with self.assertRaises(SystemExit) as cm:
                R.expand([missing])
            self.assertEqual(cm.exception.code, 'gwt-rawstore: no such file: ' + missing)

    def test_expand_skips_dot_git_and_recurses_sorted(self):
        with tempfile.TemporaryDirectory() as d:
            for rel in ('b.PNG', 'a.jpg', os.path.join('sub', 'c.pdf'), os.path.join('.git', 'x.png')):
                p = os.path.join(d, rel)
                os.makedirs(os.path.dirname(p), exist_ok=True)
                with open(p, 'wb') as f:
                    f.write(b'x')
            got = [os.path.relpath(p, d) for p in R.expand([d])]
            self.assertEqual(got, ['a.jpg', 'b.PNG', os.path.join('sub', 'c.pdf')])


if __name__ == '__main__':
    unittest.main()
