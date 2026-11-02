"""Read-only same-act identity hints. Evidence keys are deliberately conservative."""
from collections import defaultdict
from datetime import date as civil_date
import json
import unicodedata
from .event_duplicates import _date_key

INDIVIDUAL = {'Birth', 'Baptism', 'Death', 'Burial'}


def _fold(value):
    value = unicodedata.normalize('NFD', value.casefold())
    return ' '.join(''.join(c for c in value if not unicodedata.combining(c)).split())


def _one_edit(a, b):
    if a == b:
        return True
    if min(len(a), len(b)) < 5 or abs(len(a) - len(b)) > 1:
        return False
    for i, (x, y) in enumerate(zip(a, b)):
        if x != y:
            return a[i + 1:] == b[i + 1:] if len(a) == len(b) else (
                a[i + 1:] == b[i:] if len(a) > len(b) else a[i:] == b[i + 1:])
    return True


def _compatible_names(a, b):
    # Primary names only: aliases cannot turn two different primary identities into a match.
    x, y = a.get('primary_name') or {}, b.get('primary_name') or {}
    given_a, given_b = _fold(x.get('first_name') or ''), _fold(y.get('first_name') or '')
    sa = [_fold(s.get('surname') or '') for s in x.get('surname_list') or []]
    sb = [_fold(s.get('surname') or '') for s in y.get('surname_list') or []]
    if not given_a or given_a != given_b or not sa or len(sa) != len(sb) or not all(sa + sb):
        return False
    return all(_one_edit(u, v) for u, v in zip(sa, sb))


def _parent_pairs(tree):
    pairs = set()
    for family in tree['families'].values():
        parents = [family.get('father_handle'), family.get('mother_handle')]
        if all(parents):
            pairs.add(frozenset(parents))
        for child in family.get('child_ref_list') or []:
            for parent in parents:
                if parent and child.get('ref'):
                    pairs.add(frozenset((parent, child['ref'])))
    return pairs


def _exact_event_date(event):
    date = event.get('date') or {}
    values = date.get('dateval') or []
    # Only a complete, unqualified Gregorian day: year-only or estimated dates are weak identity evidence.
    if (len(values) != 4 or not all(values[:3]) or values[3] or date.get('modifier', 0)
            or date.get('quality', 0) or date.get('calendar', 0) or date.get('newyear', 0)):
        return None
    try:
        civil_date(values[2], values[1], values[0])
    except (TypeError, ValueError, OverflowError):
        return None
    return _date_key(date)


def check_same_act_people(tree, out, in_scope):
    """Compare primary individual events with the same exact citation, not family participants."""
    groups = defaultdict(dict)
    for person in tree['people'].values():
        for ref in person.get('event_ref_list') or []:
            event = tree['events'].get(ref.get('ref'))
            if not event or ref.get('role') != 'Primary' or event.get('type') not in INDIVIDUAL:
                continue
            date, place = _exact_event_date(event), event.get('place')
            if not date or place not in tree['places']:
                continue
            # Person-level citations alone do not say which event they substantiate.
            for handle in event.get('citation_list') or []:
                cite = tree['citations'].get(handle) or {}
                source, page = cite.get('source_handle'), cite.get('page')
                if source not in tree['sources'] or not isinstance(page, str) or not page.strip():
                    continue
                sig = (event['type'], date, place, source, page)
                groups[sig][person['handle']] = person
    relatives = _parent_pairs(tree)
    for sig, people in sorted(groups.items()):
        ordered = sorted(people.values(), key=lambda p: (p.get('gramps_id') or '', p['handle']))
        for i, a in enumerate(ordered):
            for b in ordered[i + 1:]:
                if frozenset((a['handle'], b['handle'])) in relatives:
                    continue
                if not _compatible_names(a, b):
                    continue
                if a.get('gender') in (0, 1) and b.get('gender') in (0, 1) and a['gender'] != b['gender']:
                    continue
                if not any(in_scope('people', p['handle']) for p in (a, b)):
                    continue
                ids = [p.get('gramps_id') or p['handle'] for p in (a, b)]
                evidence = json.dumps(dict(type=sig[0], date=json.loads(sig[1]), place=sig[2],
                                           source=sig[3], page=sig[4]), sort_keys=True, ensure_ascii=False)
                out.add('same-act-person', ','.join(ids),
                        'same primary individual act; ' + evidence + '; candidate only; compare full act, '
                        'parents, spouses, notes and privacy; backup and checkpoint required before any merge; '
                        'no survivor selected, nothing changed')
