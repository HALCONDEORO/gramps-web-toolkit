"""Offline component review. Structural distance is not evidence of unrelatedness."""
from __future__ import annotations

from collections import defaultdict
import hashlib
import json
import re

from .tree import BadInput, Tree


def fingerprint(tree: Tree) -> str:
    """Bind review assertions to all object contents, not timestamps or file names."""
    objects = {t: sorted(rows.values(), key=lambda o: o['handle']) for t, rows in tree.objs.items()}
    return hashlib.sha256(json.dumps(objects, sort_keys=True, ensure_ascii=False,
                                    separators=(',', ':')).encode()).hexdigest()


def review(tree: Tree, root: str, holds: dict | None = None) -> dict:
    """Include singletons and unilateral family references; never output a deletion plan.

    A hold manifest is an assertion by a reviewer, not authenticated evidence. Text in the
    tree is not parsed for instructions or authority. Shared evidence and associations are
    reported separately and do not add family edges.
    """
    if tree.partial:
        raise BadInput('component-review needs a full JSON dump; XML loading omits evidence and associations')
    P, F = tree.objs['people'], tree.objs['families']
    by_id = defaultdict(list)
    for h, p in P.items():
        by_id[p.get('gramps_id')].append(h)
    if len(by_id.get(root, [])) != 1:
        raise BadInput('root must identify exactly one person by Gramps ID')
    label = lambda h: tree.gid('people', h)
    # Bipartite person-family edges avoid quadratic work for large families.
    family_side, person_side = set(), set()
    dangling = []
    for fh, f in F.items():
        members = [f.get('father_handle'), f.get('mother_handle')]
        members += [r.get('ref') for r in f.get('child_ref_list', [])]
        for ph in filter(None, members):
            if ph in P:
                family_side.add((ph, fh))
            else:
                dangling.append({'owner_type': 'families', 'owner': tree.gid('families', fh), 'target': ph})
    for ph, p in P.items():
        for fh in set(p.get('family_list', []) + p.get('parent_family_list', [])):
            if fh in F:
                person_side.add((ph, fh))
            else:
                dangling.append({'owner_type': 'people', 'owner': label(ph), 'target': fh})
    graph = defaultdict(set)
    for ph, fh in family_side | person_side:
        a, b = ('people', ph), ('families', fh)
        graph[a].add(b)
        graph[b].add(a)
    components, component_of = [], {}
    for ph in sorted(P):
        if ph in component_of:
            continue
        todo, seen, people = [('people', ph)], set(), []
        while todo:
            node = todo.pop()
            if node in seen:
                continue
            seen.add(node)
            if node[0] == 'people':
                people.append(node[1])
            todo.extend(graph[node] - seen)
        key = min(people)  # stable handles, not a genealogical ranking
        for h in people:
            component_of[h] = key
        components.append((key, sorted(people, key=lambda h: (label(h), h))))
    root_component = component_of[by_id[root][0]]
    digest = fingerprint(tree)
    protected, hold_rows = set(), []
    if holds is not None:
        if not isinstance(holds, dict) or holds.get('format') != 'gwt-component-holds/1':
            raise BadInput('hold manifest format must be gwt-component-holds/1')
        if holds.get('tree_sha256') != digest:
            raise BadInput('hold manifest does not match this tree; reconcile before using it')
        rows = holds.get('holds')
        if not isinstance(rows, list):
            raise BadInput('hold manifest needs a holds list')
        for row in rows:
            if not isinstance(row, dict):
                raise BadInput('each hold needs person and nonempty evidence_refs')
            pid, refs = row.get('person'), row.get('evidence_refs')
            if not isinstance(pid, str) or len(by_id.get(pid, [])) != 1:
                raise BadInput('each hold must identify exactly one person')
            if not isinstance(refs, list) or not refs or any(not isinstance(r, str) or not r.strip() for r in refs):
                raise BadInput('each hold needs nonempty evidence_refs; references are not verified by this report')
            protected.add(component_of[by_id[pid][0]])
            hold_rows.append({'person': pid, 'evidence_refs': sorted(set(refs))})
    # Association references are possible leads, not structural family links.
    associations = []
    for ph, p in P.items():
        for ref in p.get('person_ref_list', []):
            target = ref.get('ref')
            if target in P:
                associations.append({'person': label(ph), 'target': label(target),
                                     'cross_component': component_of[ph] != component_of[target]})
            else:
                dangling.append({'owner_type': 'association', 'owner': label(ph), 'target': target})
    # Follow only person -> event -> citation -> note/media/citation evidence.
    # Shared sources, places and repositories are deliberately excluded: same book/town
    # is not a case-specific bridge. No transitive traversal through shared evidence.
    evidence_owners = defaultdict(set)
    for ph, p in P.items():
        queue, seen = [('people', ph)], set()
        while queue:
            typ, handle = queue.pop()
            if (typ, handle) in seen:
                continue
            seen.add((typ, handle))
            obj = tree.objs[typ].get(handle, {})
            if typ in ('notes', 'media'):
                continue
            for key, dest in (('note_list', 'notes'), ('citation_list', 'citations')):
                for target in obj.get(key, []):
                    if target in tree.objs[dest]:
                        evidence_owners[(dest, target)].add(ph)
                        if dest == 'citations':
                            queue.append((dest, target))
            for ref in obj.get('media_list', []):
                target = ref.get('ref')
                if target in tree.objs['media']:
                    evidence_owners[('media', target)].add(ph)
            if typ == 'people':
                for ref in obj.get('event_ref_list', []):
                    queue.append(('events', ref.get('ref')))
    # Exact existing person IDs in note text are review leads, never instructions.
    ids = [pid for pid in by_id if isinstance(pid, str) and pid]
    pattern = re.compile(r"(?<![\w])(?:" + "|".join(re.escape(pid) for pid in sorted(ids, key=lambda x: (-len(x), x))) + r")(?![\w])") if ids else None
    mentions = []
    for nh, note in sorted(tree.objs['notes'].items()):
        text = note.get('text', '')
        text = text.get('string', '') if isinstance(text, dict) else text
        matches = sorted(set(pattern.findall(text))) if pattern and isinstance(text, str) else []
        if matches:
            mentions.append({'note': tree.gid('notes', nh), 'person_ids': matches})
    shared = []
    for (typ, h), owners in sorted(evidence_owners.items()):
        if len({component_of[o] for o in owners}) > 1:
            shared.append({'type': typ, 'id': tree.gid(typ, h),
                           'people': sorted(label(o) for o in owners)})
    rows = []
    for key, members in components:
        rows.append({'component': key, 'people': [label(h) for h in members],
                     'root_connected': key == root_component,
                     'review_hold': key in protected,
                     'status': 'structurally_connected' if key == root_component else
                               'review_hold' if key in protected else 'unlinked_not_assessed'})
    rows.sort(key=lambda r: (r['people'], r['component']))
    return {'format': 'gwt-component-review/1', 'tree_sha256': digest, 'root': root,
            'advisory_only': True, 'safe_to_delete': [],
            'limits': ['No deletion, merge, identity or kinship verdict.',
                       'Unlinked does not mean unrelated. Structural links can themselves be hypotheses.',
                       'Holds are reviewer assertions; their references are not authenticated or fetched.',
                       'Textual references and private omitted material require separate review.',
                       'Shared evidence is a review lead, not a family edge or proof of shared identity.'],
            'counts': {'people': len(P), 'components': len(rows),
                       'root_connected': sum(len(r['people']) for r in rows if r['root_connected']),
                       'held_outside_root': sum(len(r['people']) for r in rows
                                                if r['review_hold'] and not r['root_connected'])},
            'components': rows, 'holds': sorted(hold_rows, key=lambda r: (r['person'], r['evidence_refs'])),
            'unilateral_family_refs': sorted([
                {'person': label(ph), 'family': tree.gid('families', fh),
                 'present_on': 'family' if (ph, fh) in family_side else 'person'}
                for ph, fh in family_side ^ person_side], key=lambda r: (r['person'], r['family'])),
            'associations': sorted(associations, key=lambda r: (r['person'], r['target'])),
            'shared_evidence': shared, 'note_person_id_mentions': mentions,
            'dangling_refs': sorted(dangling, key=lambda r: (r['owner_type'], r['owner'], str(r['target'])))}


def command(args) -> int:
    from .tree import load
    holds = None
    if args.holds:
        try:
            with open(args.holds, encoding='utf-8') as fh:
                holds = json.load(fh)
        except (OSError, ValueError) as exc:
            raise BadInput(f'cannot read hold manifest: {exc}') from exc
    result = review(load(args.tree), args.root, holds)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0
