import contextlib, hashlib, io, json, os, sys, tempfile, unittest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gwt_act_packet as P

# Every person, reader and image below is invented; image bytes are dummy data.
HEAD = ['person', 'date', 'type', 'image', 'crop', 'reading_1', 'reader_1', 'reading_2', 'reader_2',
        'review', 'reviewer']
IMG, CROP = b'synthetic-image-bytes', b'synthetic-crop-bytes'


def row(**kw):
    d = {'person': 'I0001', 'date': '1850-03-02', 'type': 'baptism', 'image': 'img/a.jpg',
         'crop': 'img/a-crop.png', 'reading_1': 'Juan Prueba', 'reader_1': 'alice',
         'reading_2': 'Juan  prueba', 'reader_2': 'bob', 'review': '', 'reviewer': ''}
    d.update(kw)
    return d


class T(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        self.root = os.path.join(self.td.name, 'root')
        os.makedirs(os.path.join(self.root, 'img'))
        for n, b in (('a.jpg', IMG), ('a-crop.png', CROP)):
            with open(os.path.join(self.root, 'img', n), 'wb') as f:
                f.write(b)
        self.inp = os.path.join(self.td.name, 'in.tsv')
        self.ot = os.path.join(self.td.name, 'out.tsv')
        self.oj = os.path.join(self.td.name, 'out.json')

    def run_cli(self, rows, head=HEAD):
        with open(self.inp, 'w', encoding='utf-8') as f:
            f.write('\t'.join(head) + '\n')
            for r in rows:
                f.write('\t'.join(r.get(h, '') for h in head) + '\n')
        return P.main([self.inp, '--root', self.root, '--out-tsv', self.ot, '--out-json', self.oj])

    def manifest(self):
        with open(self.oj, encoding='utf-8') as f:
            return json.load(f)

    def test_agree_pending_with_hashes(self):
        self.assertEqual(self.run_cli([row()]), 0)
        a = self.manifest()['acts'][0]
        self.assertEqual(a['image_sha256'], hashlib.sha256(IMG).hexdigest())
        self.assertEqual(a['crop_sha256'], hashlib.sha256(CROP).hexdigest())
        self.assertEqual(a['agreement'], 'agree')
        self.assertEqual(a['status'], 'pending_review')
        self.assertEqual(self.manifest()['auto_promoted'], 0)
        with open(self.ot, encoding='utf-8') as f:
            self.assertEqual(f.readline().rstrip('\n').split('\t'), P.TSV_COLUMNS)

    def test_conflict_and_uncertain_preserved(self):
        self.assertEqual(self.run_cli([row(reading_1='Juan Pr[?]eba', reading_2='Joan Prueba')]), 0)
        a = self.manifest()['acts'][0]
        self.assertEqual(a['agreement'], 'conflict')
        self.assertTrue(a['uncertain'])
        self.assertEqual(a['reading_1'], 'Juan Pr[?]eba')

    def test_approval_cannot_override_conflict_or_uncertain(self):
        self.assertEqual(self.run_cli([row(reading_2='Joan Prueba', review='approved', reviewer='carol')]), 0)
        a = self.manifest()['acts'][0]
        self.assertEqual(a['status'], 'pending_review')
        self.assertEqual(a['agreement'], 'conflict')
        self.assertIn('approval_ignored_conflict_or_uncertain', a['warnings'])
        self.assertEqual(self.run_cli([row(reading_1='Juan [?]', reading_2='Juan [?]',
                                           review='approved', reviewer='carol')]), 0)
        a = self.manifest()['acts'][0]
        self.assertEqual(a['status'], 'pending_review')
        self.assertTrue(a['uncertain'])

    def test_approval_never_promotes(self):
        self.assertEqual(self.run_cli([row(review='approved', reviewer='carol'),
                                       row(person='I0002', review='rejected', reviewer='carol')]), 0)
        m = self.manifest()
        self.assertEqual({a['status'] for a in m['acts']}, {'pending_review'})
        self.assertEqual(m['status_counts'], {'pending_review': 2})
        self.assertEqual(m['auto_promoted'], 0)
        self.assertEqual(m['auto_verified'], 0)
        self.assertEqual(m['mode'], 'writer_review_only')
        self.assertEqual(m['acts'][0]['review'], 'approved')
        with open(self.ot, encoding='utf-8') as f:
            self.assertNotIn('promotable', f.read())

    def test_arbitrary_bytes_as_crop_flagged_not_verified(self):
        with open(os.path.join(self.root, 'img', 'real.png'), 'wb') as f:
            f.write(b'\x89PNG\r\n\x1a\n' + b'dummy')
        self.assertEqual(self.run_cli([row(crop='img/real.png')]), 0)
        a = self.manifest()['acts'][0]
        self.assertEqual(a['crop_signature'], 'png-signature')
        self.assertEqual(a['image_signature'], 'unrecognized')
        self.assertEqual(self.run_cli([row()]), 0)
        a = self.manifest()['acts'][0]
        self.assertEqual(a['crop_signature'], 'unrecognized')
        self.assertIn('crop_signature_unrecognized', a['warnings'])
        self.assertEqual(a['status'], 'pending_review')

    def test_crop_same_file_or_bytes_flagged(self):
        self.assertEqual(self.run_cli([row(crop='img/a.jpg')]), 1)
        with open(os.path.join(self.root, 'img', 'copy.png'), 'wb') as f:
            f.write(IMG)
        self.assertEqual(self.run_cli([row(crop='img/copy.png')]), 0)
        self.assertIn('crop_bytes_identical_to_image', self.manifest()['acts'][0]['warnings'])

    def test_duplicate_reader_ids_rejected(self):
        for r2 in ('alice', 'ALICE', 'Alice'):
            self.assertEqual(self.run_cli([row(reader_2=r2)]), 1, r2)
            self.assertFalse(os.path.exists(self.oj))
        self.assertEqual(self.run_cli([row(review='approved', reviewer='BOB')]), 1)

    def test_reviewer_without_review_rejected(self):
        self.assertEqual(self.run_cli([row(reviewer='carol')]), 1)

    def test_csv_formula_injection_neutralised(self):
        evil = ['=1+1', '+SUM(A1)', '-2+3', '@cmd', ' =HYPERLINK("x")']
        for e in evil:
            self.assertEqual(self.run_cli([row(reading_1=e, reading_2=e)]), 0, e)
            with open(self.ot, encoding='utf-8') as f:
                lines = f.read().split('\n')
            cells = lines[1].split('\t')
            r1 = cells[P.TSV_COLUMNS.index('reading_1')]
            self.assertTrue(r1.startswith("'"), r1)
            self.assertEqual(self.manifest()['acts'][0]['reading_1'], e.strip())
            for c in cells:
                self.assertFalse(c.lstrip().startswith(P.FORMULA_START), c)

    def test_reviewer_must_be_distinct(self):
        self.assertEqual(self.run_cli([row(review='approved', reviewer='alice')]), 1)

    def test_rejections_write_nothing(self):
        bad = [row(image='img/missing.jpg'), row(crop=''), row(reading_2=''), row(reader_2='alice'),
               row(image='../x.jpg'), row(image='/etc/x.jpg'), row(person='bad id'),
               row(date='02/03/1850'), row(type='census'), row(review='maybe', reviewer='carol'),
               row(image='http://example.invalid/a.jpg')]
        for r in bad:
            self.assertEqual(self.run_cli([r]), 1, r)
            self.assertFalse(os.path.exists(self.ot))
            self.assertFalse(os.path.exists(self.oj))

    def test_symlink_escape_rejected(self):
        outside = os.path.join(self.td.name, 'outside.jpg')
        with open(outside, 'wb') as f:
            f.write(b'x')
        try:
            os.symlink(outside, os.path.join(self.root, 'img', 'link.jpg'))
        except (OSError, NotImplementedError):
            self.skipTest('no symlinks')
        self.assertEqual(self.run_cli([row(image='img/link.jpg')]), 1)

    def test_empty_file_and_duplicates_rejected(self):
        open(os.path.join(self.root, 'img', 'e.jpg'), 'wb').close()
        self.assertEqual(self.run_cli([row(image='img/e.jpg')]), 1)
        self.assertEqual(self.run_cli([row(), row()]), 1)

    def test_header_errors(self):
        self.assertEqual(self.run_cli([row()], head=[h for h in HEAD if h != 'crop']), 2)
        self.assertEqual(self.run_cli([row()], head=HEAD + ['extra']), 2)

    def cli(self, ot, oj):
        return P.main([self.inp, '--root', self.root, '--out-tsv', ot, '--out-json', oj])

    def test_output_may_not_overwrite_input_or_evidence(self):
        self.run_cli([row()])
        with open(self.inp, 'rb') as f:
            before = f.read()
        self.assertEqual(self.cli(self.inp, self.oj), 2)
        self.assertEqual(self.cli(self.ot, self.inp), 2)
        self.assertEqual(self.cli(self.ot, self.ot), 2)
        ev = os.path.join(self.root, 'img', 'a.jpg')
        self.assertEqual(self.cli(ev, self.oj), 2)
        with open(self.inp, 'rb') as f:
            self.assertEqual(f.read(), before)
        with open(ev, 'rb') as f:
            self.assertEqual(f.read(), IMG)

    def test_output_directory_or_missing_parent_rejected(self):
        self.run_cli([row()])
        self.assertEqual(self.cli(self.td.name, self.oj), 2)
        self.assertEqual(self.cli(os.path.join(self.td.name, 'no', 'o.tsv'), self.oj), 2)

    def test_failed_swap_restores_previous_outputs(self):
        self.assertEqual(self.run_cli([row()]), 0)
        with open(self.ot, 'rb') as f:
            old_t = f.read()
        with open(self.oj, 'rb') as f:
            old_j = f.read()
        real = os.replace
        calls = []

        def flaky(src, dst):
            calls.append(dst)
            if dst == self.oj and len(calls) == 2:
                raise OSError('simulated failure')
            return real(src, dst)
        P.os.replace = flaky
        try:
            self.assertEqual(self.run_cli([row(person='I0009')]), 2)
        finally:
            P.os.replace = real
        with open(self.ot, 'rb') as f:
            self.assertEqual(f.read(), old_t)
        with open(self.oj, 'rb') as f:
            self.assertEqual(f.read(), old_j)
        self.assertEqual([n for n in os.listdir(self.td.name) if n.endswith('.tmp')], [])

    def test_failed_first_write_leaves_nothing(self):
        real = os.replace
        P.os.replace = lambda s, d: (_ for _ in ()).throw(OSError('boom'))
        try:
            self.assertEqual(self.run_cli([row()]), 2)
        finally:
            P.os.replace = real
        self.assertFalse(os.path.exists(self.ot))
        self.assertFalse(os.path.exists(self.oj))
        self.assertEqual([n for n in os.listdir(self.td.name) if n.endswith('.tmp')], [])

    def test_invalid_calendar_and_non_ascii_dates_rejected(self):
        for d in ('1850-02-30', '1851-02-29', '1850-13-01', '1850-00-10', '0000', '1850-04-31',
                  '١٨٥٠', '１８５０-03-02'):
            self.assertEqual(self.run_cli([row(date=d)]), 1, d)
        for d in ('1850', '1850-03', '1852-02-29'):
            self.assertEqual(self.run_cli([row(date=d)]), 0, d)

    def test_more_uncertainty_marks_flagged(self):
        for m in ('Juan [unclear]', 'Juan Unclear', 'Juan…', 'Juan...', 'Juan？', 'Ju[illegible]'):
            self.assertEqual(self.run_cli([row(reading_1=m, reading_2=m)]), 0, m)
            self.assertTrue(self.manifest()['acts'][0]['uncertain'], m)
        self.assertEqual(self.run_cli([row()]), 0)
        self.assertFalse(self.manifest()['acts'][0]['uncertain'])

    def test_canonical_duplicate_evidence_rejected(self):
        self.assertEqual(self.run_cli([row(), row(image='img/./a.jpg')]), 1)
        self.assertEqual(self.run_cli([row(), row(image='img//a.jpg')]), 1)
        self.assertEqual(self.run_cli([row(crop='img/./a.jpg')]), 1)
        try:
            os.symlink(os.path.join(self.root, 'img', 'a.jpg'), os.path.join(self.root, 'img', 'l.jpg'))
        except (OSError, NotImplementedError):
            return
        self.assertEqual(self.run_cli([row(), row(image='img/l.jpg')]), 1)
        self.assertEqual(self.run_cli([row(crop='img/l.jpg')]), 1)

    def test_hash_and_signature_from_same_read(self):
        body = b'\x89PNG\r\n\x1a\n' + b'x' * 200000
        p = os.path.join(self.root, 'img', 'big.png')
        with open(p, 'wb') as f:
            f.write(body)
        self.assertEqual(P.file_facts(p), ('png-signature', hashlib.sha256(body).hexdigest()))

    def test_bom_header_accepted(self):
        self.run_cli([row()])
        with open(self.inp, 'rb') as f:
            data = f.read()
        with open(self.inp, 'wb') as f:
            f.write(b'\xef\xbb\xbf' + data)
        self.assertEqual(P.main([self.inp, '--root', self.root, '--out-tsv', self.ot,
                                 '--out-json', self.oj]), 0)

    def test_symlinked_output_to_input_or_evidence_rejected(self):
        self.run_cli([row()])
        try:
            lk = os.path.join(self.td.name, 'lk.tsv')
            os.symlink(self.inp, lk)
            lk2 = os.path.join(self.td.name, 'lk2.json')
            os.symlink(os.path.join(self.root, 'img', 'a.jpg'), lk2)
        except (OSError, NotImplementedError):
            self.skipTest('no symlinks')
        self.assertEqual(self.cli(lk, self.oj), 2)
        self.assertEqual(self.cli(self.ot, lk2), 2)

    def test_failed_second_replace_after_first_new_file_removes_it(self):
        real = os.replace
        calls = []

        def flaky(src, dst):
            calls.append(dst)
            if len(calls) == 2:
                raise OSError('simulated failure')
            return real(src, dst)
        P.os.replace = flaky
        try:
            self.assertEqual(self.run_cli([row()]), 2)
        finally:
            P.os.replace = real
        self.assertFalse(os.path.exists(self.ot))
        self.assertFalse(os.path.exists(self.oj))

    def test_one_bad_row_blocks_all(self):
        self.assertEqual(self.run_cli([row(), row(person='I0002', image='img/nope.jpg')]), 1)
        self.assertFalse(os.path.exists(self.oj))

    def leftovers(self):
        return [n for n in os.listdir(self.td.name) if n.endswith('.tmp')]

    def test_no_promotion_in_any_output(self):
        rows = [row(review='approved', reviewer='carol'), row(person='I0002', reading_2='Joan', review='approved', reviewer='carol'),
                row(person='I0003', review='rejected', reviewer='carol'), row(person='I0004')]
        self.assertEqual(self.run_cli(rows), 0)
        m = self.manifest()
        self.assertEqual(m['auto_promoted'], 0)
        self.assertEqual(m['auto_verified'], 0)
        self.assertEqual(m['status_counts'], {'pending_review': 4})
        self.assertEqual([a['status'] for a in m['acts']], ['pending_review'] * 4)
        with open(self.ot, encoding='utf-8') as f:
            lines = f.read().splitlines()[1:]
        st = P.TSV_COLUMNS.index('status')
        self.assertEqual([l.split('\t')[st] for l in lines], ['pending_review'] * 4)

    def test_provenance_hashes_paths_and_columns(self):
        self.assertEqual(self.run_cli([row()]), 0)
        a = self.manifest()['acts'][0]
        self.assertNotEqual(a['image_sha256'], a['crop_sha256'])
        self.assertEqual((a['image'], a['crop']), ('img/a.jpg', 'img/a-crop.png'))
        with open(self.ot, encoding='utf-8') as f:
            cells = f.read().splitlines()[1].split('\t')
        self.assertEqual(cells[P.TSV_COLUMNS.index('image_sha256')], hashlib.sha256(IMG).hexdigest())
        self.assertEqual(cells[P.TSV_COLUMNS.index('crop_sha256')], hashlib.sha256(CROP).hexdigest())

    def test_calendar_dates_must_match_whole_string(self):
        for d in ('1850-03-02x', '1850-03-021', '1850-3-2', '1850-03-', 'x1850', '1850-02-29'):
            self.assertEqual(self.run_cli([row(date=d)]), 1, d)
            self.assertFalse(os.path.exists(self.oj))
        self.assertFalse(P._valid_date('1850-13-01'))
        self.assertFalse(P._valid_date('1850-02-30'))
        self.assertTrue(P._valid_date('1848-02-29'))

    def test_reader_and_reviewer_independence_padded_or_cased(self):
        for kw in ({'reader_2': 'alice '}, {'reader_2': 'ALICE'}, {'review': 'approved', 'reviewer': ' Bob '},
                   {'review': 'approved', 'reviewer': 'ALICE'}):
            self.assertEqual(self.run_cli([row(**kw)]), 1, kw)
            self.assertFalse(os.path.exists(self.oj))
        self.assertEqual(self.run_cli([row(review='approved', reviewer='carol')]), 0)

    def test_same_evidence_spelled_differently_is_duplicate(self):
        self.assertEqual(self.run_cli([row(), row(image='img/../img/a.jpg')]), 1)
        self.assertEqual(self.run_cli([row(), row(image='img/x/../a.jpg')]), 1)
        self.assertEqual(self.run_cli([row(), row(person='I0002', image='img/./a.jpg')]), 0)

    def test_hardlink_and_inside_root_outputs_rejected(self):
        self.run_cli([row()])
        with open(self.inp, 'rb') as f:
            before = f.read()
        self.assertEqual(self.cli(os.path.join(self.root, 'new.tsv'), self.oj), 2)
        self.assertFalse(os.path.exists(os.path.join(self.root, 'new.tsv')))
        h_in, h_out = os.path.join(self.td.name, 'h_in.tsv'), os.path.join(self.td.name, 'h_out.json')
        try:
            os.link(self.inp, h_in)
            os.link(self.oj, h_out)
        except (OSError, NotImplementedError):
            self.skipTest('no hard links')
        self.assertEqual(self.cli(h_in, self.oj), 2)
        self.assertEqual(self.cli(self.ot, h_in), 2)
        self.assertEqual(self.cli(self.oj, h_out), 2)
        self.assertEqual(self.cli(h_out, self.oj), 2)
        with open(self.inp, 'rb') as f:
            self.assertEqual(f.read(), before)

    def test_failed_json_render_keeps_previous_outputs(self):
        self.assertEqual(self.run_cli([row()]), 0)
        with open(self.ot, 'rb') as f:
            old_t = f.read()
        with open(self.oj, 'rb') as f:
            old_j = f.read()
        real = P.render_json

        def boom(records):
            raise OSError('render failed')
        P.render_json = boom
        try:
            self.assertEqual(self.run_cli([row(person='I0009')]), 2)
        finally:
            P.render_json = real
        with open(self.ot, 'rb') as f:
            self.assertEqual(f.read(), old_t)
        with open(self.oj, 'rb') as f:
            self.assertEqual(f.read(), old_j)
        self.assertEqual(self.leftovers(), [])

    def test_failed_swap_leaves_no_temp_and_first_output_removed(self):
        real = os.replace
        calls = []

        def flaky(src, dst):
            calls.append(dst)
            if len(calls) == 2:
                raise OSError('simulated failure')
            return real(src, dst)
        P.os.replace = flaky
        try:
            self.assertEqual(self.run_cli([row()]), 2)
        finally:
            P.os.replace = real
        self.assertEqual(self.leftovers(), [])
        self.assertFalse(os.path.exists(self.ot))
        self.assertFalse(os.path.exists(self.oj))

    def run_err(self, rows):
        """Run the CLI and return (exit code, stderr text)."""
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            rc = self.run_cli(rows)
        return rc, buf.getvalue()

    def no_outputs(self):
        self.assertFalse(os.path.exists(self.ot))
        self.assertFalse(os.path.exists(self.oj))
        self.assertEqual(self.leftovers(), [])

    def test_snapshot_failure_cleans_temp_files_and_writes_nothing(self):
        real = P._snapshot

        def boom(path):
            raise PermissionError('cannot read existing output')
        P._snapshot = boom
        try:
            rc, err = self.run_err([row()])
        finally:
            P._snapshot = real
        self.assertEqual(rc, 2)
        self.assertIn('cannot read existing output', err)
        self.assertIn('outputs left as before', err)
        self.no_outputs()

    def test_rollback_failure_is_reported_not_claimed_as_before(self):
        self.assertEqual(self.run_cli([row()]), 0)
        real_replace, real_restore = os.replace, P._restore
        calls = []

        def flaky(src, dst):
            calls.append(dst)
            if len(calls) == 2:
                raise OSError('simulated swap failure')
            return real_replace(src, dst)

        def bad_restore(path, old):
            raise OSError('simulated restore failure')
        P.os.replace, P._restore = flaky, bad_restore
        try:
            rc, err = self.run_err([row(person='I0009')])
        finally:
            P.os.replace, P._restore = real_replace, real_restore
        self.assertEqual(rc, 2)
        self.assertIn('rollback incomplete', err)
        self.assertIn('simulated restore failure', err)
        self.assertNotIn('left as before', err)
        self.assertEqual(self.leftovers(), [])

    def test_csv_error_is_a_clean_rejection(self):
        rc, err = self.run_err([row(reading_1='a' * 200000, reading_2='b')])
        self.assertEqual(rc, 2)
        self.assertIn('unreadable TSV', err)
        self.no_outputs()

    def test_bidi_and_unicode_line_separators_rejected_with_reason(self):
        for ch in ('‮', '‪', '⁦', '⁩', '‏', '‎', '؜',
                   ' ', ' ', '\x85', '\x9f', '\x0b', '\x0c', '\x7f'):
            for field in ('reading_1', 'person', 'image'):
                v = 'I0001' if field == 'person' else 'Juan'
                if field == 'image':
                    v = 'img/a.jpg'
                rc, err = self.run_err([row(**{field: v + ch})])
                self.assertEqual(rc, 1, (field, ch))
                self.assertIn('character in %s' % field, err, (field, ch))
                self.assertIn('bidirectional or line-separator', err)
                self.no_outputs()

    def test_agreement_uses_casefold_and_is_not_exact(self):
        self.assertEqual(self.run_cli([row(reading_1='Calle Straße', reading_2='CALLE  STRASSE')]), 0)
        a = self.manifest()['acts'][0]
        self.assertEqual(a['agreement'], 'agree')  # casefold: documented, intentionally loose
        self.assertEqual(a['reading_1'], 'Calle Straße')  # stored verbatim
        self.assertEqual(a['reading_2'], 'CALLE  STRASSE')
        self.assertEqual(self.run_cli([row(reading_1='José', reading_2='Jose')]), 0)
        self.assertEqual(self.manifest()['acts'][0]['agreement'], 'conflict')


if __name__ == '__main__':
    unittest.main()
