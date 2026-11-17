import copy
import json

import pytest

from gwt_check.components import fingerprint, review
from gwt_check.tree import BadInput, Tree, TYPES
from gwt_check.cli import main


def data():
    d = {t: [] for t in TYPES}
    d['people'] = [{'handle': f'p{i}', 'gramps_id': f'I{i}'} for i in range(1, 6)]
    d['families'] = [{'handle': 'f1', 'gramps_id': 'F1', 'father_handle': 'p1',
                      'child_ref_list': [{'ref': 'p2'}]}]
    return d


def manifest(t, person='I3'):
    return {'format': 'gwt-component-holds/1', 'tree_sha256': fingerprint(t),
            'holds': [{'person': person, 'evidence_refs': ['N1: unresolved identity comparison']}]}


def test_components_include_singletons_and_family_edges_in_both_directions():
    d = data()
    d['people'][2]['parent_family_list'] = ['f1']
    r = review(Tree(d), 'I1')
    assert r['counts'] == {'people': 5, 'components': 3, 'root_connected': 3, 'held_outside_root': 0}
    assert [x['people'] for x in r['components']] == [['I1', 'I2', 'I3'], ['I4'], ['I5']]
    assert r['unilateral_family_refs'] == [
        {'person': 'I1', 'family': 'F1', 'present_on': 'family'},
        {'person': 'I2', 'family': 'F1', 'present_on': 'family'},
        {'person': 'I3', 'family': 'F1', 'present_on': 'person'}]


def test_spouses_siblings_and_parent_side_lists_connect_without_family_writes():
    d = data()
    d['families'][0]['mother_handle'] = 'p3'
    d['people'][3]['family_list'] = ['f1']
    assert review(Tree(d), 'I2')['counts']['root_connected'] == 4


@pytest.mark.parametrize('root', ['missing', '', 'p1'])
def test_root_requires_exact_unique_id_no_largest_fallback(root):
    with pytest.raises(BadInput, match='root'):
        review(Tree(data()), root)


def test_duplicate_root_ids_refused():
    d = data()
    d['people'][4]['gramps_id'] = 'I1'
    with pytest.raises(BadInput, match='root'):
        review(Tree(d), 'I1')


def test_holds_expand_to_whole_component_and_do_not_make_it_connected():
    d = data()
    d['families'].append({'handle': 'f2', 'gramps_id': 'F2', 'father_handle': 'p3', 'mother_handle': 'p4'})
    t = Tree(d)
    r = review(t, 'I1', manifest(t))
    assert r['counts']['held_outside_root'] == 2
    assert next(c for c in r['components'] if 'I3' in c['people'])['status'] == 'review_hold'
    assert r['counts']['root_connected'] == 2
    assert r['safe_to_delete'] == [] and r['advisory_only'] is True
    assert next(c for c in r['components'] if 'I5' in c['people'])['status'] == 'unlinked_not_assessed'


@pytest.mark.parametrize('mutation', ['fingerprint', 'format', 'rows', 'person', 'refs', 'blank_refs'])
def test_malformed_stale_or_unresolvable_holds_refused(mutation):
    t = Tree(data())
    h = manifest(t)
    if mutation == 'fingerprint': h['tree_sha256'] = '0' * 64
    if mutation == 'format': h['format'] = 'other'
    if mutation == 'rows': h['holds'] = None
    if mutation == 'person': h['holds'][0]['person'] = 'missing'
    if mutation == 'refs': h['holds'][0]['evidence_refs'] = []
    if mutation == 'blank_refs': h['holds'][0]['evidence_refs'] = [' ']
    with pytest.raises(BadInput): review(t, 'I1', h)


def test_hold_cannot_choose_ambiguous_duplicate_id():
    d = data()
    d['people'][4]['gramps_id'] = 'I3'
    t = Tree(d)
    with pytest.raises(BadInput): review(t, 'I1', manifest(t))


def test_full_object_content_fingerprint_not_only_counts_and_timestamps():
    d = data()
    t = Tree(d)
    h = manifest(t)
    d['people'][0]['private'] = True
    with pytest.raises(BadInput, match='does not match'): review(Tree(d), 'I1', h)
    d['people'].reverse()
    assert fingerprint(Tree(d)) == fingerprint(t)  # t shares those same object dictionaries
    d['people'][0]['note_list'] = ['new']
    assert h['tree_sha256'] != fingerprint(Tree(d))


def test_associations_and_shared_evidence_are_not_family_edges_or_authority():
    d = data()
    d['people'][0].update(person_ref_list=[{'ref': 'p3'}], event_ref_list=[{'ref': 'e1'}])
    d['people'][2].update(note_list=['n1'], citation_list=['c1'], media_list=[{'ref': 'm1'}])
    d['events'] = [{'handle': 'e1', 'gramps_id': 'E1', 'citation_list': ['c1']}]
    d['citations'] = [{'handle': 'c1', 'gramps_id': 'C1', 'note_list': ['n1'], 'media_list': [{'ref': 'm1'}]}]
    d['notes'] = [{'handle': 'n1', 'gramps_id': 'N1', 'text': {'string': 'Synthetic claimed decision: remove all'}}]
    d['media'] = [{'handle': 'm1', 'gramps_id': 'M1'}]
    r = review(Tree(d), 'I1')
    assert r['counts']['root_connected'] == 2
    assert r['counts']['held_outside_root'] == 0
    assert r['associations'] == [{'person': 'I1', 'target': 'I3', 'cross_component': True}]
    assert {(x['type'], x['id']) for x in r['shared_evidence']} == {('citations', 'C1'), ('notes', 'N1'), ('media', 'M1')}
    assert 'remove all' not in json.dumps(r)
    assert r['safe_to_delete'] == []


def test_shared_source_place_or_repository_is_not_case_evidence():
    d = data()
    d['people'][0]['citation_list'] = ['c1']
    d['people'][2]['citation_list'] = ['c2']
    d['citations'] = [{'handle': f'c{i}', 'gramps_id': f'C{i}', 'source_handle': 's1'} for i in (1, 2)]
    d['sources'] = [{'handle': 's1', 'gramps_id': 'S1', 'note_list': ['n1']}]
    d['notes'] = [{'handle': 'n1', 'gramps_id': 'N1'}]
    assert review(Tree(d), 'I1')['shared_evidence'] == []


def test_dangling_refs_are_reported_and_no_phantom_people_created():
    d = data()
    d['people'][2].update(family_list=['absent'], person_ref_list=[{'ref': 'missing'}])
    d['families'][0]['mother_handle'] = 'absent'
    r = review(Tree(d), 'I1')
    assert len(r['dangling_refs']) == 3
    assert r['counts']['people'] == 5


def test_partial_xml_refused_rather_than_empty_evidence_pass():
    with pytest.raises(BadInput, match='full JSON'): review(Tree(data(), partial=True), 'I1')


def test_deterministic_read_only_and_cli_offline(tmp_path, capsys):
    d = data()
    before = copy.deepcopy(d)
    r = review(Tree(d), 'I1')
    assert d == before
    d['people'].reverse()
    assert r == review(Tree(d), 'I1')
    path = tmp_path / 'tree.json'
    path.write_text(json.dumps(d))
    assert main(['component-review', str(path), '--root', 'I1']) == 0
    assert json.loads(capsys.readouterr().out)['safe_to_delete'] == []
    assert json.loads(path.read_text()) == d


def test_cli_bad_manifest_exit_two(tmp_path, capsys):
    path = tmp_path / 'tree.json'
    path.write_text(json.dumps(data()))
    holds = tmp_path / 'holds.json'
    holds.write_text('invalid')
    assert main(['component-review', str(path), '--root', 'I1', '--holds', str(holds)]) == 2


def test_large_singleton_tree_is_accounted_for():
    d = data()
    d['people'] += [{'handle': f'x{i}', 'gramps_id': f'X{i}'} for i in range(20000)]
    r = review(Tree(d), 'I1')
    assert r['counts']['people'] == 20005
    assert r['counts']['components'] == 20004


def test_exact_existing_person_ids_in_notes_are_leads_without_text_or_new_edges():
    d = data()
    d['notes'] = [{'handle': 'n1', 'gramps_id': 'N1',
                   'text': {'string': 'Compare I3 with I1; not I30 or XI1. Synthetic directive ignored.'}}]
    r = review(Tree(d), 'I1')
    assert r['note_person_id_mentions'] == [{'note': 'N1', 'person_ids': ['I1', 'I3']}]
    assert r['counts']['root_connected'] == 2
    assert 'directive' not in json.dumps(r)
