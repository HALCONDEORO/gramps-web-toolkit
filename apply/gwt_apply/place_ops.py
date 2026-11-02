"""Conservative place merge/delete operations. Default CLI mode remains read-only."""
import copy
import json


def _parents(place):
    return {r.get('ref') for r in place.get('placeref_list') or [] if r.get('ref')}


def _inside(applier, place, other):
    seen, todo = set(), list(_parents(place))
    while todo:
        handle = todo.pop()
        if handle == other['handle']:
            return True
        if handle not in seen:
            seen.add(handle)
            todo.extend(_parents(applier.c.get('Place', handle) or {}))
    return False


def _check_merge(applier, winner, loser, conflict, fold):
    if winner['handle'] == loser['handle']:
        raise conflict('winner and loser are the same place')
    types = {fold(str(o.get('place_type') or '')) for o in (winner, loser)} - {'', 'unknown'}
    if len(types) > 1:
        raise conflict('places have different types; review them before merging')
    if _inside(applier, winner, loser) or _inside(applier, loser, winner):
        raise conflict('one place encloses the other; merging would create a loop')
    if _parents(winner) != _parents(loser):
        raise conflict('places have different parent sets; review their hierarchy before merging')
    # The merge keeps the winner's scalar fields. Refuse to drop a fact or weaken privacy.
    for key in ('lat', 'long', 'code', 'private'):
        if loser.get(key) not in (None, '') and winner.get(key) != loser[key]:
            raise conflict(f'places differ in {key}; review before merging')


def _moving(loser, conflict, referrers):
    holders = sorted(referrers('Place', loser['handle']))
    if any(kind.lower() not in ('event', 'place') for kind, _ in holders):
        raise conflict('place has an unsupported reference kind; review before merging')
    return holders


def _simulate(applier, winner, loser, holders):
    for field in ('alt_names', 'citation_list', 'note_list', 'media_list', 'attribute_list', 'tag_list'):
        have = winner.get(field) or []
        winner[field] = have + [value for value in loser.get(field) or [] if value not in have]
    if loser.get('name') and loser['name'] not in winner['alt_names']:
        winner['alt_names'].append(loser['name'])
    applier.c.pending[('Place', winner['handle'])] = copy.deepcopy(winner)
    for kind, handle in holders:
        cls = 'Event' if kind.lower() == 'event' else 'Place'
        obj = copy.deepcopy(applier.c.get(cls, handle))
        if cls == 'Event':
            obj['place'] = winner['handle']
        else:
            obj['placeref_list'] = [dict(r, ref=winner['handle']) if r.get('ref') == loser['handle'] else r
                                   for r in obj.get('placeref_list') or []]
        applier.c.pending[(cls, handle)] = obj


def merge_place(applier, op, conflict, fold, referrers, merge_done, merge_note, show_merge):
    if merge_done('Place', op):
        return
    winner = copy.deepcopy(applier.obj('Place', op['winner']))
    loser = copy.deepcopy(applier.obj('Place', op['loser']))
    _check_merge(applier, winner, loser, conflict, fold)
    holders = _moving(loser, conflict, referrers)
    # Keep the whole loser record in the research note, not only its display name.
    record = {k: v for k, v in loser.items() if k not in ('_read', '_snap', 'backlinks', 'extended', 'profile')}
    merge_note(op, 'Place', f'Place {applier.label(loser)}: {json.dumps(record, ensure_ascii=False)}')
    winner = applier.obj('Place', op['winner'])
    winner.setdefault('_class', 'Place')
    loser.setdefault('_class', 'Place')
    show_merge(winner, loser)
    applier.actions.extend(f'  {kind} {handle}: place {applier.label(loser)} -> {applier.label(winner)}'
                           for kind, handle in holders)
    # Simulate the server's rewritten references for every following dry-run operation.
    if applier.c.dry_run:
        _simulate(applier, winner, loser, holders)
    applier.c.merge('Place', winner['handle'], loser['handle'])
    applier.wrote = True


def delete_place(applier, op, conflict, stable_handle, referrers):
    ref = op['place']
    handle = applier.keymap.get(ref[4:]) or stable_handle(ref[4:]) if ref.startswith('key:') else \
        applier.c.handle_for_gramps_id('Place', ref)
    if handle is None or not applier.c.exists('Place', handle):
        raise conflict(f'no Place {ref}; deletion is not silently assumed complete')
    obj = applier.c.get('Place', handle)
    held = _moving(obj, conflict, referrers)
    # Live backlinks can still name a reference removed by an earlier planned update.
    for kind, h in held:
        cls = 'Event' if kind.lower() == 'event' else 'Place'
        if (cls, h) in applier.c.deleted:
            continue
        current = applier.c.pending.get((cls, h))
        if current is None or json.dumps(handle) in json.dumps(current):
            raise conflict(f'Place {ref} is still referenced by {kind} {h}; nothing deleted')
    applier.actions.append(f'delete Place {ref}: {json.dumps(obj, ensure_ascii=False)}')
    applier.reasons.append(f'missing: delete Place {ref}')
    applier.c.remove('Place', handle)
    applier.wrote = True
