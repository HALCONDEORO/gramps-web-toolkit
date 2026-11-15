"""Invented descriptions are clues, never genealogical proof."""
import copy
import pytest
from gwt_check import checks,selftest
from gwt_check.tree import Tree

def case(description='Exampleville, Marriage Alex Example x Robin Sample.',kind='Birth'):
    data=selftest.clean_tree();event=selftest._get(data,'events','E0001')
    event.update(type=kind,description=description)
    return data,event

def rows(data,scope=None):
    return [r for r in checks.run_checks(Tree(data),scope=scope)[0] if r['check']=='event-type-description']

def test_grouped_by_event_not_number_of_owners_and_read_only():
    data,event=case();person=selftest._get(data,'people','I0002')
    person['event_ref_list'].append(selftest._ref(event['handle'],role='Primary'))
    before=copy.deepcopy(data);result=rows(data)
    assert len(result)==1 and result[0]['key']=='E0001'
    assert 'Marriage' in result[0]['detail'] and 'original' in result[0]['detail']
    assert 'I0001' in result[0]['detail'] and 'I0002' in result[0]['detail']
    assert 'event-type-description' in checks.INFO_CHECKS and data==before

@pytest.mark.parametrize('text,kind',[('Baptême Alex','Baptism'),('Exampleville, Sepultura Alex','Burial'),('Matrimonio Alex','Marriage')])
def test_explicit_accented_headings(text,kind):
    data,event=case(text);assert kind in rows(data)[0]['detail']

@pytest.mark.parametrize('text', ['No marriage Alex','Exampleville, not Marriage Alex','Possible Baptism Alex',
    'Marriage maybe Alex','Marriage perhaps Alex','Marriage probablemente Alex',
    'Marriage hipotético Alex','Marriage Alex (uncertain)','Marriage douteux Alex',
    'Marriage? Alex','Birth Alex; marriage of parents','Certificate of marriage Alex',
    'Alex attended a marriage','Exampleville, Alex Marriage','', 'Baptismal sponsor Alex'])
def test_ambiguous_negated_incidental_or_unrecognised_is_not_classified(text):
    assert rows(case(text)[0])==[]

def test_matching_type_is_quiet_and_unsupported_type_is_not_overwritten():
    assert rows(case(kind='Marriage')[0])==[]
    assert rows(case(kind='Custom Event')[0])==[]

def test_unrelated_scope_quiet_event_scope_reports():
    data,event=case();assert rows(data,set())==[]
    assert len(rows(data,{('events',event['handle'])}))==1
