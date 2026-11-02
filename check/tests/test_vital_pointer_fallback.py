"""Synthetic pointer checks: an intentional Gramps fallback is not a wrong vital event."""
import copy

import pytest

from gwt_check import checks, selftest
from gwt_check.tree import Tree


def pointer_rows(data):
    findings, _ = checks.run_checks(Tree(data))
    return {row['key'] for row in findings if row['check'] == 'birth-death-pointer'}


def fallback_data(field, event_type):
    data = selftest.clean_tree()
    person = next((p for p in data['people'] if p['gramps_id'] == 'I0004'), None)
    assert person is not None
    ref = person['event_ref_list'][person[field]]
    event = next((e for e in data['events'] if e['handle'] == ref['ref']), None)
    assert event is not None
    event['type'] = event_type
    return data, person, ref, event


@pytest.mark.parametrize('field,event_type', [
    ('birth_ref_index', 'Baptism'), ('birth_ref_index', 'Christening'), ('birth_ref_index', 'Stillbirth'),
    ('death_ref_index', 'Burial'), ('death_ref_index', 'Cremation'), ('death_ref_index', 'Cause Of Death'),
    ('death_ref_index', 'Probate'), ('death_ref_index', 'Stillbirth'),
])
def test_primary_fallback_is_valid_when_exact_vital_is_absent(field, event_type):
    data, _, _, _ = fallback_data(field, event_type)
    assert not pointer_rows(data)


@pytest.mark.parametrize('field,event_type', [('birth_ref_index', 'Baptism'), ('death_ref_index', 'Burial')])
def test_exact_primary_vital_takes_precedence(field, event_type):
    data, person, _, fallback = fallback_data(field, event_type)
    exact = copy.deepcopy(fallback)
    exact.update(handle='hE9999', gramps_id='E9999', type='Birth' if field == 'birth_ref_index' else 'Death')
    data['events'].append(exact)
    person['event_ref_list'].append({'ref': exact['handle'], 'role': 'Primary'})
    assert f'I0004 {field}' in pointer_rows(data)


@pytest.mark.parametrize('field,event_type', [('birth_ref_index', 'Baptism'), ('death_ref_index', 'Burial')])
@pytest.mark.parametrize('role', ['Witness', 'Godparent'])
def test_non_primary_fallback_is_still_wrong(field, event_type, role):
    data, _, ref, _ = fallback_data(field, event_type)
    ref['role'] = role
    assert f'I0004 {field}' in pointer_rows(data)


@pytest.mark.parametrize('field,event_type', [('birth_ref_index', 'Burial'), ('death_ref_index', 'Baptism'),
                                             ('birth_ref_index', 'Adult Christening'), ('death_ref_index', 'Residence')])
def test_wrong_side_or_unrecognised_event_is_still_wrong(field, event_type):
    data, _, _, _ = fallback_data(field, event_type)
    assert f'I0004 {field}' in pointer_rows(data)


@pytest.mark.parametrize('index', [-2, 99])
def test_invalid_index_is_still_wrong(index):
    data, person, _, _ = fallback_data('birth_ref_index', 'Baptism')
    person['birth_ref_index'] = index
    assert 'I0004 birth_ref_index' in pointer_rows(data)


def test_missing_target_is_still_wrong():
    data, _, ref, _ = fallback_data('birth_ref_index', 'Baptism')
    ref['ref'] = 'missing'
    assert 'I0004 birth_ref_index' in pointer_rows(data)


def test_absent_pointer_without_exact_vital_is_not_required_to_point_to_fallback():
    data, person, _, _ = fallback_data('birth_ref_index', 'Baptism')
    person['birth_ref_index'] = -1
    assert not pointer_rows(data)
