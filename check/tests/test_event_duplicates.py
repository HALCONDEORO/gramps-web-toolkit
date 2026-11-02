"""Invented controls: same kind is not proof that two objects can be deleted."""
import copy
import pytest
from gwt_check import checks, selftest
from gwt_check.tree import Tree


def fixture():
    data = selftest.clean_tree()
    event = selftest._get(data, 'events', 'E0001')
    twin = copy.deepcopy(event)
    twin.update(handle='hE0900', gramps_id='E0900')
    data['events'].append(twin)
    person = selftest._get(data, 'people', 'I0001')
    person['event_ref_list'].append(selftest._ref('hE0900', role='Primary'))
    return data, event, twin, person


def findings(data, scope=None, name='same-person-event'):
    return [r for r in checks.run_checks(Tree(data), scope=scope)[0] if r['check'] == name]


def test_report_is_deterministic_and_does_not_change_tree():
    data, _, _, person = fixture()
    before = copy.deepcopy(data)
    [row] = findings(data)
    assert row['key'] == 'I0001 E0001,E0900'
    assert 'nothing changed' in row['detail']
    assert data == before
    person['event_ref_list'].reverse()
    assert findings(data) == [row]


@pytest.mark.parametrize('field,value', [('type', 'Baptism'), ('place', 'hP0002')])
def test_different_kind_or_place_is_not_duplicate(field, value):
    data, _, twin, _ = fixture()
    twin[field] = value
    assert findings(data) == []


@pytest.mark.parametrize('field,value', [('modifier', 1), ('quality', 1), ('calendar', 1),
    ('newyear', 1), ('dateval', [0, 3, 1900, False]), ('dateval', [1, 3, 1900, True])])
def test_date_qualifiers_and_precision_remain_distinct(field, value):
    data, _, twin, _ = fixture()
    twin['date'][field] = value
    assert findings(data) == []


def test_repeated_reference_is_not_two_objects_and_roles_are_distinct():
    data, _, _, person = fixture()
    person['event_ref_list'][-1]['ref'] = 'hE0001'
    assert findings(data) == []
    person['event_ref_list'][-1].update(ref='hE0900', role='Witness')
    assert findings(data) == []


@pytest.mark.parametrize('scope', [set(), {('events', 'unrelated')}])
def test_unrelated_scope_is_quiet(scope):
    assert findings(fixture()[0], scope) == []


@pytest.mark.parametrize('scope', [{('events', 'hE0900')}, {('people', 'hI0001')}])
def test_related_scope_is_reported(scope):
    assert len(findings(fixture()[0], scope)) == 1


def test_missing_date_or_place_is_not_identity():
    data, event, twin, _ = fixture()
    for obj in (event, twin):
        obj['date'] = selftest._date()
    assert findings(data) == []
    for obj in (event, twin):
        obj['date'] = selftest._date(1900)
        obj['place'] = ''
    assert findings(data) == []


def test_citations_on_same_owner_are_reported_not_merged():
    data, event, _, _ = fixture()
    citation = copy.deepcopy(data['citations'][0])
    citation.update(handle='hC0900', gramps_id='C0900', private=True, confidence=0)
    data['citations'].append(citation)
    event['citation_list'].append('hC0900')
    before = copy.deepcopy(data)
    [row] = findings(data, name='same-person-citation')
    assert row['key'] == 'I0001 E0001 C0001,C0900'
    assert data == before
    citation['page'] = 'folio 1v'
    assert findings(data, name='same-person-citation') == []


def test_citations_of_different_owners_are_not_collapsed():
    data, _, twin, _ = fixture()
    citation = copy.deepcopy(data['citations'][0])
    citation.update(handle='hC0900', gramps_id='C0900')
    data['citations'].append(citation)
    twin['citation_list'] = ['hC0900']
    assert findings(data, name='same-person-citation') == []


def test_event_without_date_still_exposes_duplicate_citations():
    data, event, _, _ = fixture()
    event['date'] = selftest._date()
    citation = copy.deepcopy(data['citations'][0])
    citation.update(handle='hC0900', gramps_id='C0900')
    data['citations'].append(citation)
    event['citation_list'] = ['hC0001', 'hC0900']
    assert len(findings(data, name='same-person-citation')) == 1


def test_unknown_place_does_not_create_event_match():
    data, event, twin, _ = fixture()
    event['place'] = twin['place'] = 'missing'
    assert findings(data) == []


def test_other_person_with_same_event_is_not_duplicate_on_either_person():
    data, _, _, person = fixture()
    person['event_ref_list'].pop()
    other = selftest._get(data, 'people', 'I0002')
    other['event_ref_list'].append(selftest._ref('hE0900', role='Primary'))
    assert findings(data) == []

def test_citation_unknown_date_qualifiers_are_not_collapsed():
    data, event, _, _ = fixture()
    first = data['citations'][0]
    first['date'] = selftest._date()
    citation = copy.deepcopy(first)
    citation.update(handle='hC0900', gramps_id='C0900')
    citation['date']['modifier'] = 1
    data['citations'].append(citation)
    event['citation_list'] = ['hC0001', 'hC0900']
    assert findings(data, name='same-person-citation') == []
