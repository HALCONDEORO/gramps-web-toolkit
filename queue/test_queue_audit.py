import datetime, hashlib, io, json, os, sys, tempfile, unittest
from contextlib import redirect_stderr, redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gwt_queue as Q
import gwt_queue_audit as A

NOW = datetime.datetime(2026, 11, 2, tzinfo=datetime.timezone.utc)


def row(**kw):
    base = {c: '' for c in Q.COLUMNS}
    base.update(id='q1', person_key='p-one', kind='search', source='src-a', query='synthetic',
                priority='0', status='open', tries='0')
    base.update(kw)
    return '\t'.join(base[c] for c in Q.COLUMNS)


class AuditTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, 'q.tsv')

    def tearDown(self):
        self.dir.cleanup()

    def audit(self, *lines, header=None, stale_days=14):
        head = '\t'.join(Q.COLUMNS) if header is None else header
        with open(self.path, 'w', encoding='utf-8') as f:
            f.write('\n'.join([head, *lines]) + '\n')
        return A.audit_file(self.path, now=NOW, stale_days=stale_days)

    def codes(self, report):
        return [f['code'] for f in report['findings']]

    def test_clean_queue_has_no_findings(self):
        rep = self.audit(row(), row(id='q2', note='needs:q1'))
        self.assertEqual(rep['findings'], [])

    def test_bad_header_and_short_row(self):
        rep = self.audit('q1\tonly', header='id\tperson')
        self.assertIn('schema-header', self.codes(rep))
        self.assertIn('schema-columns', self.codes(rep))

    def test_bad_values(self):
        rep = self.audit(row(status='weird', kind='nope', priority='x', tries='1.5', next_at='soon'))
        self.assertEqual(self.codes(rep).count('schema-value'), 5)

    def test_duplicate_and_colliding_ids(self):
        rep = self.audit(row(), row(), row(id='Q2'), row(id='q2'))
        c = self.codes(rep)
        self.assertEqual(c.count('id-duplicate'), 2)
        self.assertEqual(c.count('id-collision'), 2)

    def test_missing_id_and_odd_format(self):
        rep = self.audit(row(id=''), row(id='task-9'))
        self.assertIn('id-missing', self.codes(rep))
        self.assertIn('id-format', self.codes(rep))

    def test_dedupe_key_collision_only_for_live_rows(self):
        rep = self.audit(row(dedupe_key='k'), row(id='q2', dedupe_key='k'),
                         row(id='q3', dedupe_key='k', status='failed'))
        self.assertEqual(self.codes(rep).count('dedupe-collision'), 2)

    def test_stale_blocked_and_fresh_blocked(self):
        old = row(status='done', result='blocked', done_at='2026-09-01T00:00:00Z', tries='1')
        new = row(id='q2', status='done', result='blocked', done_at='2026-11-01T00:00:00Z', tries='1')
        rep = self.audit(old, new)
        self.assertEqual([f['task_id'] for f in rep['findings'] if f['code'] == 'stale-blocked'], ['q1'])

    def test_stale_claim(self):
        rep = self.audit(row(status='claimed', lane='l', token='t', claimed_at='2026-01-01T00:00:00Z', tries='1'))
        self.assertIn('stale-claim', self.codes(rep))

    def test_impossible_done_states(self):
        rep = self.audit(row(status='done', tries='0', token='t'),
                         row(id='q2', status='done', result='hit', evidence='e', tries='1',
                             claimed_at='2026-10-01T00:00:00Z', done_at='2026-09-01T00:00:00Z'),
                         row(id='q3', status='done', result='none', tries='1', done_at='2027-01-01T00:00:00Z'))
        msgs = ' | '.join(f['message'] for f in rep['findings'] if f['code'] == 'done-state')
        for part in ('without a result', 'valid done_at', 'token', 'no recorded attempt',
                     'before claimed_at', 'in the future'):
            self.assertIn(part, msgs)

    def test_non_done_row_with_done_fields(self):
        rep = self.audit(row(result='hit', done_at='2026-10-01T00:00:00Z', token='t', lane='x'))
        self.assertEqual(self.codes(rep).count('open-state'), 3)

    def test_provenance(self):
        rep = self.audit(row(source='', person_key=''),
                         row(id='q2', status='done', result='hit', done_at='2026-10-01T00:00:00Z', tries='1'))
        self.assertEqual(self.codes(rep).count('provenance'), 3)

    def test_prereq_missing_self_and_cycle(self):
        rep = self.audit(row(note='needs:q2'), row(id='q2', note='after: q3, q1'),
                         row(id='q3', note='needs:q3 prereq:q9'), row(id='q4', note='needs:q5'),
                         row(id='q5', note='needs:q4'))
        c = self.codes(rep)
        self.assertEqual(c.count('prereq-self'), 1)
        self.assertEqual(c.count('prereq-missing'), 1)
        cyc = [f for f in rep['findings'] if f['code'] == 'prereq-cycle']
        self.assertEqual(sorted(f['task_id'] for f in cyc), ['q1', 'q2', 'q4', 'q5'])

    def test_repeated_header_lines_and_names(self):
        head = '\t'.join(Q.COLUMNS)
        rep = self.audit(head, row(), head)
        self.assertEqual(self.codes(rep).count('schema-header'), 2)
        self.assertNotIn('id-format', self.codes(rep))
        self.assertEqual(rep['rows'], 1)
        rep = self.audit(row(), header='\t'.join(Q.COLUMNS[:-1] + ('id',)))
        self.assertIn('repeated', rep['findings'][0]['message'])

    def test_extra_tabs_and_blank_lines(self):
        rep = self.audit(row() + '\textra\tmore', '', '   ', row(id='q2').replace('\t', '\t\t', 1))
        cols = [f for f in rep['findings'] if f['code'] == 'schema-columns']
        self.assertEqual(sorted(f['line'] for f in cols), [2, 5])

    def test_giant_chain_and_cycle_stay_linear(self):
        n = 3000
        chain = [row(id=f'q{int(i)}', note=f'needs:q{int(i + 1)}') for i in range(1, n)] + [row(id=f'q{int(n)}')]
        self.assertNotIn('prereq-cycle', self.codes(self.audit(*chain)))
        ring = [row(id=f'q{int(i)}', note=f'needs:q{int(i % n + 1)}') for i in range(1, n + 1)]
        rep = self.audit(*ring)
        cyc = [f for f in rep['findings'] if f['code'] == 'prereq-cycle']
        self.assertEqual(len(cyc), n)
        self.assertIn(f'({int(n)} tasks)', cyc[0]['message'])
        self.assertLess(len(cyc[0]['message']), 200)

    def test_many_duplicates_and_hostile_note_are_bounded(self):
        rep = self.audit(*[row() for _ in range(200)], row(id='q2', note='needs:' + ' ' * 50000 + '!' + 'needs:' * 2000))
        dup = [f for f in rep['findings'] if f['code'] == 'id-duplicate']
        self.assertEqual(len(dup), 200)
        self.assertLess(len(dup[0]['message']), 80)

    def test_prereq_keywords_are_not_ids(self):
        self.assertEqual(A.prereqs({'note': 'needs:q3 prereq:q9, q8 after: q1'}), ['q3', 'q9', 'q8', 'q1'])

    def test_false_completed_when_prerequisite_not_done(self):
        done = dict(status='done', result='none', tries='1', done_at='2026-10-01T00:00:00Z')
        rep = self.audit(row(id='q1', status='open'), row(id='q2', note='needs:q1', **done),
                         row(id='q3', **done), row(id='q4', note='needs:q3', **done))
        msgs = [(f['task_id'], f['message']) for f in rep['findings'] if f['code'] == 'done-state']
        self.assertEqual(msgs, [('q2', 'done while a prerequisite is not done')])

    def test_find_cycles_groups(self):
        g = {'a': {'b'}, 'b': {'c'}, 'c': {'a'}, 'd': {'a'}, 'e': {'e'}}
        self.assertEqual(A.find_cycles(g), [['a', 'b', 'c']])

    def test_private_and_source_preserved_and_not_leaked(self):
        rep = self.audit(row(source='private-archive', query='SECRETQUERY', note='private; needs:q77'))
        f = rep['findings'][0]
        self.assertTrue(f['private'])
        self.assertEqual(f['source'], 'private-archive')
        self.assertNotIn('SECRETQUERY', A.render_json(rep))
        self.assertNotIn('SECRETQUERY', A.render_tsv(rep))

    def test_every_finding_is_review_only(self):
        rep = self.audit(row(source=''), row(), row(id='q2', note='needs:q8'))
        self.assertTrue(rep['findings'])
        for f in rep['findings']:
            self.assertTrue(f['review_only'])
            self.assertTrue(f['suggested_action'].startswith('REVIEW ONLY: '))

    def test_queue_not_mutated(self):
        self.audit(row(status='claimed', lane='l', token='t', claimed_at='2026-01-01T00:00:00Z'),
                   row(id='q3'), row(id='q2'))
        with open(self.path, 'rb') as f:
            before = hashlib.sha256(f.read()).hexdigest()
        A.audit_file(self.path, now=NOW)
        with open(self.path, 'rb') as f:
            self.assertEqual(hashlib.sha256(f.read()).hexdigest(), before)
        self.assertEqual(os.listdir(self.dir.name), ['q.tsv'])

    def test_tsv_shape(self):
        rep = self.audit(row(source=''))
        lines = A.render_tsv(rep).splitlines()
        self.assertEqual(lines[0].split('\t'), list(A.TSV_COLUMNS))
        self.assertEqual(len(lines), 1 + len(rep['findings']))
        for line in lines:
            self.assertEqual(len(line.split('\t')), len(A.TSV_COLUMNS))

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = A.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_cli_exit_codes_and_outputs(self):
        self.audit(row(source=''))
        j, t = os.path.join(self.dir.name, 'r.json'), os.path.join(self.dir.name, 'r.tsv')
        code, out, _ = self.run_cli('--queue', self.path, '--json-out', j, '--tsv-out', t)
        self.assertEqual(code, 1)
        with open(j) as f:
            self.assertEqual(json.load(f)['summary']['findings'], 1)
        self.assertEqual(json.loads(out)['read_only'], True)
        self.assertTrue(open(t).read().startswith('finding\t'))
        self.audit(row())
        self.assertEqual(self.run_cli('--queue', self.path)[0], 0)

    def test_cli_missing_file_and_overwrite_refused(self):
        code, _, _err = self.run_cli('--queue', os.path.join(self.dir.name, 'none.tsv'))
        self.assertEqual(code, 2)
        self.audit(row())
        with self.assertRaises(SystemExit):
            self.run_cli('--queue', self.path, '--json-out', self.path)
        self.assertTrue(open(self.path).read().startswith('id\t'))


class MutationGuardTest(unittest.TestCase):
    """Boundary and safety cases that pin one rule each (see mutants-queue-audit.txt)."""

    setUp, tearDown, audit, codes = (AuditTest.setUp, AuditTest.tearDown, AuditTest.audit, AuditTest.codes)

    def messages(self, rep, code):
        return [f['message'] for f in rep['findings'] if f['code'] == code]

    def live(self, **kw):
        base = dict(status='claimed', lane='l', token='t', claimed_at='2026-11-01T00:00:00Z', tries='1',
                    lease_until='2026-11-03T00:00:00Z')
        base.update(kw)
        return base

    def test_stale_blocked_boundary(self):
        d = dict(status='done', result='blocked', tries='1')
        rep = self.audit(row(done_at='2026-10-19T00:00:00Z', **d),
                         row(id='q2', done_at='2026-10-20T00:00:00Z', **d))
        self.assertEqual([f['task_id'] for f in rep['findings'] if f['code'] == 'stale-blocked'], ['q1'])

    def test_lease_ending_now_is_live(self):
        rep = self.audit(row(**self.live(lease_until='2026-11-02T00:00:00Z')),
                         row(id='q2', **self.live(lease_until='2026-11-01T23:59:59Z')))
        self.assertEqual([f['task_id'] for f in rep['findings'] if f['code'] == 'stale-claim'], ['q2'])

    def test_done_at_equal_now_or_claimed_at_is_fine(self):
        d = dict(status='done', result='none', tries='1')
        rep = self.audit(row(done_at='2026-11-02T00:00:00Z', claimed_at='2026-11-02T00:00:00Z', **d))
        self.assertEqual(rep['findings'], [])
        rep = self.audit(row(done_at='2026-11-02T00:00:01Z', **d),
                         row(id='q2', done_at='2026-10-01T00:00:00Z', claimed_at='2026-10-01T00:00:01Z', **d))
        msgs = self.messages(rep, 'done-state')
        self.assertEqual(sorted(msgs), ['done_at is before claimed_at', 'done_at is in the future'])

    def test_done_waiting_on_unknown_id_is_not_false_completion(self):
        d = dict(status='done', result='none', tries='1', done_at='2026-10-01T00:00:00Z')
        rep = self.audit(row(note='needs:q99', **d))
        self.assertEqual(self.codes(rep), ['prereq-missing'])

    def test_done_waiting_on_claimed_task_is_false_completion(self):
        d = dict(status='done', result='none', tries='1', done_at='2026-10-01T00:00:00Z')
        rep = self.audit(row(**self.live()), row(id='q2', note='needs:q1', **d))
        self.assertEqual(self.messages(rep, 'done-state'), ['done while a prerequisite is not done'])

    def test_duplicate_line_list_cut_only_above_cap(self):
        five = self.messages(self.audit(*[row() for _ in range(5)]), 'id-duplicate')
        six = self.messages(self.audit(*[row() for _ in range(6)]), 'id-duplicate')
        self.assertEqual(five[0], 'id used by 5 rows (lines 2, 3, 4, 5, 6)')
        self.assertEqual(six[0], 'id used by 6 rows (lines 2, 3, 4, 5, 6, ...)')

    def test_claimed_rows_share_dedupe_key(self):
        rep = self.audit(row(dedupe_key='k', **self.live()), row(id='q2', dedupe_key='k', **self.live()))
        self.assertEqual(self.codes(rep), ['dedupe-collision'] * 2)

    def test_negative_priority_is_a_valid_integer(self):
        rep = self.audit(row(priority='-3'), row(id='q2', priority=' 5 '))
        self.assertEqual(rep['findings'], [])
        self.assertEqual(self.codes(self.audit(row(priority='--3'))), ['schema-value'])

    def test_open_row_holding_only_a_lane(self):
        rep = self.audit(row(lane='lane-x'))
        self.assertEqual(self.codes(rep), ['open-state'])
        self.assertIn('lane or token', rep['findings'][0]['message'])

    def test_blank_evidence_is_missing_for_a_hit(self):
        rep = self.audit(row(status='done', result='hit', evidence='   ', tries='1',
                             done_at='2026-10-01T00:00:00Z'))
        self.assertEqual(self.messages(rep, 'provenance'), ['hit without evidence'])

    def test_private_source_ignores_letter_case(self):
        rep = self.audit(row(source='Private-Archive', person_key=''))
        self.assertEqual(self.codes(rep), ['provenance'])
        self.assertTrue(rep['findings'][0]['private'])
        self.assertEqual(rep['findings'][0]['source'], 'Private-Archive')


class ReviewRegressionTest(unittest.TestCase):
    """One case per item of contributions/reviews/queueaudit-final.md."""

    setUp, tearDown, audit, codes = (AuditTest.setUp, AuditTest.tearDown, AuditTest.audit, AuditTest.codes)
    run_cli = AuditTest.run_cli

    def p(self, name):
        return os.path.join(self.dir.name, name)

    def test_far_future_claimed_at_is_a_finding_not_a_crash(self):
        rep = self.audit(row(status='claimed', lane='l', token='t', claimed_at='9999-12-31T23:59:59Z', tries='1'))
        msgs = [f['message'] for f in rep['findings'] if f['code'] == 'schema-value']
        self.assertEqual(msgs, ['lease end cannot be computed (date out of range)'])
        self.audit(row(status='claimed', lane='l', token='t', claimed_at='9999-12-31T23:59:59Z', tries='1'))
        self.assertEqual(self.run_cli('--queue', self.path)[0], 1)

    def test_stale_days_must_be_finite(self):
        for bad in ('nan', 'inf', '-inf', '-1', '1e999', '36501', 'x'):
            self.audit(row())
            with self.assertRaises(SystemExit, msg=bad):
                self.run_cli('--queue', self.path, '--stale-days=' + bad)
            self.assertEqual(A.main is not None, True)
        for bad in (float('nan'), float('inf'), -1.0, 1e9):
            with self.assertRaises(ValueError):
                self.audit(row(), stale_days=bad)
        self.assertEqual(self.run_cli('--queue', self.path, '--stale-days', '36500')[0], 0)

    def test_tsv_neutralises_formulas_and_control_chars(self):
        rep = self.audit(row(id='=HYPERLINK("http://x","y")', source='+cmd'), row(id='q2', source='a\x1b[31mb\x07'))
        out = A.render_tsv(rep)
        for line in out.splitlines()[1:]:
            for cell in line.split('\t'):
                self.assertNotIn(cell[:1], ('=', '+', '@', '-'))
        self.assertNotIn('\x1b', out)
        self.assertNotIn('\x07', out)
        self.assertIn("'=HYPERLINK", out)
        self.assertIn("'+cmd", out)

    def test_json_has_no_nan(self):
        text = A.render_json(self.audit(row(source='')))
        self.assertNotIn('NaN', text)
        with self.assertRaises(ValueError):
            A.render_json({'x': float('nan')})

    def test_output_aliases_forbidden(self):
        self.audit(row())
        link, d, j = self.p('link.json'), self.p('dir'), self.p('r.json')
        os.mkdir(d)
        os.symlink(self.path, link)
        hard = self.p('hard.tsv')
        os.link(self.path, hard)
        before = open(self.path).read()
        for flag, target in (('--json-out', link), ('--json-out', d), ('--tsv-out', hard), ('--tsv-out', self.path)):
            with self.assertRaises(SystemExit):
                self.run_cli('--queue', self.path, flag, target)
        with self.assertRaises(SystemExit):
            self.run_cli('--queue', self.path, '--json-out', j, '--tsv-out', j)
        self.assertFalse(os.path.exists(j))
        self.assertEqual(open(self.path).read(), before)

    def test_missing_queue_with_existing_output_and_nul_path(self):
        existing = self.p('e.json')
        open(existing, 'w').close()
        self.assertEqual(self.run_cli('--queue', self.p('none.tsv'), '--json-out', existing)[0], 2)
        self.audit(row())
        with self.assertRaises(SystemExit):
            self.run_cli('--queue', self.path, '--json-out', 'a\0b')
        self.assertEqual(self.run_cli('--queue', 'a\0b')[0], 2)

    def test_unwritable_output_is_exit_2_and_writes_nothing(self):
        self.audit(row(source=''))
        good = self.p('ok.json')
        code, out, _err = self.run_cli('--queue', self.path, '--json-out', good,
                                      '--tsv-out', self.p('nodir') + '/r.tsv')
        self.assertEqual(code, 2)
        self.assertEqual(out, '')
        self.assertFalse(os.path.exists(good))
        self.assertEqual(sorted(os.listdir(self.dir.name)), ['q.tsv'])

    def test_messages_do_not_leak_note_or_cells(self):
        bad = row(note='private needs:alice-ssn-123', status='STATUS-SECRET', kind='KIND-SECRET')
        done = row(id='q2', status='done', result='none', tries='1', done_at='2026-10-01T00:00:00Z',
                   note='needs:q3')
        rep = self.audit(bad, done, row(id='q3', note='needs:' + 'z' * 5000))
        text = A.render_json(rep) + A.render_tsv(rep)
        for secret in ('alice-ssn-123', 'STATUS-SECRET', 'KIND-SECRET', 'zzzzzzzz'):
            self.assertNotIn(secret, text)
        self.assertIn('a prerequisite is not in the queue', text)

    def test_bounds(self):
        rep = self.audit(row(id='q9' + 'x' * 5000, note='needs:' + ','.join(['m'] * 100000)))
        missing = [f for f in rep['findings'] if f['code'] == 'prereq-missing']
        self.assertEqual(len(missing), 1)
        self.assertLessEqual(len(rep['findings'][0]['task_id']), A.MAX_FIELD_CHARS + 3)
        many = A.prereqs({'note': 'needs:' + ','.join(f'i{int(i)}' for i in range(A.MAX_PREREQS + 50))})
        self.assertEqual(len(many), A.MAX_PREREQS)
        self.assertEqual(A.prereqs({'note': 'needs:' + ','.join(['m'] * 100000)}), ['m'])
        self.assertEqual(len(set(many)), A.MAX_PREREQS)
        old = A.MAX_QUEUE_BYTES
        A.MAX_QUEUE_BYTES = 10
        try:
            with self.assertRaises(ValueError):
                self.audit(row())
            self.assertEqual(self.run_cli('--queue', self.path)[0], 2)
        finally:
            A.MAX_QUEUE_BYTES = old

    def test_write_all_rolls_back_first_replace_when_second_fails(self):
        a, b = self.p('a.json'), self.p('b.tsv')
        with open(a, 'w') as f:
            f.write('old-a')
        real = os.replace

        def flaky(src, dst):
            if dst == b:
                raise OSError('boom')
            return real(src, dst)

        A.os.replace = flaky
        try:
            with self.assertRaises(OSError):
                A.write_all([(a, 'new-a'), (b, 'new-b')])
        finally:
            A.os.replace = real
        self.assertEqual(open(a).read(), 'old-a')
        self.assertFalse(os.path.exists(b))
        self.assertEqual(sorted(os.listdir(self.dir.name)), ['a.json'])

    def _flaky_with_failing_restore(self, a, b):
        real = os.replace

        def flaky(src, dst):
            if dst == b or (dst == a and os.path.basename(src).startswith('.gwt-audit-') and flaky.a_done):
                raise OSError('boom')
            result = real(src, dst)
            if dst == a:
                flaky.a_done = True
            return result

        flaky.a_done = False
        return real, flaky

    def test_write_all_reports_failed_rollback(self):
        a, b = self.p('a.json'), self.p('b.tsv')
        with open(a, 'w') as f:
            f.write('old-a')
        real, flaky = self._flaky_with_failing_restore(a, b)
        A.os.replace = flaky
        try:
            with self.assertRaises(A.RollbackError) as ctx:
                A.write_all([(a, 'new-a'), (b, 'new-b')])
        finally:
            A.os.replace = real
        self.assertIn(a, str(ctx.exception))
        self.assertEqual(open(a).read(), 'new-a')  # honest: the file really is still new
        self.assertFalse([n for n in os.listdir(self.dir.name) if n.startswith('.gwt-audit-')])

    def test_cli_says_rollback_failed(self):
        q = self.p('q.tsv')
        with open(q, 'w') as f:
            f.write('\t'.join(Q.COLUMNS) + '\n' + row() + '\n')
        a, b = self.p('a.json'), self.p('b.tsv')
        with open(a, 'w') as f:
            f.write('old-a')
        real, flaky = self._flaky_with_failing_restore(a, b)
        A.os.replace = flaky
        try:
            code, out, err = self.run_cli('--queue', q, '--json-out', a, '--tsv-out', b)
        finally:
            A.os.replace = real
        self.assertEqual(code, 2)
        self.assertIn('rollback FAILED', err)
        self.assertEqual(out, '')

    def test_restore_returns_false_on_failure(self):
        self.assertFalse(A.restore(self.p('no-such-dir/x'), b'old'))
        self.assertTrue(A.restore(self.p('never-existed'), None))

    def test_read_raw_bound_counts_bytes(self):
        with open(self.p('q.tsv'), 'wb') as f:
            f.write(b'id\n' + b'\xc3\xa9' * 10)
        # cap 14: the capped read (15 bytes) ends on a character boundary and decodes cleanly, so only the
        # length check can raise here, not a UnicodeDecodeError (which is also a ValueError).
        old, A.MAX_QUEUE_BYTES = A.MAX_QUEUE_BYTES, 14
        real_size = os.path.getsize
        A.os.path.getsize = lambda p: 0  # a file that grew after the size check: only the read cap can stop it
        try:
            with self.assertRaises(ValueError):
                A.read_raw(self.p('q.tsv'))
        finally:
            A.os.path.getsize = real_size
            A.MAX_QUEUE_BYTES = old

    def test_symlink_output_to_other_file_or_dangling_is_refused(self):
        # A link to a file that is not the queue is caught only by the islink guard (same_file cannot see it).
        self.audit(row())
        other, link, dangling = self.p('other.txt'), self.p('l1.json'), self.p('l2.json')
        with open(other, 'w') as f:
            f.write('keep')
        os.symlink(other, link)
        os.symlink(self.p('missing-target'), dangling)
        for flag, target in (('--json-out', link), ('--tsv-out', link), ('--json-out', dangling)):
            with self.assertRaises(SystemExit):
                self.run_cli('--queue', self.path, flag, target)
        self.assertEqual(open(other).read(), 'keep')
        self.assertFalse(os.path.exists(self.p('missing-target')))
        self.assertIn('symlink', A.bad_outputs(self.path, (link, None)))

    def test_private_comes_from_note_not_query_or_source(self):
        rep = self.audit(row(person_key='', note='private lead'),
                         row(id='q2', person_key='', query='private query', note='plain'))
        by_id = {f['task_id']: f['private'] for f in rep['findings']}
        self.assertEqual(by_id, {'q1': True, 'q2': False})

    def test_argparse_prog_name(self):
        out = io.StringIO()
        with redirect_stdout(out), self.assertRaises(SystemExit):
            A.main(['--help'])
        self.assertTrue(out.getvalue().startswith('usage: gwt-queue-audit '))

    def test_write_all_success_replaces_all(self):
        a, b = self.p('a.json'), self.p('b.tsv')
        A.write_all([(a, 'x'), (b, 'y')])
        self.assertEqual((open(a).read(), open(b).read()), ('x', 'y'))

    def test_keyword_needs_word_boundary(self):
        self.assertEqual(A.prereqs({'note': 'unneeds:q1 needs:q2'}), ['q2'])


if __name__ == '__main__':
    unittest.main()
