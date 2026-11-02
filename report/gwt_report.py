#!/usr/bin/env python3
"""gwt-report: how two people of a tree are related, and what is new in a tree since the last time you told someone.

Reads offline files (a `gwt-check dump` or a `gwt-apply` snapshot, gzipped or not) and never touches the tree.

  gwt-report kin [--lang en|es|fr] [--json] [--weak-tag TEXT] DUMP A B
  gwt-report digest --from OLD --to NEW [--root I0001] [--base-url URL] [--told FILE --audience NAME [--mark]]
                    [--format md|html]

kin lists every blood path between A and B (through parent families where the link is Birth, Adopted, Unknown or
empty; never through a marriage), the nearest common ancestors with their Ahnentafel numbers seen from A and from
B, the relationship name in English, Spanish and French, and how many links of each path are confirmed, probable,
weak-tagged or unmarked. It also lists implex: one ancestor (or couple) reached at two or more Ahnentafel numbers
in A's or B's pedigree.

digest lists the people, events, citations and media in NEW that OLD lacks, grouped by person and, with --root,
by side of the root's family and ordered by kin distance. Each item gets its Gramps Web link (--base-url) and the
web addresses its citations hold. --told FILE (TSV: audience, object id, date) leaves out what that audience was
already told; --mark appends what this digest shows. Exit 2 on a bad file. Standard library only.
"""
import argparse
import collections
import contextlib
import datetime
import functools
import gzip
import html
import io
import json
import os
import pathlib
import re
import sys
import unicodedata

CLASSES = {'Person': 'people', 'Family': 'families', 'Event': 'events', 'Citation': 'citations', 'Media': 'media',
           'Note': 'notes', 'Tag': 'tags', 'Place': 'places', 'Source': 'sources'}
SNAPSHOT_FORMAT = 'gwt-apply-snapshot/1'
ACCEPTED = 'a gwt-check dump or a gwt-apply snapshot'
LINEAGE = ('', 'Birth', 'Unknown', 'Adopted')  # a parent link kin follows; Stepchild, Foster, Sponsored are not blood
WEAK_TAGS = ('hipotesis', 'hypothes', 'weak', 'debil', 'faible')  # a tag whose folded name holds one marks a weak link
PROBABLE, CONFIRMED = '[probable]', '[confirmed]'  # the marks gwt-apply puts on a child_ref note
LINK_CLASSES = ('confirmed', 'probable', 'weak', 'unmarked')
MAX_GEN = 30         # how far up kin and implex look
MAX_PATHS = 2000     # paths kept per person, nearest first: a big pedigree or a deep implex multiplies them
MAX_PEDIGREE = 200000
WEB_KIND = {'people': 'person', 'events': 'event', 'citations': 'citation', 'media': 'media'}
URL_RE = re.compile(r'https?://[^\s<>"\]\)]+')
GZIP_MAGIC = b'\x1f\x8b'
SIDE_ORDER = {'root': 0, "father's side": 1, "mother's side": 2, 'both sides': 3, 'descendants': 4,
              'not blood kin': 5, 'other': 6}


class BadInput(Exception):
    pass


def local_path(raw):
    """The normalized, immutable path of a local file the user named on the command line. The command line is the
    one place paths come in; every open in this module goes through here and never touches the text again."""
    return pathlib.Path(os.path.normpath(os.fspath(raw)))


def read_json(source):
    """The JSON in a local file, gzipped or not, read through one handle so the sniffed bytes are the bytes parsed."""
    with contextlib.ExitStack() as stack:
        raw = stack.enter_context(source.open('rb'))
        gz = raw.read(2) == GZIP_MAGIC
        raw.seek(0)
        stream = stack.enter_context(gzip.GzipFile(fileobj=raw)) if gz else raw
        text = stack.enter_context(io.TextIOWrapper(stream, encoding='utf-8'))
        return json.load(text)


def classes_of(path, data):
    """The {class name: list} part of a dump or snapshot. A file in another shape is an error, never an empty tree."""
    if not isinstance(data, dict):
        raise BadInput(f'{path}: not {ACCEPTED}')
    snapshot = data.get('format') == SNAPSHOT_FORMAT
    objects = data.get('objects', data)
    if not isinstance(objects, dict):
        raise BadInput(f'{path}: not {ACCEPTED}: expected one list per type')
    if snapshot:
        objects = {CLASSES[k]: v for k, v in objects.items() if k in CLASSES}
    for t in ('people', 'families'):
        if not isinstance(objects.get(t), list):
            raise BadInput(f'{path}: no {t} list: is this {ACCEPTED}?')
    if not objects['people']:
        raise BadInput(f'{path}: the file holds no people')
    return objects


def by_handle(path, t, objs):
    if not isinstance(objs, list) or any(not isinstance(o, dict) or 'handle' not in o for o in objs):
        raise BadInput(f'{path}: {t} must be a list of objects with a handle')
    return {o['handle']: o for o in objs}


def ids_index(T):
    """{gramps id: (class, handle)} for the classes a digest links to; the first object with an id wins."""
    index = {}
    for t in WEB_KIND:
        for o in T[t].values():
            if o.get('gramps_id'):
                index.setdefault(o['gramps_id'], (t, o['handle']))
    return index


def load(path):
    """Every class kin and digest read, keyed by handle."""
    try:
        data = read_json(local_path(path))
    except (OSError, ValueError, EOFError) as exc:
        raise BadInput(f'{path}: not JSON, so not {ACCEPTED} ({exc})') from exc
    objects = classes_of(path, data)
    T = {t: by_handle(path, t, objects.get(t) or []) for t in CLASSES.values()}
    T['_taken'] = data.get('taken') or data.get('dumped_at') or ''
    T['_tree'] = data.get('tree') or ''
    T['_by_id'] = ids_index(T)
    return T


def text_of(value):
    return (value.get('string') if isinstance(value, dict) else value) or ''


def fold(s):
    s = unicodedata.normalize('NFKD', s or '').casefold()
    return ''.join(c for c in s if not unicodedata.combining(c))


def label(p):
    n = p.get('primary_name') or {}
    sn = ' '.join(s.get('surname', '') for s in n.get('surname_list', []) if s.get('surname'))
    return ' '.join(x for x in (n.get('first_name', ''), sn) if x) or p.get('gramps_id', '') or '?'


def who(T, h):
    p = T['people'][h]
    return f"{label(p)} ({p.get('gramps_id') or h})"


def gramps_id(T, h):
    return T['people'][h].get('gramps_id') or h


def person_handle(T, ref):
    if ref in T['people']:
        return ref
    t, h = T['_by_id'].get(ref, (None, None))
    if t != 'people':
        raise BadInput(f'no person {ref} in the file')
    return h


# ---- kin ---------------------------------------------------------------------------------------------------------

def parent_steps(T, h):
    """(parent, 'father'|'mother', family, child_ref) for each parent link of h that carries blood or adoption."""
    for fh in T['people'][h].get('parent_family_list') or []:
        f = T['families'].get(fh)
        if not f:
            continue
        ref = next((c for c in f.get('child_ref_list') or [] if c.get('ref') == h), None)
        if ref is None:
            continue
        for key, role, rel in (('father_handle', 'father', 'frel'), ('mother_handle', 'mother', 'mrel')):
            if f.get(key) in T['people'] and text_of(ref.get(rel)) in LINEAGE:
                yield f[key], role, fh, ref


def ancestor_paths(T, h, max_gen=MAX_GEN, max_paths=MAX_PATHS):
    """{ancestor: [path, ...]}, h itself with the empty path. A path is a tuple of steps (child, parent, role, family,
    child_ref) from h upwards. The walk goes one generation at a time, so the cap drops the most distant paths
    first, never a whole side. Returns (paths, truncated)."""
    out = {h: [()]}
    count = 0
    todo = collections.deque([(h, (), frozenset((h,)))])
    while todo:
        cur, path, seen = todo.popleft()
        if len(path) >= max_gen:
            continue
        for parent, role, fh, ref in parent_steps(T, cur):
            if parent in seen:
                continue  # a person recorded as their own ancestor: bad data, never an endless walk
            if count >= max_paths:
                return out, True
            count += 1
            p2 = path + ((cur, parent, role, fh, ref),)
            out.setdefault(parent, []).append(p2)
            todo.append((parent, p2, seen | {parent}))
    return out, False


def ahnentafel(path):
    n = 1
    for step in path:
        n = 2 * n + (step[2] == 'mother')
    return n


def tagged_weak(T, obj, weak_tags):
    return any(any(w in fold(text_of((T['tags'].get(t) or {}).get('name'))) for w in weak_tags)
               for t in obj.get('tag_list') or [])


def link_class(T, step, weak_tags):
    """confirmed, probable, weak or unmarked, for one child -> parent link."""
    child, _, _, fh, ref = step
    if tagged_weak(T, T['families'][fh], weak_tags) or tagged_weak(T, T['people'][child], weak_tags):
        return 'weak'
    marks = [text_of((T['notes'].get(n) or {}).get('text')).lstrip() for n in ref.get('note_list') or []]
    if any(m.startswith(PROBABLE) for m in marks):
        return 'probable'
    if any(m.startswith(CONFIRMED) for m in marks) or any(
            ((T['citations'].get(c) or {}).get('confidence') or 0) >= 3 for c in ref.get('citation_list') or []):
        return 'confirmed'
    return 'unmarked'


def line_groups(pa, pb):
    """{(people from a, people from b, family a, family b): [(ancestor, path_a, path_b), ...]}: one entry per line
    of descent, the members being the ancestors of a couple that line reaches."""
    groups = {}
    for c in set(pa) & set(pb):
        for path_a in pa[c]:
            on_a = {s[0] for s in path_a}
            for path_b in pb[c]:
                # two lines that meet below c (share a person) belong to that nearer ancestor's relationship
                if not on_a.isdisjoint(s[0] for s in path_b):
                    continue
                key = (tuple(s[0] for s in path_a), tuple(s[0] for s in path_b),
                       path_a[-1][3] if path_a else None, path_b[-1][3] if path_b else None)
                groups.setdefault(key, []).append((c, path_a, path_b))
    return groups


def is_half(T, g1, g2, fam_a, fam_b, members):
    return bool(g1 and g2) and (fam_a != fam_b or len(members) == 1 and all(
        T['families'][fam_a].get(k) in T['people'] for k in ('father_handle', 'mother_handle')))


def is_adopted(members):
    return any(text_of(s[4].get('frel' if s[2] == 'father' else 'mrel')) == 'Adopted'
               for m in members for s in m[1] + m[2])


def count_links(T, steps, weak_tags):
    links = dict.fromkeys(LINK_CLASSES, 0)
    for s in steps:
        links[link_class(T, s, weak_tags)] += 1
    return links


def one_relationship(T, a, b, fams, members, weak_tags):
    members.sort(key=lambda m: ahnentafel(m[1]))
    _, path_a, path_b = members[0]
    g1, g2 = len(path_a), len(path_b)
    return {
        'generations': [g1, g2],
        'half': is_half(T, g1, g2, fams[0], fams[1], members),
        'adopted': is_adopted(members),
        'ancestors': [(c, ahnentafel(p1), ahnentafel(p2)) for c, p1, p2 in members],
        'path_a': [a] + [s[1] for s in path_a],
        'path_b': [b] + [s[1] for s in path_b],
        'links': count_links(T, list(path_a) + list(path_b), weak_tags),
        'side': path_a[0][2] if path_a else None,
    }


def relationships(T, a, b, weak_tags=WEAK_TAGS, max_paths=MAX_PATHS, a_paths=None):
    """Every blood relationship between a and b, nearest first. Each is one line of descent: the nearest common
    ancestor (or couple), the path from a and from b, generations up from each, half or not. a_paths is
    ancestor_paths(T, a) when the caller already has it."""
    pa, ta = a_paths or ancestor_paths(T, a, max_paths=max_paths)
    pb, tb = ancestor_paths(T, b, max_paths=max_paths)
    rels = [one_relationship(T, a, b, key[2:], members, weak_tags) for key, members in line_groups(pa, pb).items()]
    rels.sort(key=lambda r: (sum(r['generations']), r['ancestors'][0][1]))
    return rels, ta or tb


def gender(T, h):
    g = T['people'][h].get('gender')
    return g if g in (0, 1) else None


EN_ORD = ('', 'first', 'second', 'third', 'fourth', 'fifth', 'sixth', 'seventh', 'eighth', 'ninth', 'tenth')
ES_ORD = ('', '', 'segund', 'tercer', 'cuart', 'quint', 'sext', 'séptim', 'octav', 'noven', 'décim')
ES_UP = ('', 'padre', 'abuelo', 'bisabuelo', 'tatarabuelo')
ES_DOWN = ('', 'hijo', 'nieto', 'bisnieto', 'tataranieto')


def en_pick(sex, m, f, n):
    return m if sex == 1 else f if sex == 0 else n


def en_greats(g, word):  # g >= 2 generations: grand-, great-grand-, great-great-grand-, 3x great-grand-
    if g == 2:
        return 'grand' + word
    return ('great-' * (g - 2) if g <= 4 else f'{g - 2}x great-') + 'grand' + word


def en_direct(g1, g2, sex):
    """b is an ancestor or a descendant of a: exactly one of g1, g2 is 0."""
    if g1 == 0:
        w = en_pick(sex, 'son', 'daughter', 'child')
        return w if g2 == 1 else en_greats(g2, w)
    w = en_pick(sex, 'father', 'mother', 'parent')
    return w if g1 == 1 else en_greats(g1, w)


def en_cousin(g1, g2, half):
    degree, removed = min(g1, g2) - 1, abs(g1 - g2)
    out = (EN_ORD[degree] if degree < len(EN_ORD) else f'{degree}th') + ' cousin'
    if removed:
        out += ' ' + {1: 'once', 2: 'twice'}.get(removed, f'{removed} times') + ' removed'
    return ('half ' if half else '') + out


def en_collateral(g1, g2, half, sex):
    h = 'half ' if half else ''
    if g1 == 1 and g2 == 1:
        return h.replace(' ', '-') + en_pick(sex, 'brother', 'sister', 'sibling')
    if g1 == 1 or g2 == 1:
        w = en_pick(sex, 'nephew', 'niece', 'nephew or niece') if g1 == 1 \
            else en_pick(sex, 'uncle', 'aunt', 'uncle or aunt')
        g = max(g1, g2)
        pre = '' if g == 2 else 'great-' * (g - 2) if g <= 4 else f'{g - 2}x great-'
        return h + pre + w
    return en_cousin(g1, g2, half)


def name_en(g1, g2, half, sex):
    if g1 == 0 and g2 == 0:
        return 'same person'
    if g1 == 0 or g2 == 0:
        return en_direct(g1, g2, sex)
    return en_collateral(g1, g2, half, sex)


def es_gender(word, fem):  # masculine word ending in -o made feminine
    return word[:-1] + 'a' if fem and word.endswith('o') else word


def es_ordinal(n, fem):
    return ES_ORD[n] + ('a' if fem else 'o') if n < len(ES_ORD) else f'de grado {n}'


def es_gap(d, words, fem):
    return '' if d == 1 else ' ' + es_gender(words[d], fem) if d < len(words) else f' en {d} generaciones'


def es_direct(g1, g2, fem):
    """b is an ancestor or a descendant of a: exactly one of g1, g2 is 0."""
    if g1 == 0:
        return es_gender(ES_DOWN[g2], fem) if g2 < len(ES_DOWN) else f'descendiente en {g2} generaciones'
    if g1 == 1:
        return 'madre' if fem else 'padre'
    if g1 < len(ES_UP):
        return es_gender(ES_UP[g1], fem)
    return ('antepasada' if fem else 'antepasado') + f' en {g1} generaciones'


def es_same_generation(n, fem):
    if n == 1:
        return 'hermana' if fem else 'hermano'
    cousin = 'prima' if fem else 'primo'
    if n == 2:
        return cousin + (' hermana' if fem else ' hermano')
    return cousin + ' ' + es_ordinal(n - 1, fem)


def es_collateral(g1, g2, half, fem):
    half_word = ('media ' if fem else 'medio ') if half else ''
    n, d = min(g1, g2), abs(g1 - g2)
    if d == 0:
        return half_word + es_same_generation(n, fem)
    ordinal = '' if n == 1 else ' ' + es_ordinal(n, fem)
    if g1 < g2:  # b is in a lower generation: sobrino, sobrino nieto, sobrino bisnieto
        base = ('sobrina' if fem else 'sobrino') + es_gap(d, ES_DOWN, fem)
    else:
        base = ('tía' if fem else 'tío') + es_gap(d, ES_UP, fem)
    return half_word + base + ordinal


def name_es(g1, g2, half, sex):
    fem = sex == 0
    if g1 == 0 and g2 == 0:
        return 'la misma persona'
    if g1 == 0 or g2 == 0:
        return es_direct(g1, g2, fem)
    return es_collateral(g1, g2, half, fem)


def fr_pick(sex, m, f, n=None):
    return f if sex == 0 else m if sex == 1 or n is None else n


def fr_direct(g1, g2, sex):
    """b is an ancestor or a descendant of a: exactly one of g1, g2 is 0."""
    g = max(g1, g2)
    if g == 1:
        return fr_pick(sex, 'fils', 'fille', 'enfant') if g1 == 0 else fr_pick(sex, 'père', 'mère', 'parent')
    if g1 == 0:
        w = fr_pick(sex, 'petit-fils', 'petite-fille', 'petit-enfant')
    else:
        w = fr_pick(sex, 'grand-père', 'grand-mère', 'grand-parent')
    if g > 4:
        return (fr_pick(sex, 'descendant', 'descendante') if g1 == 0 else 'ancêtre') + f' à la {g}e génération'
    return 'arrière-' * (g - 2) + w


def fr_uncle(g1, g2, sex):
    g = max(g1, g2)
    if g1 == 1:
        return fr_pick(sex, 'neveu', 'nièce') if g == 2 else 'arrière-' * (g - 3) + fr_pick(sex, 'petit-neveu', 'petite-nièce')
    return fr_pick(sex, 'oncle', 'tante') if g == 2 else 'arrière-' * (g - 3) + fr_pick(sex, 'grand-oncle', 'grand-tante')


def fr_cousin(g1, g2, demi, sex):
    deg, gap = min(g1, g2) - 1, abs(g1 - g2)
    words = {1: fr_pick(sex, 'cousin germain', 'cousine germaine'),
             2: fr_pick(sex, 'cousin issu de germain', 'cousine issue de germain'),
             3: fr_pick(sex, "cousin issu d'issu de germain", "cousine issue d'issue de germain")}
    out = demi + words.get(deg, fr_pick(sex, 'cousin', 'cousine') + f' au {deg}e degré de cousinage')
    if gap:
        gens = {1: 'une génération', 2: 'deux générations'}.get(gap, f'{gap} générations')
        where = 'au-dessous' if g2 > g1 else 'au-dessus'
        out += f', {gens} {where}'
    return out


def name_fr(g1, g2, half, sex):
    if g1 == 0 and g2 == 0:
        return 'la même personne'
    if g1 == 0 or g2 == 0:
        return fr_direct(g1, g2, sex)
    demi = 'demi-' if half else ''
    if g1 == 1 and g2 == 1:
        return demi + fr_pick(sex, 'frère', 'sœur')
    if g1 == 1 or g2 == 1:
        return demi + fr_uncle(g1, g2, sex)
    return fr_cousin(g1, g2, demi, sex)


NAMERS = {'en': name_en, 'es': name_es, 'fr': name_fr}


def names(g1, g2, half, sex):
    return {lang: f(g1, g2, half, sex) for lang, f in NAMERS.items()}


def pedigree(T, h, max_gen=MAX_GEN):
    """{person: [Ahnentafel numbers]} over the first father and first mother of each person, and truncated."""
    nums, todo, n_seen = {}, [(h, 1, 0)], 0
    while todo:
        cur, n, gen = todo.pop()
        nums.setdefault(cur, []).append(n)
        n_seen += 1
        if gen >= max_gen or n_seen >= MAX_PEDIGREE:
            continue
        got = set()
        for parent, role, _, _ in parent_steps(T, cur):
            if role not in got:
                got.add(role)
                todo.append((parent, 2 * n + (role == 'mother'), gen + 1))
    return {p: sorted(ns) for p, ns in nums.items()}, n_seen >= MAX_PEDIGREE


def repeats_below(p, ns, done, at):
    """True when p is reached at one number only, was listed already, or every number has the same child below."""
    return len(ns) < 2 or p in done or len({at.get(n // 2) for n in ns}) < 2


def implex_mate(nums, at, ns):
    """The partner reached at n+1 every time the person is reached at the even number n, when they repeat alike."""
    mates = {at.get(n + 1) for n in ns} if all(n % 2 == 0 for n in ns) else {None}
    only = next(iter(mates), None)
    mate = only if len(mates) == 1 else None
    if mate and nums.get(mate) and len(nums[mate]) == len(ns):
        return mate
    return None


def implex(T, h):
    """The lowest ancestors reached at more than one Ahnentafel number: couples (father at n, mother at n+1 every
    time) and single people. An ancestor whose every number has the same child below is the parent of an implex
    already listed, so it is left out."""
    nums, truncated = pedigree(T, h)
    at = {n: p for p, ns in nums.items() for n in ns}
    out, done = [], set()
    for p, ns in sorted(nums.items(), key=lambda kv: kv[1][0]):
        if repeats_below(p, ns, done, at):
            continue
        mate = implex_mate(nums, at, ns)
        if mate:
            out.append({'people': [p, mate], 'numbers': [[n, n + 1] for n in ns]})
            done |= {p, mate}
        else:
            out.append({'people': [p], 'numbers': [[n] for n in ns]})
            done.add(p)
    return out, truncated


def kin(T, a, b, weak_tags=WEAK_TAGS):
    rels, truncated = relationships(T, a, b, weak_tags)
    sex = gender(T, b)
    for r in rels:
        r['name'] = names(r['generations'][0], r['generations'][1], r['half'], sex)
    return {'a': a, 'b': b, 'relationships': rels, 'truncated': truncated,
            'implex': {'a': implex(T, a)[0], 'b': implex(T, b)[0] if b != a else []}}


def relationship_json(T, r):
    return {
        'name': r['name'], 'generations': r['generations'], 'half': r['half'], 'adopted': r['adopted'],
        'ancestors': [{'id': gramps_id(T, c), 'name': label(T['people'][c]), 'ahnentafel_a': na, 'ahnentafel_b': nb}
                      for c, na, nb in r['ancestors']],
        'path_a': [gramps_id(T, h) for h in r['path_a']], 'path_b': [gramps_id(T, h) for h in r['path_b']],
        'links': r['links'],
    }


def kin_json(T, rep):
    return {
        'a': gramps_id(T, rep['a']), 'b': gramps_id(T, rep['b']), 'truncated': rep['truncated'],
        'relationships': [relationship_json(T, r) for r in rep['relationships']],
        'implex': {k: [{'ids': [gramps_id(T, h) for h in e['people']], 'numbers': e['numbers']} for e in v]
                   for k, v in rep['implex'].items()},
    }


def id_path(T, handles):
    return ' > '.join(gramps_id(T, h) for h in handles)


def print_relationship(T, r, lang):
    adopted = ' (through an adoption)' if r['adopted'] else ''
    print(f"  {r['name'][lang]}{adopted}")
    anc = ' and '.join(who(T, c) for c, _, _ in r['ancestors'])
    from_a = '-'.join(str(x[1]) for x in r['ancestors'])
    from_b = '-'.join(str(x[2]) for x in r['ancestors'])
    print(f'    common ancestors: {anc}; Ahnentafel from A {from_a}, from B {from_b}')
    print(f"    path from A: {id_path(T, r['path_a'])}")
    print(f"    path from B: {id_path(T, r['path_b'])}")
    print('    links: ' + ', '.join(f"{r['links'][k]} {k}" for k in LINK_CLASSES))


def print_implex(T, rep):
    for side in ('A', 'B'):
        for e in rep['implex'][side.lower()]:
            people = ' and '.join(who(T, p) for p in e['people'])
            numbers = ', '.join('-'.join(map(str, ns)) for ns in e['numbers'])
            print(f"  implex in {side}'s pedigree: {people} at {numbers}")


def print_kin(T, rep, lang):
    print(f"{who(T, rep['a'])} -> {who(T, rep['b'])}")
    if not rep['relationships']:
        print('  no blood path')
    for r in rep['relationships']:
        print_relationship(T, r, lang)
    print_implex(T, rep)
    if rep['truncated']:
        print(f'  (path list cut at {MAX_PATHS} paths per person: the most distant relationships are not shown)')


# ---- digest ------------------------------------------------------------------------------------------------------

def refs_in(obj, key):
    """Every handle under any `key` list in obj, at any depth: citation_list holds handles, media_list refs."""
    out = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == key and isinstance(v, list):
                out += [x.get('ref') if isinstance(x, dict) else x for x in v]
            else:
                out += refs_in(v, key)
    elif isinstance(obj, list):
        for v in obj:
            out += refs_in(v, key)
    return [x for x in out if x]


def claim(T, own, h, people):
    own.setdefault(h, set()).update(p for p in people if p in T['people'])


def person_claims(T, own):
    add = functools.partial(claim, T, own)
    for p in T['people'].values():
        for r in p.get('event_ref_list') or []:
            add(r.get('ref'), [p['handle']])
        for k in ('citation_list', 'media_list'):
            for h in refs_in(p, k):
                add(h, [p['handle']])


def family_claims(T, own):
    add = functools.partial(claim, T, own)
    for f in T['families'].values():
        spouses = [f.get('father_handle'), f.get('mother_handle')]
        for r in f.get('event_ref_list') or []:
            add(r.get('ref'), spouses)
        for c in f.get('child_ref_list') or []:
            for h in refs_in(c, 'citation_list'):
                add(h, [c.get('ref')])
        rest = {k: v for k, v in f.items() if k != 'child_ref_list'}
        for k in ('citation_list', 'media_list'):
            for h in refs_in(rest, k):
                add(h, spouses)


def carried_claims(T, own):
    """What an event or a citation carries belongs to its people."""
    for t in ('events', 'citations', 'media'):
        for o in T[t].values():
            for k in ('citation_list', 'media_list'):
                for h in refs_in(o, k):
                    if h != o['handle']:
                        own.setdefault(h, set()).update(own.get(o['handle'], set()))


def holders(T):
    """{handle of an event, citation or media: set of people it belongs to}."""
    own = {}
    person_claims(T, own)
    family_claims(T, own)
    carried_claims(T, own)
    return own


def urls_of(T, citation):
    c = T['citations'].get(citation) or {}
    texts = [c.get('page') or ''] + [text_of(a.get('value')) for a in c.get('attribute_list') or []]
    texts += [text_of((T['notes'].get(n) or {}).get('text')) for n in c.get('note_list') or []]
    out = []
    for t in texts:
        for u in URL_RE.findall(t):
            u = u.rstrip('.,;')
            if u not in out:
                out.append(u)
    return out


def year_of(ev):
    d = (ev or {}).get('date') or {}
    v = d.get('dateval') or []
    return d.get('year') or (v[2] if len(v) > 2 and isinstance(v[2], int) else 0) or None


def describe_person(T, o):
    birth = None
    refs = o.get('event_ref_list') or []
    i = o.get('birth_ref_index', -1)
    if isinstance(i, int) and 0 <= i < len(refs):
        birth = T['events'].get(refs[i].get('ref'))
    y = year_of(birth)
    born = f', born {y}' if y else ''
    return f'new person: {label(o)}{born}'


def describe_event(T, o):
    name = (T['places'].get(o.get('place')) or {}).get('name')
    place = text_of(name.get('value')) if isinstance(name, dict) else ''
    bits = [text_of(o.get('type')) or 'event', str(year_of(o) or ''), place, o.get('description') or '']
    return 'new event: ' + ', '.join(b for b in bits if b)


def describe_citation(T, o):
    src = text_of((T['sources'].get(o.get('source_handle')) or {}).get('title'))
    bits = [src, o.get('page') or '', f"confidence {o.get('confidence', '?')}"]
    return 'new citation: ' + ', '.join(b for b in bits if b)


def describe_media(o):
    return 'new media: ' + (o.get('desc') or o.get('path') or o.get('gramps_id') or '')


DESCRIBERS = {'people': describe_person, 'events': describe_event, 'citations': describe_citation}


def describe(T, t, o):
    if t in DESCRIBERS:
        return DESCRIBERS[t](T, o)
    return describe_media(o)


def is_weak(T, t, o, weak_tags):
    return tagged_weak(T, o, weak_tags) or (t == 'citations' and (o.get('confidence') or 0) <= 1)


def told_cells(path, n, line):
    """The (audience, object id, ...) cells of one line of a told file, or None for a blank, comment or header line."""
    if not line.strip() or line.startswith('#'):
        return None
    cells = [c.strip() for c in line.rstrip('\n').split('\t')]
    if n == 1 and cells[0].lower() == 'audience':
        return None
    if len(cells) < 2 or not cells[0] or not cells[1]:
        raise BadInput(f'{path} line {n}: expected audience<TAB>object id<TAB>date')
    return cells


def read_told(path, audience):
    """The object ids this audience was already told. A missing file is nobody told anything yet."""
    source = local_path(path)
    if not source.exists():
        return set()
    try:
        with source.open(encoding='utf-8-sig') as fh:
            lines = fh.readlines()
    except (OSError, UnicodeDecodeError) as exc:
        raise BadInput(f'{path}: cannot read the told file ({exc})') from exc
    told = set()
    for n, line in enumerate(lines, 1):
        cells = told_cells(path, n, line)
        if cells is None:
            continue
        if cells[0] == audience:
            told.add(cells[1])
    return told


def fresh_objects(old, new, include_private):
    """(class, handle, object) for what NEW holds and OLD lacks; private ones only when asked for."""
    for t in WEB_KIND:
        for h, o in new[t].items():
            if h in old[t] or (o.get('private') and not include_private):
                continue
            yield t, h, o


def make_item(new, t, h, o, gid, weak_tags):
    return {'type': t, 'id': gid, 'text': describe(new, t, o), 'weak': is_weak(new, t, o, weak_tags),
            'urls': urls_of(new, h) if t == 'citations' else
            [u for c in o.get('citation_list') or [] for u in urls_of(new, c)]}


def attach(by_person, new, people, item, include_private):
    for p in people:
        if p and new['people'][p].get('private') and not include_private:
            continue
        by_person.setdefault(p, []).append(item)


def items_by_person(old, new, told, weak_tags, include_private):
    own = holders(new)
    by_person = {}
    for t, h, o in fresh_objects(old, new, include_private):
        gid = o.get('gramps_id') or h
        if gid in told:
            continue
        people = {h} if t == 'people' else own.get(h, set()) or {None}
        attach(by_person, new, people, make_item(new, t, h, o, gid, weak_tags), include_private)
    return by_person


def side_of(rels):
    """Which side of the root a person's kin reach through: the root's father, mother, both, or below the root."""
    roles = {rr['side'] for rr in rels}
    named = next((x for x in roles if x), None)
    if named is None:
        return 'descendants'
    return 'both sides' if {'father', 'mother'} <= roles else f"{named}'s side"


def kin_group(new, root, root_paths, p, weak_tags, g):
    rels, g['truncated'] = relationships(new, root, p, weak_tags, a_paths=root_paths)
    if not rels:
        g['side'] = 'not blood kin'
        return
    r = rels[0]
    side = side_of(rels)
    g.update(side=side, distance=sum(r['generations']), links=r['links'],
             kin=names(r['generations'][0], r['generations'][1], r['half'], gender(new, p)))


def group_of(new, root, root_paths, p, items, weak_tags):
    g = {'person': p, 'items': items, 'side': 'other', 'kin': None, 'distance': None, 'links': None,
         'truncated': False}
    if p and root:
        if p == root:
            g.update(side='root', distance=0, kin=names(0, 0, False, None))
        else:
            kin_group(new, root, root_paths, p, weak_tags, g)
    return g


def group_key(new, g):
    p = g['person']
    return (SIDE_ORDER[g['side']], g['distance'] if g['distance'] is not None else 1 << 30,
            (new['people'][p].get('gramps_id') or p) if p else '~')


def digest(old, new, root=None, told=frozenset(), weak_tags=WEAK_TAGS, include_private=False):
    """Groups of new items: [{side, person, kin, distance, items: [...]}], nearest kin first."""
    by_person = items_by_person(old, new, told, weak_tags, include_private)
    root_paths = ancestor_paths(new, root) if root else None
    groups = [group_of(new, root, root_paths, p, items, weak_tags) for p, items in by_person.items()]
    groups.sort(key=functools.partial(group_key, new))
    return groups


def parse_offset_stamp(stamp):
    return datetime.datetime.strptime(stamp, '%Y-%m-%dT%H:%M:%S%z')


def when(stamp):
    """The time a dump or snapshot was taken, or None when it cannot be read."""
    for parse in (datetime.datetime.fromisoformat, parse_offset_stamp):
        try:
            return parse(stamp)
        except (TypeError, ValueError):
            pass
    return None


def check_pair(old, new, old_path, new_path):
    """A digest of two different trees is noise: refuse it. Warn when OLD was taken after NEW."""
    if old['_tree'] and new['_tree'] and old['_tree'] != new['_tree']:
        raise BadInput(f"{old_path} and {new_path} are snapshots of two different trees ({old['_tree']}, "
                       f"{new['_tree']})")
    a, b = when(old['_taken']), when(new['_taken'])
    if a and b and (a.tzinfo is None) == (b.tzinfo is None) and a > b:
        print(f'gwt-report: warning: --from {old_path} was taken after --to {new_path}; are they swapped?',
              file=sys.stderr)


def web_url(base, t, gid):
    return f"{base.rstrip('/')}/{WEB_KIND[t]}/{gid}" if base else ''


def escape_html(s):
    return html.escape(s, quote=False)


def keep_text(s):
    return s


def link_html(u):
    return f'<a href="{html.escape(u)}">{escape_html(u)}</a>'


Style = collections.namedtuple('Style', 'esc link title side_head person_head note')


def style_for(fmt):
    """How each line is dressed: (open, close) tags around a title, a side, a person and a note. The html style keeps
    to the inline tags chat apps take (b, u, i, a), no page around them."""
    if fmt == 'html':
        title, side_head, person_head = ('<b>', '</b>'), ('\n<b><u>', '</u></b>'), ('\n<b>', '</b>')
        return Style(escape_html, link_html, title, side_head, person_head, ('\n<i>(', ')</i>'))
    return Style(keep_text, keep_text, ('# ', ''), ('\n## ', ''), ('\n### ', ''), ('\n(', ')'))


def wrap(tags, text):
    return f'{tags[0]}{text}{tags[1]}'


def person_head_text(T, g, base, lang):
    """(heading text, Gramps Web address) of a group's person."""
    p = g['person']
    if not p:
        return 'not attached to a person', ''
    head = who(T, p)
    if g['kin']:
        head += ', ' + g['kin'][lang]
    url = web_url(base, 'people', gramps_id(T, p))
    if g['links'] and (g['links']['probable'] or g['links']['weak']):
        c, pr, w, u = (g['links'][k] for k in LINK_CLASSES)
        head += f' (path: {c} confirmed, {pr} probable, {w} weak, {u} unmarked links)'
    return head, url


def item_line(it, base, st):
    text = it['text'] + (' [weak]' if it['weak'] else '')
    urls = [u for u in [web_url(base, it['type'], it['id'])] + it['urls'] if u]
    return '- ' + ' '.join([st.esc(text)] + [st.link(u) for u in urls])


def group_lines(T, g, base, lang, st):
    head, url = person_head_text(T, g, base, lang)
    link = ' ' + st.link(url) if url else ''
    yield wrap(st.person_head, st.esc(head)) + link + '\n'
    for it in g['items']:
        yield item_line(it, base, st)


def render(T, groups, base, lang, fmt, since=''):
    """Markdown, or html: the same lines with only the inline tags chat apps take (b, u, i, a), no page around them,
    so the text pastes into a Telegram HTML message or an email as it is."""
    st = style_for(fmt)
    suffix = f' since {since}' if since else ''
    lines = [wrap(st.title, st.esc('New in the tree' + suffix))]
    if not groups:
        lines.append('\nNothing new.')
    side = None
    for g in groups:
        if g['side'] != side:
            side = g['side']
            lines.append(wrap(st.side_head, st.esc(side)))
        lines.extend(group_lines(T, g, base, lang, st))
    cut = sum(1 for g in groups if g.get('truncated'))
    if cut:
        lines.append(wrap(st.note, st.esc(
            f'kin paths were cut at {MAX_PATHS} per person for {cut} of these people: a more distant '
            'relationship, or the only one, may be missing')))
    return '\n'.join(lines).lstrip('\n') + '\n'


def told_head(fh):
    """What the told file needs before the new rows: its header when empty, a newline when the last one is missing."""
    fh.seek(0, os.SEEK_END)
    if fh.tell() == 0:
        return 'audience\tobject\tdate\n'
    fh.seek(-1, os.SEEK_END)
    head = '' if fh.read(1) == b'\n' else '\n'  # a hand-edited file may lack its last newline
    return head


def mark(path, audience, ids):
    today = datetime.date.today().isoformat()
    target = local_path(path)
    try:
        with target.open('ab+') as fh:  # append in place: the file keeps its inode, mode and owner
            head = told_head(fh)
            rows = ''.join(f'{audience}\t{i}\t{today}\n' for i in ids)
            fh.write((head + rows).encode('utf-8'))
    except OSError as exc:
        raise BadInput(f'{path}: cannot write the told file ({exc})') from exc


def build_parser():
    p = argparse.ArgumentParser(prog='gwt-report', description=__doc__.split('\n')[0])
    sub = p.add_subparsers(dest='cmd', required=True)
    k = sub.add_parser('kin', help='every blood path between two people, named in en, es and fr, and implex')
    k.add_argument('dump')
    k.add_argument('a', help='Gramps id of the first person')
    k.add_argument('b', help='Gramps id of the second person')
    k.add_argument('--lang', choices=sorted(NAMERS), default='en', help='language of the text output (json has all)')
    k.add_argument('--json', action='store_true')
    default_tags = ', '.join(WEAK_TAGS)
    k.add_argument('--weak-tag', action='append', metavar='TEXT',
                   help='a tag whose name holds TEXT (accents and case ignored) marks a weak link; repeat for more '
                        f'(default: {default_tags})')
    d = sub.add_parser('digest', help='what is new in a tree since an older snapshot, by person, with links')
    d.add_argument('--from', dest='old', required=True, metavar='OLD')
    d.add_argument('--to', dest='new', required=True, metavar='NEW')
    d.add_argument('--root', help='Gramps id: order people by kin distance to this person and group by side')
    d.add_argument('--base-url', default='', help='the Gramps Web address, for a link to each person and item')
    d.add_argument('--lang', choices=sorted(NAMERS), default='en')
    d.add_argument('--told', metavar='FILE', help='TSV of audience, object id, date: leave out what was told')
    d.add_argument('--audience', help='with --told: whose told rows count')
    d.add_argument('--mark', action='store_true', help='append every item shown to --told for --audience')
    d.add_argument('--format', choices=('md', 'html'), default='md')
    d.add_argument('--weak-tag', action='append', metavar='TEXT')
    d.add_argument('--include-private', action='store_true', help='also list objects and people marked private')
    return p


def run_kin(args, weak):
    T = load(local_path(args.dump))
    rep = kin(T, person_handle(T, args.a), person_handle(T, args.b), weak)
    if args.json:
        print(json.dumps(kin_json(T, rep), ensure_ascii=False, indent=1))
    else:
        print_kin(T, rep, args.lang)
    return 0


def mark_shown(args, told_path, groups):
    ids = []
    for g in groups:
        ids += [it['id'] for it in g['items'] if it['id'] not in ids]
    mark(told_path, args.audience, ids)
    print(f'gwt-report: marked {len(ids)} items as told to {args.audience}', file=sys.stderr)


def run_digest(parser, args, weak):
    if (args.told or args.mark) and not args.audience:
        parser.error('--told and --mark need --audience')
    if (args.audience or args.mark) and not args.told:
        parser.error('--audience and --mark need --told FILE')
    old, new = load(local_path(args.old)), load(local_path(args.new))
    check_pair(old, new, args.old, args.new)
    root = person_handle(new, args.root) if args.root else None
    told_path = local_path(args.told) if args.told else None
    told = read_told(told_path, args.audience) if told_path else frozenset()
    groups = digest(old, new, root, told, weak, args.include_private)
    sys.stdout.write(render(new, groups, args.base_url, args.lang, args.format, old['_taken']))
    if args.mark:
        mark_shown(args, told_path, groups)
    return 0


def main(argv=None):
    p = build_parser()
    args = p.parse_args(argv)
    weak = tuple(fold(w) for w in args.weak_tag) if args.weak_tag else WEAK_TAGS
    try:
        if args.cmd == 'kin':
            return run_kin(args, weak)
        return run_digest(p, args, weak)
    except BadInput as exc:
        print(f'gwt-report: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
