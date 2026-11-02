"""Same-person duplicate candidates, without selecting a survivor or editing data."""
from __future__ import annotations

import json
from collections import defaultdict


def _date_key(date):
    """Keep calendar, precision, modifier and every stored qualifier distinct."""
    if not isinstance(date, dict):
        return None
    value = date.get('dateval')
    if not isinstance(value, list) or len(value) != 4 or not any(value[:3]):
        return None
    return json.dumps(date, sort_keys=True, ensure_ascii=False)


def _groups(rows):
    grouped = defaultdict(dict)
    for key, obj in rows:
        if key is not None:
            grouped[key][obj['handle']] = obj
    return [list(group.values()) for group in grouped.values() if len(group) > 1]


def check_person_duplicates(tree, out, in_scope):
    """Only compare linked objects of one person; matching is a review hint."""
    for person in tree['people'].values():
        pid = person.get('gramps_id') or person['handle']
        events = []
        linked_events = {}
        for ref in person.get('event_ref_list') or []:
            event = tree['events'].get(ref.get('ref'))
            if event:
                linked_events[event['handle']] = event
                date = _date_key(event.get('date'))
                place = event.get('place')
                key = (event.get('type'), date, place, ref.get('role'))
                if date and place in tree['places'] and event.get('type'):
                    events.append((key, event))
        for group in _groups(events):
            if in_scope('people', person['handle']) or any(in_scope('events', e['handle']) for e in group):
                ids = sorted(e.get('gramps_id') or e['handle'] for e in group)
                out.add('same-person-event', f"{pid} {','.join(ids)}",
                        'same stored type, date, place and role; review descriptions, citations, notes and privacy; '
                        'no survivor selected, nothing changed')
        # Citations directly on the person and on its events are candidates only
        # within the SAME owning object. Different event owners are not collapsed.
        owners = [person] + list(linked_events.values())
        for owner in owners:
            citations = []
            for handle in owner.get('citation_list') or []:
                citation = tree['citations'].get(handle)
                if not citation:
                    continue
                source = citation.get('source_handle')
                page = citation.get('page')
                if source in tree['sources'] and isinstance(page, str) and page.strip():
                    key = (source, page, _date_key(citation.get('date')))
                    citations.append((key, citation))
            for group in _groups(citations):
                related = [('people', person['handle'])]
                related += [('people' if owner is person else 'events', owner['handle'])]
                related += [('citations', c['handle']) for c in group]
                if any(in_scope(t, h) for t, h in related):
                    ids = sorted(c.get('gramps_id') or c['handle'] for c in group)
                    oid = owner.get('gramps_id') or owner['handle']
                    out.add('same-person-citation', f"{pid} {oid} {','.join(ids)}",
                            'same owner, source, literal page and stored date; review confidence, media, notes '
                            'and privacy; no survivor selected, nothing changed')
