"""Offline statistics of explicitly deceased people's own structured occupations."""
import collections
import json
import gwt_report as R
from gwt_pedigree_reports import deceased


def event_type(value):
    return R.text_of(value)



def event_year(event):
    d = event.get('date') or {}
    vals = d.get('dateval') or []
    if (d.get('calendar', 0) != 0 or d.get('modifier', 0) != 0 or
            len(vals) != 4 or type(vals[2]) is not int or vals[2] <= 0):
        return None
    return vals[2]


def century(event):
    year = event_year(event)
    return (year - 1) // 100 + 1 if year else None


def category_map(path):
    if not path:
        return {}
    def unique(pairs):
        out = {}
        for key, value in pairs:
            if key in out:
                raise ValueError('duplicate category literal')
            out[key] = value
        return out
    data = json.loads(R.local_path(path).read_text(encoding='utf-8'), object_pairs_hook=unique)
    if not isinstance(data, dict) or any(not k or not isinstance(v, str) or not v.strip()
                                         for k, v in data.items()):
        raise ValueError('categories must map exact nonempty literals to categories')
    return data


def occupation_row(T, handle, person, eh, event, literal, citations, paths, categories, include_private):
    """Format one accepted occupation without changing its original literal."""
    place = T['places'].get(event.get('place'), {})
    if place.get('private') and not include_private:place = {}
    return dict(person=person.get('gramps_id', handle), event=event.get('gramps_id', eh),
                     literal=literal, category=categories.get(literal), citations=citations,
                     place_id=place.get('gramps_id'), place=place.get('title') or None,
                     century=century(event),
                     period25=((event_year(event)-1)//25)*25+1 if event_year(event) else None,
                     period50=((event_year(event)-1)//50)*50+1 if event_year(event) else None,
                     generations=sorted(set(paths.get(handle, []))))


def occupation_rows(T, handle, person, paths, categories, include_private, excluded):
    """Collect one allowed person's events in original reference order."""
    from gwt_pedigree_reports import existing_citations, conflicted
    rows = []
    seen = set()
    for ref in person.get('event_ref_list', []):
        eh = ref.get('ref')
        if eh in seen:
            continue
        event = T['events'].get(eh, {})
        if conflicted(T, event):
            excluded['conflicting_occupation'] += 1
            continue
        if event_type(event.get('type')) != 'Occupation':
            continue
        if ref.get('role') != 'Primary':
            excluded['non_primary_occupation'] += 1
            continue
        if (event.get('private') or ref.get('private')) and not include_private:
            excluded['private_event'] += 1
            continue
        seen.add(eh)
        literal = event.get('description')
        if not isinstance(literal, str) or not literal.strip() or literal.strip() == '[REDACTED]':
            excluded['missing_or_redacted_literal'] += 1
            continue
        citations = existing_citations(T, event.get('citation_list', []), include_private)
        if not citations:
            excluded['uncited_occupation'] += 1
            continue
        rows.append(occupation_row(T, handle, person, eh, event, literal, citations, paths, categories, include_private))
    return rows


def occupation_dimensions(rows):
    """Aggregate original literals by each independent report dimension."""
    dimensions = {}
    for dim in ['category', 'place_id', 'century', 'period25', 'period50', 'generations']:
        counter = collections.Counter()
        for row in rows:
            values = row[dim] if dim == 'generations' else [row[dim]]
            for value in values or [None]:
                counter[(row['literal'], value)] += 1
        dimensions[dim] = [dict(literal=k[0], value=k[1], count=v)
                           for k, v in sorted(counter.items(), key=lambda kv: str(kv[0]))]
    return dimensions


def report(T, root=None, categories=None, include_private=False):
    categories = categories or {}
    from gwt_pedigree_reports import ancestry
    slots, issues = ancestry(T, root, include_private=include_private)
    paths = collections.defaultdict(list)
    for slot, handle in slots.items():
        if slot != 1:paths[handle].append(slot.bit_length()-1)
    truncated = any(x['reason']=='generation-limit' for x in issues)
    rows = []
    excluded = collections.Counter()
    for handle, person in T['people'].items():
        if handle not in paths:
            excluded['not_direct_ancestor'] += 1
            continue
        if not deceased(T, person, include_private):
            excluded['not_explicitly_deceased'] += 1
            continue
        if person.get('private') and not include_private:
            excluded['private_person'] += 1
            continue
        rows.extend(occupation_rows(T, handle, person, paths, categories, include_private, excluded))
    frequencies = collections.Counter(r['literal'] for r in rows)
    dimensions = occupation_dimensions(rows)
    return dict(schema_version=1, scope='offline explicitly deceased structured primary occupations only',
                unit='one person-event pair; not number of distinct people', root=root,
                generation_paths_truncated=truncated, generations='parent-link distance; root=0; observed paths up to generation12, cutoff reported',
                excluded=dict(excluded), excluded_links=issues, rows=rows,
                frequencies=[dict(literal=k, count=v) for k, v in sorted(frequencies.items())],
                by=dimensions,
                caveat='No free-text extraction, inferred occupation, relationship proof or write authority. '
                       'Missing dates/places/generations remain unknown. Redacted fixtures cannot yield real frequencies.')


def run(args):
    T = R.load(R.local_path(args.dump))
    root = R.person_handle(T, args.root) if args.root else None
    result = report(T, root, category_map(args.categories), args.include_private_deceased)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0
