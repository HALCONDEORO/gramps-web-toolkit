"""Reproducible offline ancestry and structured evidence reports. No tree writes."""
import collections
import json
import re
from urllib.parse import urlsplit
import gwt_report as R


def existing_citations(T, handles, include_private=False):
    return sorted({T['citations'][c].get('gramps_id', c) for c in handles
                   if c in T['citations'] and T['citations'][c].get('source_handle') in T['sources']
                   and (include_private or (not T['citations'][c].get('private') and
                        not T['sources'][T['citations'][c]['source_handle']].get('private')))})


def person_sources(T, person, include_private=False):
    refs = list(person.get('citation_list', []))
    for ref in person.get('event_ref_list', []):
        event = T['events'].get(ref.get('ref'), {})
        if ref.get('role') == 'Primary' and (include_private or not (ref.get('private') or event.get('private'))):
            refs.extend(event.get('citation_list', []))
    return existing_citations(T, refs, include_private)


def conflicted(T, obj):
    tags = [R.fold(R.text_of(T['tags'].get(t, {}).get('name'))) for t in obj.get('tag_list', [])]
    return any(any(word in name for word in ['conflict', 'contradic']) for name in tags)


def deceased(T, person, include_private=False):
    for ref in person.get('event_ref_list', []):
        event = T['events'].get(ref.get('ref'), {})
        if (ref.get('role') != 'Primary' or R.text_of(event.get('type')) not in
                {'Death', 'Burial', 'Cremation'}):
            continue
        if conflicted(T, event) or conflicted(T, ref):
            continue
        if not include_private and (event.get('private') or ref.get('private')):
            continue
        return True
    return False



def named(person):
    name = R.label(person).strip()
    folded = R.fold(name)
    markers = ('unknown', 'inconnu', 'inconnue', 'desconocido', 'desconocida', '[redacted]', '?')
    return bool((person.get('primary_name') or {}).get('first_name', '').strip()) and not any(
        marker in folded for marker in markers)


def parent_options(T, n, handle, include_private, issues):
    """Collect tree-link candidates, excluding adoption before blockers."""
    options = collections.defaultdict(set)
    blocked_roles = set()
    for parent, role, fh, ref in R.parent_steps(T, handle):
        relation = R.text_of(ref.get('frel' if role == 'father' else 'mrel'))
        if relation == 'Adopted':
            issues.append(dict(slot=2*n+(role=='mother'), reason='adopted-link-excluded'))
            continue
        fam = T['families'][fh]
        options[role].add(parent)
        if not include_private and (fam.get('private') or ref.get('private') or T['people'][parent].get('private')):
            blocked_roles.add(role)
            continue
        if (conflicted(T, fam) or conflicted(T, T['people'][handle]) or
                conflicted(T, T['people'][parent])):
            blocked_roles.add(role)
            issue = dict(slot=2*n+(role=='mother'), reason='conflicting-link-excluded')
            if deceased(T, T['people'][parent], include_private) and (include_private or not T['people'][parent].get('private')):
                issue['candidate'] = parent
            issues.append(issue)
            continue
    return options, blocked_roles


def ancestry(T, root, max_generation=12, include_private=False):
    if root is None:
        raise R.BadInput('a reproducible root is required; no inferred root')
    if not 0 <= max_generation <= 12:
        raise R.BadInput('max-generation must be between 0 and 12')
    slots = {1: root}
    issues = []
    queue = collections.deque([(1, root, frozenset([root]))])
    while queue:
        n, handle, seen = queue.popleft()
        generation = n.bit_length() - 1
        if generation >= max_generation:
            if any(True for _ in R.parent_steps(T, handle)):
                issues.append(dict(slot=n, reason='generation-limit'))
            continue
        options, blocked_roles = parent_options(T, n, handle, include_private, issues)
        for role, parents in options.items():
            child_slot = 2*n + (role == 'mother')
            if role in blocked_roles or len(parents) != 1:
                issues.append(dict(slot=child_slot, reason='ambiguous-parents-excluded'))
                continue
            parent = next(iter(parents))
            if parent in seen:
                issues.append(dict(slot=child_slot, reason='cycle-excluded'))
                continue
            slots[child_slot] = parent
            queue.append((child_slot, parent, seen | {parent}))
    return slots, issues


def eligible(T, person, include_private=False):
    return (deceased(T, person, include_private) and named(person) and not conflicted(T, person) and
            (include_private or not person.get('private')) and bool(person_sources(T, person, include_private)))


def completeness(T, root, max_generation=12, include_private=False):
    slots, issues = ancestry(T, root, max_generation, include_private)
    rows = []
    for generation in range(1, max_generation + 1):
        theoretical = 2**generation
        occupied = [(slot, h) for slot, h in slots.items() if slot.bit_length()-1 == generation
                    and eligible(T, T['people'][h], include_private)]
        unique = {h for _, h in occupied}
        rows.append(dict(generation=generation, theoretical_slots=theoretical,
                         occupied_slots=len(occupied), unique_people=len(unique),
                         repeated_slots=len(occupied)-len(unique),
                         uncounted_slots=theoretical-len(occupied),
                         people=[dict(person=T['people'][h].get('gramps_id', h),
                                      slots=sorted(n for n, x in occupied if x == h),
                                      citations=person_sources(T, T['people'][h], include_private)) for h in sorted(unique)]))
    return dict(schema_version=1, scope='offline sourced named deceased direct ancestors only',
                root=T['people'][root].get('gramps_id', root), max_generation=max_generation,
                rows=rows, excluded_links=issues,
                caveat='Uncounted includes unknown, unsourced, possibly living, private, placeholder and conflict. '
                       'Occupied does not certify biological identity or record readings. Root is not an ancestor row. '
                       'Missing links in filtered fixtures cannot measure the real complete tree.')


def validate_evidence_url(value):
    """Require an observed HTTPS record link, not credentials or control text."""
    try:
        url = urlsplit(value)
        if (url.scheme != 'https' or not url.hostname or url.username or url.password or
                re.search(r'[\s\x00-\x1f\x7f]', value)):
            raise ValueError()
    except ValueError as exc:
        raise R.BadInput('evidence URL must be an observed HTTPS record link') from exc


def validate_evidence_values(row):
    """Validate typed manifest attestations without inferring readings."""
    if (any(not isinstance(row[k], str) or not row[k].strip() for k in
            ['person', 'citation', 'literal', 'literal_url']) or row['kind'] not in ['index', 'act'] or
            type(row['conflict']) is not bool or not isinstance(row['readings'], list) or
            any(not isinstance(x, str) or not x.strip() for x in row['readings']) or
            len(set(row['readings'])) != len(row['readings'])):
        raise R.BadInput('invalid evidence values')


def evidence_records(path):
    if not path:
        return []
    rows = R.read_json(R.local_path(path))
    if not isinstance(rows, list):
        raise R.BadInput('evidence manifest must be a list')
    keys = {'person', 'citation', 'kind', 'literal', 'literal_url', 'readings', 'conflict'}
    for row in rows:
        if not isinstance(row, dict) or set(row) != keys:
            raise R.BadInput('invalid evidence row schema')
        validate_evidence_values(row)
        validate_evidence_url(row['literal_url'])
    return rows


def evidence_stage(conflict, supplied, citations):
    """Classify recorded attestations, never notes or inferred readings."""
    if conflict:
        stage = 'contradiction'
    elif any(r['kind'] == 'act' and len(r['readings']) >= 2 for r in supplied):
        stage = 'two-readings'
    elif any(r['kind'] == 'act' and len(r['readings']) == 1 for r in supplied):
        stage = 'one-reading'
    elif any(r['kind'] == 'index' for r in supplied):
        stage = 'index'
    else:
        stage = 'unclassified-source' if citations else 'no-source'
    return stage


def evidence_positions(slots, issues, handle):
    """Keep chosen and contradictory candidate positions distinct from proof."""
    return sorted({n for n, h in slots.items() if h == handle} |
                  {x['slot'] for x in issues if x.get('candidate') == handle})


def evidence_fan(T, root, records, max_generation=12, include_private=False):
    slots, issues = ancestry(T, root, max_generation, include_private)
    by_person = collections.defaultdict(list)
    for row in records:
        by_person[row['person']].append(row)
    rows = []
    conflict_candidates = {x['candidate'] for x in issues if x.get('candidate')}
    for handle in sorted((set(slots.values()) | conflict_candidates) - {root}):
        person = T['people'][handle]
        if not deceased(T, person, include_private) or not named(person) or (person.get('private') and not include_private):
            continue
        pid = person.get('gramps_id', handle)
        citations = person_sources(T, person, include_private)
        supplied = by_person[pid]
        # The manifest cannot attach another person's citation to this person's evidence row.
        if any(r['citation'] not in citations for r in supplied):
            raise R.BadInput('evidence citation is not attached to the named ancestor')
        conflict = handle in conflict_candidates or conflicted(T, person) or any(r['conflict'] for r in supplied)
        stage = evidence_stage(conflict, supplied, citations)
        positions = evidence_positions(slots, issues, handle)
        rows.append(dict(person=pid, slots=positions,
                         generations=sorted({n.bit_length()-1 for n in positions}),
                         citations=citations, evidence=supplied, stage=stage,
                         has_attached_source=bool(citations), parentage_verified=False))
    return dict(schema_version=1, scope='offline deceased direct ancestors evidence inventory',
                root=T['people'][root].get('gramps_id',root), rows=rows, excluded_links=issues,
                caveat='Manifest entries are recorded attestations, not independent verification of readings. '
                       'A source without a reviewed kind remains unclassified, not silently an act or an index. '
                       'A cited profile is not verified parentage. No write or publication authority. Filtered fixtures cannot reconstruct missing ancestors.')


def run(args):
    T = R.load(R.local_path(args.dump))
    root = R.person_handle(T, args.root)
    if args.cmd == 'pedigree-completeness':
        result = completeness(T, root, args.max_generation, args.include_private_deceased)
    else:
        result = evidence_fan(T, root, evidence_records(args.evidence), args.max_generation,
                              args.include_private_deceased)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0
