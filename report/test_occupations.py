import copy
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
import gwt_occupations as O
import gwt_report as R


def tree():
    T = {name: {} for name in R.CLASSES.values()}
    T['people'] = {'p': {'handle': 'p', 'gramps_id': 'I1', 'event_ref_list': [
        {'ref': 'd', 'role': 'Primary'}, {'ref': 'o', 'role': 'Primary'}]}}
    T['events'] = {'d': {'type': 'Death'}, 'o': {'gramps_id': 'E1', 'type': 'Occupation',
        'description': 'Journalière', 'date': {'calendar': 0, 'modifier': 0, 'dateval': [1, 1, 1800, False]},
        'place': 'l', 'citation_list': ['c']}}
    for key,event in T['events'].items():event['handle'] = key
    T['citations'] = {'c': {'gramps_id': 'C1', 'handle': 'c', 'source_handle':'s'}}
    T['sources'] = {'s':{'handle':'s','gramps_id':'S1'}}
    T['people']['child']={'handle':'child','gramps_id':'I2','parent_family_list':['f']}
    T['families']['f']={'handle':'f','father_handle':'p','child_ref_list':[{'ref':'child','frel':'Birth'}]}
    T['places'] = {'l': {'gramps_id': 'L1', 'handle': 'l', 'title': 'Invented town'}}
    return T


class OccupationTests(unittest.TestCase):
    def test_original_literal_and_explicit_category_kept_separate(self):
        r = O.report(tree(), root="child", categories={'Journalière': 'agriculture'})
        self.assertEqual(r['rows'][0]['literal'], 'Journalière')
        self.assertEqual(r['rows'][0]['category'], 'agriculture')
        self.assertEqual(r['rows'][0]['century'], 18)
        self.assertEqual(r['by']['place_id'][0]['value'], 'L1')
        self.assertEqual(r['rows'][0]['citations'], ['C1'])
        self.assertIsNone(O.report(tree(), root="child")['rows'][0]['category'])

    def test_living_unknown_and_witness_not_inferred(self):
        for kind in ['Birth', 'Occupation', None]:
            T = tree();T['events']['d']['type'] = kind
            self.assertEqual(O.report(T, root="child")['rows'], [])
        T = tree();T['people']['p']['event_ref_list'][0]['role'] = 'Witness'
        self.assertEqual(O.report(T, root="child")['rows'], [])
        for role in ['Witness', 'Father', 'Mother', None]:
            T = tree();T['people']['p']['event_ref_list'][1]['role'] = role
            self.assertEqual(O.report(T, root="child")['rows'], [])

    def test_privacy_never_opens_living(self):
        for obj in ['people', 'events']:
            T = tree();T[obj]['p' if obj == 'people' else 'o']['private'] = True
            self.assertEqual(O.report(T, root="child")['rows'], [])
            self.assertEqual(len(O.report(T, root="child", include_private=True)['rows']), 1)
        T = tree();T['events']['d']['type'] = 'Birth'
        self.assertEqual(O.report(T, root="child", include_private=True)['rows'], [])

    def test_private_reference_and_role_order_do_not_leak_or_drop(self):
        T = tree();T['people']['p']['event_ref_list'][1]['private'] = True
        self.assertEqual(O.report(T, root="child")['rows'], [])
        self.assertEqual(len(O.report(T, root="child", include_private=True)['rows']), 1)
        T = tree();T['people']['p']['event_ref_list'].insert(1, {'ref': 'o', 'role': 'Witness'})
        self.assertEqual(len(O.report(T, root="child")['rows']), 1)

    def test_citation_and_literal_required_and_redaction_not_counted(self):
        for literal in ['', ' ', '[REDACTED]', None]:
            T = tree();T['events']['o']['description'] = literal
            self.assertEqual(O.report(T, root="child")['rows'], [])
        T = tree();T['events']['o']['citation_list'] = ['missing']
        self.assertEqual(O.report(T, root="child")['rows'], [])

    def test_count_unit_and_duplicate_refs(self):
        T = tree();T['people']['p']['event_ref_list'].append({'ref': 'o', 'role': 'Primary'})
        self.assertEqual(O.report(T, root="child")['frequencies'], [{'literal': 'Journalière', 'count': 1}])
        T['events']['o2'] = copy.deepcopy(T['events']['o']);T['events']['o2']['gramps_id'] = 'E2'
        T['people']['p']['event_ref_list'].append({'ref': 'o2', 'role': 'Primary'})
        self.assertEqual(O.report(T, root="child")['frequencies'][0]['count'], 2)

    def test_unknown_and_approximate_dates_not_forced_to_century(self):
        for date in [{}, {'calendar': 1, 'dateval': [1, 1, 1800, False]},
                     {'modifier': 2, 'dateval': [1, 1, 1800, False]},
                     {'dateval': [1, 1, 1800, False, 1, 1, 1901, False]},
                     {'dateval': [0, 0, 0, False]}]:
            T = tree();T['events']['o']['date'] = date
            self.assertIsNone(O.report(T, root="child")['rows'][0]['century'])
        T = tree();T['events']['o']['date']['dateval'][2] = 1801
        self.assertEqual(O.report(T, root="child")['rows'][0]['century'], 19)

    def test_generations_root_and_unknown(self):
        T = tree();T['people']['child'] = {'handle': 'child', 'gramps_id': 'I2',
            'parent_family_list': ['f']}
        T['families']['f'] = {'father_handle': 'p', 'child_ref_list': [{'ref': 'child', 'frel': 'Birth'}]}
        self.assertEqual(O.report(T, root='child')['rows'][0]['generations'], [1])
        with self.assertRaises(R.BadInput):O.report(T)

    def test_time_periods_are_event_periods_and_boundary_exact(self):
        T=tree()
        self.assertEqual((O.report(T,root='child')['rows'][0]['period25'],O.report(T,root='child')['rows'][0]['period50']),(1776,1751))
        T['events']['o']['date']['dateval'][2]=1801
        self.assertEqual((O.report(T,root='child')['rows'][0]['period25'],O.report(T,root='child')['rows'][0]['period50']),(1801,1801))
        T['events']['o']['tag_list']=['t'];T['tags']['t']={'name':'contradiction'}
        self.assertEqual(O.report(T,root='child')['rows'],[])

    def test_category_file_exact_no_duplicate_or_invalid_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)/'map.json'
            for body in ['[]', '{"x":null}', '{"x":"a","x":"b"}', '{"":"a"}']:
                p.write_text(body)
                with self.assertRaises(ValueError):O.category_map(p)
            p.write_text('{"Journalière":"agriculture"}')
            self.assertEqual(O.category_map(p), {'Journalière': 'agriculture'})

    def test_cli_offline_no_input_writes(self):
        T = tree()
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)/'tree.json';p.write_text(json.dumps({k:list(v.values()) for k,v in T.items()}))
            before = p.read_bytes()
            out = io.StringIO()
            with redirect_stdout(out):self.assertEqual(R.main(['occupations', str(p), '--root', 'I2']), 0)
            self.assertEqual(json.loads(out.getvalue())['rows'][0]['literal'], 'Journalière')
            self.assertEqual(p.read_bytes(), before)
            self.assertEqual(len(list(Path(tmp).iterdir())), 1)
