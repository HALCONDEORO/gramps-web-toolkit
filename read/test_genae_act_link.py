"""Tests for gwt_genae_act_link. Invented data only; no network. Run: cd read && python3 -m unittest -v test_genae_act_link"""
import json, os, tempfile, unittest

import gwt_genae_act_link as L

PERMA = 'https://genae.org/registro/bautismos/123'
CAND = {'id': 'bautismo-123', 'name': 'Ana Zorvalo', 'date': '01/02/1750', 'locality': 'Villaficticia', 'url': PERMA}
GOOD = {'cote': 'B-7', 'ark': 'ark:/12345/abc123', 'image_url': 'https://archive.example/view/1'}


def reading(who, crop, **kw):
    return dict({'reader': who, 'crop': crop, 'given': 'Ana', 'surname': 'Zorvalo', 'date_iso': '1750-02-01'}, **kw)


class Validation(unittest.TestCase):
    def test_url(self):
        self.assertIsNone(L.url_problem(PERMA, L.INDEX_HOSTS))
        for bad in ('http://genae.org/x', 'https://u:p@genae.org/x', 'https://genae.org/a b', 'https://evil.example/x',
                    'ftp://genae.org/x', '', None, 'https://genae.org:99999/x'):
            self.assertTrue(L.url_problem(bad, L.INDEX_HOSTS), bad)

    def test_ark(self):
        self.assertIsNone(L.ark_problem('ark:/12345/abc123'))
        for bad in ('https://x.example/ark:/12345/abc', 'ark:/1/abc', 'ark:/12345/', '', None):
            self.assertTrue(L.ark_problem(bad), bad)


class Stages(unittest.TestCase):
    def test_lead_only_lists_missing_and_keeps_url(self):
        r = L.link(dict(CAND, url=PERMA))
        self.assertEqual(r['stage'], 'indexed-lead')
        self.assertEqual(r['index_url'], PERMA)
        self.assertEqual(r['missing'], ['parish', 'book', 'page', 'cote', 'ark', 'image_url'])
        self.assertEqual(r['state'], 'por-confirmar-acta')

    def test_url_kept_verbatim_even_when_invalid(self):
        odd = 'http://genae.org/x y'
        r = L.link(dict(CAND, url=odd))
        self.assertEqual(r['index_url'], odd)
        self.assertTrue(any('index url' in p for p in r['problems']))

    def test_search_page_is_not_a_permalink(self):
        r = L.link(dict(CAND, url='https://genae.org/buscar'))
        self.assertTrue(any('search page' in p for p in r['problems']))

    def test_nothing_is_guessed(self):
        r = L.link(CAND, {'ark': 'ark:/12345/abc123'})
        self.assertEqual(r['image_url'], '')
        self.assertEqual(r['stage'], 'indexed-lead')   # no cote
        self.assertIn('cote', r['missing'])

    def test_image_located_needs_cote(self):
        self.assertEqual(L.link(CAND, GOOD)['stage'], 'image-located')
        self.assertEqual(L.link(CAND, {'image_url': GOOD['image_url']})['stage'], 'indexed-lead')

    def test_invalid_ark_does_not_locate(self):
        r = L.link(CAND, {'cote': 'B-7', 'ark': 'nonsense'})
        self.assertEqual(r['stage'], 'indexed-lead')
        self.assertTrue(any(p.startswith('ark:') for p in r['problems']))

    def test_ark_mismatch_with_image_url(self):
        r = L.link(CAND, dict(GOOD, image_url='https://archive.example/ark:/12345/other'))
        self.assertTrue(any('different ark' in p for p in r['problems']))

    def test_candidate_evidence_difference_reported(self):
        r = L.link(dict(CAND, parish='A'), {'parish': 'B'})
        self.assertEqual(r['parish'], 'A')
        self.assertTrue(any('parish differs' in p for p in r['problems']))


class Verification(unittest.TestCase):
    def ev(self, readings):
        return dict(GOOD, readings=readings)

    def test_two_independent_readings_never_promote(self):
        r = L.link(CAND, self.ev([reading('r1', 'crop-a.png'), reading('r2', 'crop-b.png', given='ana')]))
        self.assertEqual(r['stage'], 'readings-agree')
        self.assertEqual(r['state'], 'por-confirmar-acta')
        self.assertEqual(r['zoom'], 'crop-a.png | crop-b.png')
        self.assertTrue(any('human writer review' in b for b in r['blockers']))

    def test_no_input_ever_yields_verificado_zoom(self):
        ev = self.ev([reading('r1', 'a'), reading('r2', 'b')])
        ev.update(date_iso='1750-02-01', filiation='hijo de X')
        with self.assertRaises(ValueError):   # unknown evidence keys are rejected, not ignored
            L.link(CAND, dict(ev, writer_review=True, state='verificado-zoom'))
        r = L.link(dict(CAND, state='verificado-zoom', stage='zoom-verified'), ev)
        self.assertEqual(r['state'], 'por-confirmar-acta')
        self.assertNotEqual(r['stage'], 'zoom-verified')
        self.assertTrue(any('human writer review' in b for b in r['blockers']))

    def test_date_and_filiation_original_or_empty(self):
        r = L.link(CAND)
        self.assertEqual((r['date_iso'], r['filiation']), ('', ''))
        self.assertTrue(any('date_iso missing' in b for b in r['blockers']))
        self.assertTrue(any('filiation missing' in b for b in r['blockers']))
        r = L.link(CAND, dict(GOOD, date_iso='1750-02-01', filiation='hijo de Y', readings=[
            reading('r1', 'a'), reading('r2', 'b')]))
        self.assertEqual((r['date_iso'], r['filiation']), ('1750-02-01', 'hijo de Y'))
        self.assertFalse(any('missing' in b for b in r['blockers']))
        r = L.link(CAND, {'date_iso': '1750-13-45'})
        self.assertEqual(r['date_iso'], '1750-13-45')
        self.assertTrue(any('not a valid' in b for b in r['blockers']))

    def test_incomplete_names_rejected(self):
        for bad in ('A.', 'A', 'Ana?', 'Zor...', '[Zor]', 'Zorv…', '', 'J. M.'):
            r = L.link(CAND, self.ev([reading('r1', 'a', surname=bad), reading('r2', 'b', surname=bad)]))
            self.assertEqual(r['stage'], 'image-located', bad)
            self.assertEqual(r['zoom'], '', bad)

    def test_empty_readers_rejected(self):
        for bad in ('', '   ', None, 5):
            r = L.link(CAND, self.ev([reading(bad, 'a'), reading('r2', 'b')]))
            self.assertEqual(r['stage'], 'image-located', repr(bad))
            self.assertTrue(any('no reader' in p for p in r['problems']))

    def test_invalid_crop_provenance_rejected(self):
        for bad in ('../a.png', '/etc/passwd', 'a b.png', 'http://x.example/c.png', 'file:///c.png', 'a\\b', None, 7):
            r = L.link(CAND, self.ev([reading('r1', bad), reading('r2', 'b')]))
            self.assertEqual(r['stage'], 'image-located', repr(bad))
            self.assertTrue(any('crop' in p for p in r['problems']), repr(bad))
        r = L.link(CAND, self.ev([reading('r1', 'https://archive.example/c1.png'), reading('r2', 'b')]))
        self.assertEqual(r['stage'], 'readings-agree')

    def test_impossible_reading_date_rejected(self):
        r = L.link(CAND, self.ev([reading('r1', 'a', date_iso='1750-02-30'), reading('r2', 'b', date_iso='1750-02-30')]))
        self.assertEqual(r['stage'], 'image-located')

    def test_one_reading_does_not(self):
        r = L.link(CAND, self.ev([reading('r1', 'crop-a.png')]))
        self.assertEqual(r['stage'], 'image-located')
        self.assertEqual(r['zoom'], '')

    def test_same_reader_or_same_crop_does_not(self):
        for rs in ([reading('r1', 'a'), reading('R1', 'b')], [reading('r1', 'a'), reading('r2', 'A')]):
            self.assertEqual(L.link(CAND, self.ev(rs))['stage'], 'image-located')

    def test_missing_crop_does_not(self):
        r = L.link(CAND, self.ev([reading('r1', 'a'), reading('r2', '')]))
        self.assertEqual(r['stage'], 'image-located')
        self.assertTrue(any('no crop' in p for p in r['problems']))

    def test_disagreement_does_not_and_is_reported(self):
        r = L.link(CAND, self.ev([reading('r1', 'a'), reading('r2', 'b', date_iso='1750-02-02')]))
        self.assertEqual(r['stage'], 'image-located')
        self.assertTrue(any('disagree on date_iso' in p for p in r['problems']))

    def test_incomplete_reading_does_not(self):
        r = L.link(CAND, self.ev([reading('r1', 'a'), reading('r2', 'b', surname='')]))
        self.assertEqual(r['stage'], 'image-located')

    def test_readings_without_image_are_ignored(self):
        r = L.link(CAND, {'readings': [reading('r1', 'a'), reading('r2', 'b')]})
        self.assertEqual(r['stage'], 'indexed-lead')
        self.assertTrue(any('ignored' in p for p in r['problems']))

    def test_garbage_readings_do_not_raise(self):
        self.assertEqual(L.link(CAND, self.ev(['x', None]))['stage'], 'image-located')


class Output(unittest.TestCase):
    def test_tsv_neutralises_and_has_header(self):
        recs = L.build([dict(CAND, name='=1+1', locality='=cmd')], {})
        lines = L.to_tsv(recs).splitlines()
        self.assertEqual(lines[0].split('\t'), list(L.COLUMNS))
        self.assertIn("'=cmd", lines[1].split('\t'))
        self.assertEqual(len(lines[1].split('\t')), len(L.COLUMNS))

    def test_build_uses_evidence_by_id(self):
        recs = L.build([CAND, dict(CAND, id='other')], {'bautismo-123': GOOD})
        self.assertEqual([r['stage'] for r in recs], ['image-located', 'indexed-lead'])

    def test_load_candidates(self):
        self.assertEqual(L.load_candidates({'candidates': [CAND]}), [CAND])
        with self.assertRaises(ValueError):
            L.load_candidates({'x': 1})

    def test_duplicate_or_empty_ids_reject(self):
        for bad in ([CAND, dict(CAND)], [CAND, dict(CAND, id=' bautismo-123 ')], [dict(CAND, id='')],
                    [dict(CAND, id=None)], [dict(CAND, id=5)], [dict(CAND, id=True)], ['x']):
            with self.assertRaises(ValueError, msg=repr(bad)):
                L.build(bad, {})

    def test_evidence_non_dict_rejects(self):
        for bad in ([], 'x', 5):
            with self.assertRaises(ValueError):
                L.build([CAND], bad)
            with self.assertRaises(ValueError):
                L.build([CAND], {'bautismo-123': bad})

    def test_bool_and_nontext_fields_ignored_and_reported(self):
        r = L.link(dict(CAND, parish=True), {'cote': 5, 'ark': ['x'], 'book': False, 'date_iso': 20})
        for k in ('parish', 'cote', 'ark', 'book', 'date_iso'):
            self.assertEqual(r[k], '', k)
        self.assertEqual(r['stage'], 'indexed-lead')
        self.assertTrue(any('not text' in p for p in r['problems']))
        r = L.link(CAND, dict(GOOD, readings=[reading('r1', 'a', given=True), reading('r2', 'b')]))
        self.assertEqual(r['stage'], 'image-located')

    def test_all_readings_and_readers_preserved(self):
        rs = [reading('r1', 'a'), reading('r2', 'b'), reading('r3', 'c', surname='Otro')]
        r = L.link(CAND, dict(GOOD, readings=rs))
        self.assertEqual(r['readings'], rs)
        self.assertIsNot(r['readings'][0], rs[0])
        self.assertEqual(r['readers'], ['r1', 'r2', 'r3'])
        row = L.to_tsv([r]).splitlines()[1].split('\t')
        self.assertIn('r1; r2; r3', row)
        self.assertIn('Otro', row[L.COLUMNS.index('readings')])

    def test_third_disagreeing_or_invalid_reading_blocks(self):
        for third in (reading('r3', 'c', surname='Otro'), reading('r3', 'c', surname='Zor?'), 'junk'):
            r = L.link(CAND, dict(GOOD, readings=[reading('r1', 'a'), reading('r2', 'b'), third]))
            self.assertEqual(r['stage'], 'image-located', repr(third))
            self.assertEqual(r['zoom'], '')

    def test_image_url_equal_to_index_never_locates(self):
        for img in (PERMA, PERMA + '/', PERMA + '#x', 'https://GENAE.org/registro/bautismos/123'):
            r = L.link(CAND, {'cote': 'B-7', 'image_url': img})
            self.assertEqual(r['stage'], 'indexed-lead', img)
            r = L.link(CAND, {'cote': 'B-7', 'image_url': img, 'ark': 'ark:/12345/abc123'})
            self.assertEqual(r['stage'], 'indexed-lead', img)
            self.assertTrue(any('index url' in p and 'image_url' in p for p in r['problems']))

    def test_ark_match_is_exact_not_substring(self):
        for img in ('https://archive.example/ark:/12345/abc123456', 'https://archive.example/ark:/12345/abc1',
                    'https://archive.example/x/ark:/12345/abc123/extra'):
            r = L.link(CAND, dict(GOOD, image_url=img))
            self.assertTrue(any('different ark' in p for p in r['problems']), img)
        r = L.link(CAND, dict(GOOD, image_url='https://archive.example/ark:/12345/abc123'))
        self.assertFalse(any('different ark' in p for p in r['problems']))

    def test_cli_rejects_aliases_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            c, e, t = (os.path.join(d, n) for n in ('c.json', 'e.json', 'o.tsv'))
            link_c = os.path.join(d, 'link.json')
            for p, data in ((c, [CAND]), (e, {})):
                with open(p, 'w') as fh:
                    json.dump(data, fh)
            os.symlink(c, link_c)
            before = open(c).read()
            self.assertEqual(L.main([c, '--tsv', c]), 2)
            self.assertEqual(L.main([c, '--json', link_c]), 2)
            self.assertEqual(L.main([c, '--evidence', e, '--json', e]), 2)
            self.assertEqual(L.main([c, '--tsv', t, '--json', t]), 2)
            self.assertEqual(open(c).read(), before)
            self.assertFalse(os.path.exists(t))
            with open(c, 'w') as fh:
                json.dump([CAND, CAND], fh)
            self.assertEqual(L.main([c, '--tsv', t]), 2)
            self.assertFalse(os.path.exists(t))

    def test_cli_rejects_hardlinked_output_and_keeps_input(self):
        with tempfile.TemporaryDirectory() as d:
            c, h = os.path.join(d, 'c.json'), os.path.join(d, 'hard.tsv')
            with open(c, 'w') as fh:
                json.dump([CAND], fh)
            os.link(c, h)
            before = open(c).read()
            self.assertEqual(L.main([c, '--tsv', h]), 2)
            self.assertEqual(open(c).read(), before)

    def test_valid_ark_with_bad_or_conflicting_image_never_locates(self):
        for img in ('http://archive.example/view/1', 'https://archive.example/ark:/12345/other', 'not a url'):
            r = L.link(CAND, {'cote': 'B-7', 'ark': 'ark:/12345/abc123', 'image_url': img})
            self.assertEqual(r['stage'], 'indexed-lead', img)
        r = L.link(dict(CAND, image_url='https://archive.example/a'), dict(GOOD, image_url='https://archive.example/b'))
        self.assertEqual(r['stage'], 'indexed-lead')
        r = L.link(dict(CAND, ark='ark:/12345/abc123'), dict(GOOD, ark='ark:/12345/zzz'))
        self.assertEqual(r['stage'], 'indexed-lead')
        self.assertTrue(any('conflicts' in p for p in r['problems']))

    def test_index_url_default_port_alias(self):
        for img in ('https://genae.org:443/registro/bautismos/123', 'https://GENAE.org:443/registro/bautismos/123/'):
            r = L.link(CAND, {'cote': 'B-7', 'image_url': img})
            self.assertEqual(r['stage'], 'indexed-lead', img)

    def test_index_columns(self):
        r = L.link(CAND)
        row = dict(zip(L.COLUMNS, L.to_tsv([r]).splitlines()[1].split('\t')))
        self.assertEqual((row['name_in_index'], row['date_in_index']), ('Ana Zorvalo', '01/02/1750'))

    def test_unknown_evidence_keys_reject(self):
        for bad in ({'cotes': 'B-7'}, {'state': 'verificado-zoom'}):
            with self.assertRaises(ValueError):
                L.build([CAND], {'bautismo-123': bad})

    def test_date_filiation_conflicts_block_stage(self):
        rs = [reading('r1', 'a'), reading('r2', 'b')]
        bad = [reading('r1', 'a', date_iso='1999-01-01'), reading('r2', 'b', date_iso='1999-01-01')]
        cases = ((dict(CAND, date_iso='1750-02-01'), dict(GOOD, readings=rs, date_iso='1750-02-02')),   # cand vs evidence
                 (CAND, dict(GOOD, readings=bad, date_iso='1750-02-01')))                               # evidence vs readings
        for cand, ev in cases:
            r = L.link(cand, ev)
            self.assertEqual(r['stage'], 'indexed-lead')
            self.assertTrue(any(b.startswith('conflict: date_iso') for b in r['blockers']))
            self.assertEqual(len(r['readings']), 2)   # readings still preserved
        r = L.link(dict(CAND, filiation='hijo de X'), dict(GOOD, filiation='hijo de Y', readings=rs))
        self.assertEqual(r['stage'], 'indexed-lead')
        self.assertTrue(any(b.startswith('conflict: filiation') for b in r['blockers']))

    def test_duplicate_json_keys_reject(self):
        with tempfile.TemporaryDirectory() as d:
            c, t = os.path.join(d, 'c.json'), os.path.join(d, 'o.tsv')
            with open(c, 'w') as fh:
                fh.write('[{"id": "a", "id": "b", "url": "%s"}]' % PERMA)
            self.assertEqual(L.main([c, '--tsv', t]), 2)
            self.assertFalse(os.path.exists(t))

    def test_output_directory_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            c = os.path.join(d, 'c.json')
            with open(c, 'w') as fh:
                json.dump([CAND], fh)
            self.assertEqual(L.main([c, '--tsv', d]), 2)

    def test_partial_swap_rolls_back_and_says_so(self):
        from unittest import mock
        with tempfile.TemporaryDirectory() as d:
            a, b = os.path.join(d, 'a.tsv'), os.path.join(d, 'b.json')
            with open(a, 'w') as fh:
                fh.write('old-a')
            real, calls = os.replace, []

            def flaky(src, dst):
                calls.append(dst)
                if len(calls) == 2:
                    raise OSError('disk gremlin')
                return real(src, dst)
            with mock.patch('os.replace', flaky):
                with self.assertRaises(OSError) as cm:
                    L._write_all([(a, 'new-a'), (b, 'new-b')])
            self.assertIn('restored', str(cm.exception))
            self.assertEqual(open(a).read(), 'old-a')
            self.assertFalse(os.path.exists(b))
            self.assertEqual(os.listdir(d).count('a.tsv'), 1)
            self.assertFalse([n for n in os.listdir(d) if n.startswith('.tmp-genae-')])
            # rollback itself failing is reported, not hidden
            calls.clear()
            with mock.patch('os.replace', flaky), mock.patch.object(L, '_rollback', return_value=[a]):
                with self.assertRaises(OSError) as cm:
                    L._write_all([(a, 'new-a'), (b, 'new-b')])
            self.assertIn('ROLLBACK FAILED', str(cm.exception))

    def test_cli_writes_files(self):
        with tempfile.TemporaryDirectory() as d:
            c, t, j = (os.path.join(d, n) for n in ('c.json', 'o.tsv', 'o.json'))
            with open(c, 'w') as fh:
                json.dump([CAND], fh)
            self.assertEqual(L.main([c, '--tsv', t, '--json', j]), 0)
            self.assertEqual(json.load(open(j))[0]['stage'], 'indexed-lead')
            self.assertTrue(open(t).read().startswith('link_id\t'))
            self.assertEqual(L.main([os.path.join(d, 'missing.json')]), 2)


if __name__ == '__main__':
    unittest.main()
