"""Synthetic place safety tests. No real tree calls."""
import copy

import pytest
from test_apply import MergingFake, R, run
from gwt_apply.ops import validate
from gwt_apply.client import Client
from gwt_apply.ops import Applier


class Places(MergingFake):
    def _merge_by_kind(self, ns, win, lose, w, loser):
        if ns != 'places':
            return super()._merge_by_kind(ns, win, lose, w, loser)
        win['alt_names'] = win.get('alt_names', []) + [lose['name']]
        for event in self.store['events'].values():
            if event.get('place') == loser:
                event['place'] = w
        for place in self.store['places'].values():
            for ref in place.get('placeref_list') or []:
                if ref.get('ref') == loser:
                    ref['ref'] = w


def tree():
    fake = Places()
    for h, name in [('placewinner123', 'Synthetic city'), ('placeloser456', 'Synthetic city alias')]:
        fake.add('places', dict(handle=h, name={'value': name}, place_type='City', lat='1', long='2'))
    fake.add('events', dict(handle='eventrecord789', type='Birth', place='placeloser456'))
    return fake


def merge():
    return dict(op='merge_place', key='merge', ruling=R, winner='P0001', loser='P0002', reason='same documented place')


def delete(place='P0002'):
    return dict(op='delete_place', key='delete', ruling=R, place=place, reason='reviewed empty duplicate',
                expect_unreferenced=True)


def test_validation_requires_intent():
    op = delete()
    assert validate([op]) == []
    op['expect_unreferenced'] = 1
    assert validate([op])
    for value in (False, None, 'true'):
        op['expect_unreferenced'] = value
        assert validate([op])
    assert validate([dict(merge(), loser='P0001')])


def test_merge_moves_events_and_keeps_loser_record():
    f = tree()
    result = run(f, [merge()])[0]
    assert result.status == 'done', result.actions
    assert f.store['events']['eventrecord789']['place'] == 'placewinner123'
    assert 'placeloser456' not in f.store['places']
    assert any('Synthetic city alias' in n['text']['string'] for n in f.store['notes'].values())
    assert run(f, [merge()])[0].status == 'skip'


def test_merge_dry_run_never_writes():
    f = tree()
    before = copy.deepcopy(f.store)
    result = run(f, [merge()], dry_run=True)[0]
    assert result.status == 'done', result.actions
    assert f.store == before
    assert not f.writes()


@pytest.mark.parametrize('key,value', [('place_type', 'Parish'), ('lat', '3'), ('long', '4'),
                                      ('code', '123'), ('private', True),
                                      ('placeref_list', [{'ref': 'third'}])])
def test_merge_refuses_lost_facts_and_different_parents(key, value):
    f = tree()
    f.store['places']['placeloser456'][key] = value
    before = copy.deepcopy(f.store)
    result = run(f, [merge()])[0]
    assert result.status == 'conflict', result.actions
    assert f.store == before
    assert not f.writes()


@pytest.mark.parametrize('first,second', [('placewinner123', 'placeloser456'), ('placeloser456', 'placewinner123')])
def test_merge_refuses_containment(first, second):
    f = tree()
    f.store['places'][first]['placeref_list'] = [{'ref': second}]
    assert run(f, [merge()])[0].status == 'conflict'
    assert not f.writes()


def test_delete_refuses_references_and_missing_place():
    f = tree()
    assert run(f, [delete()])[0].status == 'conflict'
    assert run(f, [delete('P9999')])[0].status == 'conflict'
    assert not f.writes()


def test_delete_empty_place_has_dry_run_and_real_delete():
    f = tree()
    f.store['events']['eventrecord789']['place'] = 'placewinner123'
    before = copy.deepcopy(f.store)
    assert run(f, [delete()], dry_run=True)[0].status == 'done'
    assert f.store == before
    assert not f.writes()
    assert run(f, [delete()])[0].status == 'done'
    assert 'placeloser456' not in f.store['places']


def test_delete_after_planned_event_move():
    f = tree()
    move = dict(op='update_event', key='move', ruling=R, event='E0001', set={'place': 'P0001'})
    results = run(f, [move, delete()], dry_run=True)
    assert [r.status for r in results] == ['done', 'done'], [r.actions for r in results]
    assert 'placeloser456' in f.store['places']


def test_merge_moves_child_place_and_dry_run_models_next_step():
    f = tree()
    f.add('places', dict(handle='childrecord123', name={'value': 'Child'}, place_type='Hamlet',
                         placeref_list=[{'ref': 'placeloser456'}]))
    move = dict(op='update_place', key='next', ruling=R, place='P0003', set={'enclosed_by': 'P0001'})
    results = run(f, [merge(), move], dry_run=True)
    assert [r.status for r in results] == ['done', 'skip'], [r.actions for r in results]
    assert f.store['places']['childrecord123']['placeref_list'] == [{'ref': 'placeloser456'}]
    assert run(f, [merge()])[0].status == 'done'
    assert f.store['places']['childrecord123']['placeref_list'] == [{'ref': 'placewinner123'}]


def test_unsupported_reference_and_transitive_loop_are_refused():
    f = tree()
    f.add('people', dict(handle='personrecord123', primary_name={'first_name': 'X'}, odd='placeloser456'))
    assert run(f, [merge()])[0].status == 'conflict'
    assert not f.writes()
    f = tree()
    f.add('places', dict(handle='middlerecord123', name={'value': 'Middle'},
                         placeref_list=[{'ref': 'placeloser456'}]))
    f.store['places']['placewinner123']['placeref_list'] = [{'ref': 'middlerecord123'}]
    assert run(f, [merge()])[0].status == 'conflict'
    assert not f.writes()


def test_merge_dry_run_models_event_before_following_update():
    f = tree()
    op = dict(op='update_event', key='next-event', ruling=R, event='E0001', set={'place': 'P0001'})
    results = run(f, [merge(), op], dry_run=True)
    assert [r.status for r in results] == ['done', 'skip'], [r.actions for r in results]
    assert f.store['events']['eventrecord789']['place'] == 'placeloser456'


def test_containment_guard_even_when_direct_parent_sets_match():
    f = tree()
    f.add('places', dict(handle='middlerecord123', name={'value': 'Middle'},
                         placeref_list=[{'ref': 'placewinner123'}]))
    for handle in ('placewinner123', 'placeloser456'):
        f.store['places'][handle]['placeref_list'] = [{'ref': 'middlerecord123'}]
    assert run(f, [merge()])[0].status == 'conflict'
    assert not f.writes()


def test_place_merge_invalidates_live_snapshot_reads_and_lists():
    f = tree()
    client = Client('https://gw.example', 'u', 'pw', tree='tree-test', dry_run=False, session=f)
    client.login()
    snap = {'tree': 'tree-test', 'objects': {}}
    from fake_api import NS
    for ns, (cls, _) in NS.items():
        snap['objects'][cls] = copy.deepcopy(list(f.store[ns].values()))
    client.use_snapshot(snap)
    client.list_all('Event', ['place'])
    assert Applier(client, retries=1, retry_wait=0).run([merge()])[0].status == 'done'
    assert client.get('Event', 'eventrecord789')['place'] == 'placewinner123'
    assert client.list_all('Event', ['place'])[0]['place'] == 'placewinner123'
