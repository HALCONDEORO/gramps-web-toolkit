"""Synthetic repeated-POST shapes, not real family records or identifiers."""
import copy
import pytest
from gwt_check import checks, selftest
from gwt_check.tree import Tree


def fixture(count=2, kind='Baptism'):
    data = selftest.clean_tree()
    for i in range(count):
        person = selftest._new_person(data, f'I{910+i}', 'Invented', 'Alphard')
        selftest._event(data, person, f'E{910+i}', kind, (1800, 1, 1))
        event = data['events'][-1]
        event['citation_list'] = ['hC0001']
    return data


def hits(data, scope=None):
    return [r for r in checks.run_checks(Tree(data), scope=scope)[0] if r['check'] == 'same-act-person']


def test_distinct_post_objects_same_act_and_spelling_variant():
    d = fixture()
    d['people'][-1]['primary_name']['surname_list'][0]['surname'] = 'Alphars'
    before = copy.deepcopy(d)
    [r] = hits(d)
    assert r['key'] == 'I910,I911'
    assert 'backup and checkpoint required' in r['detail']
    assert d == before
    d['people'].reverse(); d['events'].reverse()
    assert hits(d) == [r]


@pytest.mark.parametrize('kind', ['Birth', 'Baptism', 'Death', 'Burial'])
def test_each_individual_act_can_support_review(kind):
    assert len(hits(fixture(kind=kind))) == 1


def test_triple_post_reports_three_pairs_not_an_automatic_survivor():
    rows = hits(fixture(3))
    assert {r['key'] for r in rows} == {'I910,I911', 'I910,I912', 'I911,I912'}
    assert all('no survivor selected' in r['detail'] for r in rows)


@pytest.mark.parametrize('field,value', [('place', 'hP0002'), ('type', 'Death')])
def test_distinct_event_is_not_same_act(field, value):
    d = fixture(); d['events'][-1][field] = value
    assert hits(d) == []


@pytest.mark.parametrize('field,value', [('modifier', 1), ('quality', 1), ('calendar', 1),
    ('newyear', 1), ('dateval', [0, 1, 1800, False]), ('dateval', [1, 1, 1800, True]),
    ('dateval', [2, 1, 1800, False])])
def test_different_or_uncertain_dates_are_not_same_act(field, value):
    d = fixture(); d['events'][-1]['date'][field] = value
    assert hits(d) == []


@pytest.mark.parametrize('page', ['folio 2', 'folio 1v', 'Folio 1', ''])
def test_same_name_different_literal_page_is_not_same_record(page):
    d = fixture()
    cite = copy.deepcopy(d['citations'][0]); cite.update(handle='hC900', gramps_id='C900', page=page)
    d['citations'].append(cite); d['events'][-1]['citation_list'] = ['hC900']
    assert hits(d) == []


def test_different_source_unknown_place_or_missing_citation_stays_quiet():
    for change in ('source', 'place', 'cite'):
        d = fixture()
        if change == 'source':
            cite = copy.deepcopy(d['citations'][0]); cite.update(handle='hC900', source_handle='missing')
            d['citations'].append(cite); d['events'][-1]['citation_list'] = ['hC900']
        elif change == 'place':
            d['events'][-1]['place'] = 'missing'
        else:
            d['events'][-1]['citation_list'] = []
        assert hits(d) == []


def test_shared_marriage_parent_couple_and_witnesses_are_not_duplicate_people():
    assert hits(fixture(kind='Marriage')) == []
    d = fixture(); a, b = d['people'][-2:]
    d['families'].append(selftest._base('F900', father_handle=a['handle'], mother_handle=b['handle'], child_ref_list=[]))
    assert hits(d) == []
    d['families'].pop()
    b['event_ref_list'][0]['role'] = 'Witness'
    assert hits(d) == []


def test_parent_child_pair_is_not_a_same_act_identity_hint():
    d = fixture(); a, b = d['people'][-2:]
    d['families'].append(selftest._base('F900', father_handle=a['handle'], mother_handle='',
                                     child_ref_list=[selftest._ref(b['handle'])]))
    assert hits(d) == []


@pytest.mark.parametrize('field,value', [('first_name', 'Different'), ('surname', 'Othername')])
def test_different_primary_name_is_not_identity(field, value):
    d = fixture(); name = d['people'][-1]['primary_name']
    if field == 'surname': name['surname_list'][0]['surname'] = value
    else: name[field] = value
    assert hits(d) == []


def test_known_gender_conflict_is_not_identity():
    d = fixture(); d['people'][-1]['gender'] = 0; d['people'][-2]['gender'] = 1
    assert hits(d) == []


def test_scope_and_info_status():
    d = fixture()
    assert hits(d, set()) == []
    assert len(hits(d, {('people', 'hI910')})) == 1
    assert 'same-act-person' in checks.INFO_CHECKS


def test_repeated_burial_on_one_person_uses_existing_same_person_report():
    d = fixture(1, kind='Burial'); p = d['people'][-1]
    e = copy.deepcopy(d['events'][-1]); e.update(handle='hE999', gramps_id='E999')
    d['events'].append(e); p['event_ref_list'].append(selftest._ref('hE999', role='Primary'))
    assert hits(d) == []
    assert any(r['check'] == 'same-person-event' and 'I910' in r['key']
               for r in checks.run_checks(Tree(d))[0])


def test_invalid_calendar_day_is_not_exact_evidence():
    d = fixture()
    for e in d['events'][-2:]: e['date']['dateval'] = [31, 2, 1800, False]
    assert hits(d) == []


def test_duplicate_citation_objects_same_literal_reference_still_match():
    d = fixture()
    c = copy.deepcopy(d['citations'][0]); c.update(handle='hC901', gramps_id='C901')
    d['citations'].append(c); d['events'][-1]['citation_list'] = ['hC901']
    assert len(hits(d)) == 1


def test_same_citation_only_on_person_is_not_an_event_claim():
    d = fixture()
    for p, e in zip(d['people'][-2:], d['events'][-2:]):
        e['citation_list'] = []; p['citation_list'] = ['hC0001']
    assert hits(d) == []


def test_different_existing_sources_do_not_match():
    d = fixture()
    source = copy.deepcopy(d['sources'][0]); source.update(handle='hS900', gramps_id='S900')
    d['sources'].append(source)
    cite = copy.deepcopy(d['citations'][0]); cite.update(handle='hC900', gramps_id='C900', source_handle='hS900')
    d['citations'].append(cite); d['events'][-1]['citation_list'] = ['hC900']
    assert hits(d) == []
