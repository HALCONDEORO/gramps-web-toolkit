"""Storage regression tests using invented temporary ledgers only."""
import errno
import os
from pathlib import Path
import stat
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import gwt_ledger as L


class StorageTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'invented.tsv'
        self.path.write_text('\t'.join(L.COLUMNS) + '\n' +
                             'fake\tFake\tfake\told query\t2026-01-01\tnone\t\tlane\t\n')
        self.path.chmod(0o664)

    def add(self, path=None):
        L.add_row(path or self.path, {'person_key': 'new', 'query': 'new query', 'result': 'none'})

    def snapshot(self):
        s = self.path.stat()
        return (self.path.read_bytes(), s.st_ino, s.st_mode, s.st_uid, s.st_gid,
                {n: os.getxattr(self.path, n) for n in os.listxattr(self.path)})

    def assert_refused(self, operation=None):
        before = self.snapshot()
        with self.assertRaises(OSError):
            (operation or self.add)()
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(list(self.path.parent.glob('.gwt-ledger-*.tmp')), [])

    def test_normal_add_preserves_mode_owner_and_rows(self):
        before = self.path.stat()
        self.add()
        after = self.path.stat()
        self.assertEqual(stat.S_IMODE(after.st_mode), 0o664)
        self.assertEqual((after.st_uid, after.st_gid), (before.st_uid, before.st_gid))
        self.assertEqual([r['query'] for r in L.read_rows(self.path)], ['old query', 'new query'])

    def test_private_mode_never_broadened_and_new_file_private(self):
        self.path.chmod(0o640)
        self.add()
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o640)
        new = self.path.with_name('new.tsv')
        self.add(new)
        self.assertEqual(stat.S_IMODE(new.stat().st_mode), 0o600)

    def test_unknown_extended_header_refused_before_temp_creation(self):
        self.path.write_text('\t'.join(L.COLUMNS + L.READING_COLUMNS + ('extra',)) + '\n' +
                             '\t'.join(['fake', '', '', '', '', 'none', '', '', ''] + [''] * 5 + ['KEEP']) + '\n')
        with patch.object(L.tempfile, 'mkstemp', wraps=L.tempfile.mkstemp) as mk:
            self.assert_refused()
        mk.assert_not_called()
        self.assertFalse(Path(str(self.path) + '.lock').exists())

    def test_reading_cells_without_ark_are_not_dropped(self):
        self.path.write_text('\t'.join(L.COLUMNS + L.READING_COLUMNS) + '\n' +
                             '\t'.join(['fake', '', '', '', '', 'none', '', '', ''] + ['', '3', '4', 'r', 'KEEP']) + '\n')
        self.add()
        self.assertIn('KEEP', self.path.read_text())
        self.assertEqual(self.path.read_text().splitlines()[0].split('\t'), list(L.COLUMNS + L.READING_COLUMNS))

    def test_malformed_schema_and_extra_cells_refused(self):
        for content in ('person_key\tquery\nFAKE\tKEEP\n',
                        '\t'.join(L.COLUMNS) + '\n' + '\t'.join(['fake'] * 10) + '\n'):
            with self.subTest(content=content):
                self.path.write_text(content)
                self.assert_refused()

    def test_metadata_capability_errors_keep_original(self):
        for name in ('listxattr', 'getxattr', 'setxattr', 'fchmod'):
            with self.subTest(name=name):
                # A synthetic xattr ensures get/set are needed, without a real ACL dependency.
                os.setxattr(self.path, 'user.gwt_test', b'KEEP')
                before = self.snapshot()
                with patch.object(L.os, name, side_effect=OSError(errno.EPERM, 'invented capability failure')), \
                        self.assertRaises(OSError):
                    self.add()
                self.assertEqual(self.snapshot(), before)
                self.assertEqual(list(self.path.parent.glob('.gwt-ledger-*.tmp')), [])

    def test_ownership_failure_keeps_original(self):
        # Make the staged inode appear to need an ownership change; never chown a real fixture.
        real_stat = os.fstat
        def staged_stat(fd):
            values = list(real_stat(fd))
            values[4] += 1
            return os.stat_result(values)
        with patch.object(L.os, 'fstat', side_effect=staged_stat), \
                patch.object(L.os, 'fchown', side_effect=PermissionError('invented chown denial')):
            self.assert_refused()

    def test_silent_metadata_failure_is_detected(self):
        with patch.object(L.os, 'fchmod', return_value=None):
            self.assert_refused()

    def test_silent_ownership_failure_is_detected(self):
        real_stat = os.fstat
        def staged_stat(fd):
            values = list(real_stat(fd))
            values[4] += 1
            return os.stat_result(values)
        with patch.object(L.os, 'fstat', side_effect=staged_stat), \
                patch.object(L.os, 'fchown', return_value=None):
            self.assert_refused()

    def test_silent_xattr_failure_is_detected(self):
        os.setxattr(self.path, 'user.gwt_test', b'KEEP')
        with patch.object(L.os, 'setxattr', return_value=None):
            self.assert_refused()

    def test_inherited_attribute_removal_failure_keeps_original(self):
        real_list = os.listxattr
        def inherited(path):
            return real_list(path) + (['user.invented_inherited'] if isinstance(path, int) else [])
        with patch.object(L.os, 'listxattr', side_effect=inherited), \
                patch.object(L.os, 'removexattr', side_effect=PermissionError('invented removal failure')):
            self.assert_refused()

    def test_inherited_attribute_is_removed_from_replacement(self):
        removed = []
        real_list, real_get = os.listxattr, os.getxattr
        def listed(path):
            extra = ['user.invented_inherited'] if isinstance(path, int) and not removed else []
            return real_list(path) + extra
        def got(path, name, *args):
            if name == 'user.invented_inherited':
                return b'inherited'
            return real_get(path, name, *args)
        with patch.object(L.os, 'listxattr', side_effect=listed), patch.object(L.os, 'getxattr', side_effect=got), \
                patch.object(L.os, 'removexattr', side_effect=lambda fd, name: removed.append(name)):
            self.add()
        self.assertEqual(removed, ['user.invented_inherited'])

    def test_missing_acl_inspection_capability_refused(self):
        before = self.snapshot()
        with patch.object(L.os, 'listxattr', create=True):
            del L.os.listxattr
            with self.assertRaises(OSError):
                self.add()
        self.assertEqual(self.snapshot(), before)

    def test_atomic_errors_keep_original_and_metadata(self):
        for name in ('fsync', 'replace'):
            with self.subTest(name=name), patch.object(L.os, name, side_effect=OSError('invented failure')):
                self.assert_refused()

    def test_external_edit_before_replace_refused(self):
        real_fsync = os.fsync
        def external_edit(fd):
            real_fsync(fd)
            self.path.write_text('invented external update\n')
        with patch.object(L.os, 'fsync', side_effect=external_edit), self.assertRaises(OSError):
            self.add()
        self.assertEqual(self.path.read_text(), 'invented external update\n')
        self.assertEqual(list(self.path.parent.glob('.gwt-ledger-*.tmp')), [])

    def test_real_acl_and_xattr_survive_add(self):
        # Linux POSIX ACL encoding: owner, named user, group, mask, other.
        acl = struct.pack('<I', 2) + b''.join(struct.pack('<HHI', tag, perm, who) for tag, perm, who in
                    [(1, 6, 0xffffffff), (2, 4, os.getuid()), (4, 6, 0xffffffff),
                     (16, 6, 0xffffffff), (32, 4, 0xffffffff)])
        try:
            os.setxattr(self.path, 'system.posix_acl_access', acl)
        except OSError as exc:
            if exc.errno in (errno.ENOTSUP, errno.EPERM, errno.EACCES, errno.EINVAL):
                self.skipTest(f'temporary filesystem cannot install a POSIX ACL: {exc}')
            raise
        os.setxattr(self.path, 'user.gwt_test', b'KEEP')
        before = self.snapshot()
        self.add()
        after = self.snapshot()
        self.assertEqual(after[2:], before[2:])

    def test_file_symlink_refused_link_and_target_unchanged(self):
        link = self.path.with_name('alias.tsv')
        link.symlink_to(self.path.name)
        self.assert_refused(lambda: self.add(link))
        self.assertTrue(link.is_symlink())
        self.assertEqual(os.readlink(link), self.path.name)
        self.assertFalse(Path(str(link) + '.lock').exists())

    def test_dangling_symlink_refused(self):
        link = self.path.with_name('dangling.tsv')
        link.symlink_to('missing.tsv')
        with self.assertRaises(OSError):
            self.add(link)
        self.assertTrue(link.is_symlink())
        self.assertFalse(link.exists())
        self.assertFalse(Path(str(link) + '.lock').exists())

    def test_hardlink_aliases_refused_without_modification(self):
        link = self.path.with_name('hard.tsv')
        os.link(self.path, link)
        for path in (self.path, link):
            self.assert_refused(lambda path=path: self.add(path))
        self.assertEqual(link.stat().st_ino, self.path.stat().st_ino)
        self.assertFalse(Path(str(link) + '.lock').exists())

    def test_directory_alias_uses_canonical_lock_read_and_replace(self):
        alias = self.path.parent / 'directory-alias'
        alias.symlink_to(self.path.parent, target_is_directory=True)
        real_lock, real_read, real_replace = L.fcntl.flock, L.read_rows, L.os.replace
        def lock(f, op):
            self.assertEqual((os.fstat(f.fileno()).st_dev, os.fstat(f.fileno()).st_ino),
                             (Path(str(self.path) + '.lock').stat().st_dev,
                              Path(str(self.path) + '.lock').stat().st_ino))
            return real_lock(f, op)
        def read(path):
            self.assertEqual(path, str(self.path))
            return real_read(path)
        def replace(src, dst):
            self.assertEqual(dst, str(self.path))
            return real_replace(src, dst)
        with patch.object(L.fcntl, 'flock', side_effect=lock), \
                patch.object(L, 'read_rows', side_effect=read), patch.object(L.os, 'replace', side_effect=replace):
            self.add(alias / self.path.name)

    def test_directory_alias_concurrent_updates(self):
        alias = self.path.parent / 'directory-alias'
        alias.symlink_to(self.path.parent, target_is_directory=True)
        script = '''import sys
import gwt_ledger as L
for i in range(15):
    L.add_row(sys.argv[1], {'query': sys.argv[2] + ':' + str(i), 'result': 'none'})
'''
        procs = [subprocess.Popen([sys.executable, '-c', script,
                                  str((alias if i % 2 else self.path.parent) / self.path.name), str(i)],
                                 env={**os.environ, 'PYTHONPATH': str(Path(L.__file__).parent)}) for i in range(6)]
        self.addCleanup(lambda: [p.kill() for p in procs if p.poll() is None])
        self.assertEqual([p.wait(timeout=30) for p in procs], [0] * 6)
        rows = L.read_rows(self.path)
        self.assertEqual(len(rows), 91)
        self.assertEqual({r['query'] for r in rows}, {'old query'} | {f'{p}:{i}' for p in range(6) for i in range(15)})

    def test_directory_alias_parent_path_retains_filesystem_meaning(self):
        actual = self.path.parent / 'actual'
        (actual / 'child').mkdir(parents=True)
        alias = self.path.parent / 'alias'
        alias.symlink_to(actual / 'child', target_is_directory=True)
        self.add(alias / '..' / 'nested.tsv')
        self.assertTrue((actual / 'nested.tsv').is_file())
        self.assertFalse((self.path.parent / 'nested.tsv').exists())


if __name__ == '__main__':
    unittest.main()


class MetadataOwnershipCallTest(unittest.TestCase):
    def test_preserve_metadata_requests_original_owner(self):
        from types import SimpleNamespace
        original = SimpleNamespace(st_uid=101, st_gid=202, st_mode=0o100664)
        staged = SimpleNamespace(st_uid=303, st_gid=404, st_mode=0o100600)
        with patch.object(L.os, 'fstat', side_effect=[staged, original]), \
                patch.object(L.os, 'fchown') as chown, \
                patch.object(L.os, 'fchmod'), \
                patch.object(L.os, 'listxattr', return_value=[]), \
                patch.object(L, 'extended_attributes', return_value={}):
            L.preserve_metadata(99, original, {})
            self.assertEqual(chown.call_args.args, (99, 101, 202))
            self.assertEqual(chown.call_count, 1)


class SameUidContractTest(unittest.TestCase):
    def test_different_uid_refuses_before_writing_or_lock(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'ledger.tsv'
            path.write_text('KEEP')
            with patch.object(L.os, 'geteuid', return_value=os.geteuid() + 1), \
                    self.assertRaisesRegex(OSError, 'same UID'):
                L.writable_ledger(path)
            self.assertEqual(path.read_text(), 'KEEP')
            self.assertFalse(Path(str(path) + '.lock').exists())

    def test_sidecar_foreign_uid_refused_without_replacing_inode(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'ledger.tsv'
            with L.open_sidecar(str(path)):
                pass
            lock = Path(str(path) + '.lock')
            inode = lock.stat().st_ino
            with patch.object(L.os, 'geteuid', return_value=os.geteuid() + 1), \
                    self.assertRaisesRegex(OSError, 'writer UID'):
                L.open_sidecar(str(path))
            self.assertEqual(lock.stat().st_ino, inode)
