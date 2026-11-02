#!/usr/bin/env python3
"""gwt-leads: turn the gaps of a tree dump into research tasks for gwt-queue.

Reads an offline dump (the JSON `gwt-check dump` writes, or a `gwt-apply` snapshot; gzipped or not) and never
touches the tree. Exit 2 on a file that is not a dump, never a silent empty list. Prints the gaps highest priority first: direct ancestors of --root count
100 minus a point per generation (never below 40), everyone else 10.

  gwt-leads [--root I0001] [--kinds branch-end,missing-vital,uncited] [--links FILE] [--archives FILE]
            [--limit N] [--format tsv|json|queue] DUMP

Gaps:
  branch-end     a person with no birth parents recorded: where a line stops
  missing-vital  no birth (or baptism), no marriage (a family with children but no marriage event) or, for people
                 born in --living-after or earlier, no death (or burial); or such an event with no date or no place
  uncited        a birth or death event (or its baptism or burial stand-in) with no citation
  unsupported-link  (only with --links, the report `gwt-check links --json` wrote from the same dump) a birth
                 link whose cited records name neither parent, or only one of two: words only, never a wrong link

Only the person's own events count: refs with role Primary, the birth and death the person points at first.
People who may be living (no death, burial or cremation, and no date of theirs or of a descendant that proves a
birth in --living-after or earlier) and people or events marked private are held back from every format; their Gramps ids go to stderr.

`--format queue` prints one JSON line per task for `gwt-queue add --jsonl - --dedupe`. `--archives FILE` (a TSV
of place -> archive rows) adds where each gap's record is kept and whether that year is online; it never fetches
anything. Standard library only.
"""
import argparse, collections, csv, datetime, gzip, json, os, re, string, sys, unicodedata, urllib.parse

KINDS = ('branch-end', 'missing-vital', 'uncited', 'unsupported-link')
DEFAULT_KINDS = ('branch-end', 'missing-vital', 'uncited')  # unsupported-link needs --links, so it is asked for
LINK_GAPS = {'cited, names neither': 'a cited record names neither parent',
             'a record names one': 'records name only one of the two parents'}
# the other levels gwt-check links writes; a cited link has its own source on the child_ref; 'no record names a
# parent' means no cited birth or baptism at all, which uncited and missing-vital already list
LINK_NOT_GAPS = ('the link itself is cited', 'birth record names both', 'other record names both', 'no record names a parent')
BIRTH_LIKE = ('Birth', 'Baptism', 'Christening')   # in order of preference
DEATH_LIKE = ('Death', 'Burial', 'Cremation')
MARRIAGE_LIKE = ('Marriage',)
FACT_KEYS = {'Birth': 'b', 'Baptism': 'bap', 'Christening': 'bap', 'Death': 'd', 'Burial': 'bur', 'Cremation': 'bur'}
# Gramps date modifiers: 0 none, 1 before, 2 after, 3 about, 4 range, 5 span, 6 text only, 7 from, 8 to
MODIFIER_MARK = {1: '<', 2: '>', 3: '~', 4: '~', 5: '~', 7: '>', 8: '<'}
ABOUT_SLACK = 10  # an "about" or estimated year may be this many years early, for the living test
ARCHIVE_COLUMNS = ('match', 'archive', 'series', 'years', 'url')  # --archives: every one must be in the header
ARCHIVE_OPTIONAL = ('access',)
ACCESS = ('online', 'browser', 'letter', 'unknown')  # an empty access cell reads as unknown
URL_FIELDS = ('place', 'year')


class BadInput(Exception):
    pass


def default_living_after():
    return datetime.date.today().year - 100


SNAPSHOT_FORMAT = 'gwt-apply-snapshot/1'
# a gwt-apply snapshot keys objects by Gramps class; a gwt-check dump by REST namespace
SNAPSHOT_KEYS = {'Person': 'people', 'Family': 'families', 'Event': 'events', 'Place': 'places'}
ACCEPTED = 'a gwt-check dump or a gwt-apply snapshot'


def checked_path(path):
    """A file named on the command line, made absolute with every symlink and `..` resolved, so the file that is
    opened is the one that is reported. Resolving is not a security check and confines nothing: any local path,
    inside the repository or not, is intended. What is refused, as BadInput, is a path with a NUL byte and an
    existing path that is not a regular file (a directory, a device, a pipe). A missing file is left to open()."""
    raw = os.fspath(path)
    if '\0' in os.fsdecode(raw):
        raise BadInput(f'{os.fsdecode(raw)!r}: the path contains a NUL byte')
    real = os.path.realpath(raw)
    if os.path.exists(real) and not os.path.isfile(real):
        raise BadInput(f'{os.fsdecode(raw)}: not a regular file')
    return real


def os_text(exc, real, path):
    """An error's text with the resolved path shown as the user typed it."""
    return str(exc).replace(real, path)


def read_json_file(path):
    """The parsed JSON of a file, gzipped or not (BadInput when it cannot be read as JSON)."""
    real = checked_path(path)
    try:
        with open(real, 'rb') as f:
            gz = f.read(2) == b'\x1f\x8b'
        with (gzip.open if gz else open)(real, 'rt', encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError, EOFError) as exc:
        raise BadInput(f'{path}: not JSON, so not {ACCEPTED} ({os_text(exc, real, path)})')


def snapshot_objects(path, data):
    """The type -> list map of a parsed dump or snapshot (BadInput when it is neither)."""
    snapshot = isinstance(data, dict) and data.get('format') == SNAPSHOT_FORMAT
    if isinstance(data, dict):
        data = data.get('objects', data)
    if not isinstance(data, dict):
        raise BadInput(f'{path}: not {ACCEPTED}: expected a JSON object with one list per type')
    if snapshot:
        data = {SNAPSHOT_KEYS[k]: v for k, v in data.items() if k in SNAPSHOT_KEYS}
    bad = [t for t in ('people', 'families', 'events') if not isinstance(data.get(t), list)]
    if bad:
        raise BadInput(f"{path}: no {', '.join(bad)} list: is this {ACCEPTED}?")
    if not data['people']:
        raise BadInput(f'{path}: the dump holds no people')
    return data


def load(path):
    """The people, families, events and places of a `gwt-check dump` file ({tree, dumped_at, objects: {people:
    [...]}}) or of a `gwt-apply` snapshot ({format: gwt-apply-snapshot/1, objects: {Person: [...]}}), gzipped or
    not. A bare {people: [...]} map is read too. Anything else is an error, never an empty result."""
    data = snapshot_objects(path, read_json_file(path))
    out = {}
    for t in ('people', 'families', 'events'):
        bad = [o for o in data[t] if not isinstance(o, dict) or 'handle' not in o]
        if bad:
            raise BadInput(f'{path}: {len(bad)} {t} records are not objects with a handle: is this {ACCEPTED}?')
        out[t] = {o['handle']: o for o in data[t]}
    places = data.get('places', [])
    if not isinstance(places, list) or any(not isinstance(o, dict) or 'handle' not in o for o in places):
        raise BadInput(f'{path}: places must be a list of objects with a handle')
    out['places'] = {o['handle']: o for o in places}
    return out


def text_of(value):
    """A Gramps enum as the dump has it: a plain string, or {"string": ...}."""
    return (value.get('string') if isinstance(value, dict) else value) or ''


def etype(ev):
    return text_of(ev.get('type'))


def year(ev):
    """The structured year of an event's date, or None (no date, or text only)."""
    d = ev.get('date') or {}
    if d.get('modifier') == 6:
        return None
    v = d.get('dateval') or []
    y = d.get('year') if isinstance(d.get('year'), int) and d.get('year') else None
    return y or (v[2] if len(v) > 2 and isinstance(v[2], int) and v[2] else None)


def latest_year(ev):
    """The latest year the event can be in, for the living test: the end of a range or span, the year plus
    ABOUT_SLACK for an "about" or estimated date, None for "after" or "from" (no upper bound) or no year."""
    y = year(ev) if ev else None
    if not y:
        return None
    d = ev.get('date') or {}
    m = d.get('modifier') or 0
    if m in (2, 7):
        return None
    if m in (4, 5):
        v = d.get('dateval') or []
        return v[6] if len(v) > 6 and isinstance(v[6], int) and v[6] >= y else y
    if m == 3 or (d.get('quality') or 0):
        return y + ABOUT_SLACK
    return y


def year_text(ev):
    """The year as a search filter: '1860' only for a plain, regular date; '~1860', '<1860' or '>1860' when the
    date is about, a range, before, after or estimated. '' when there is no year."""
    y = year(ev)
    if not y:
        return ''
    d = ev.get('date') or {}
    mark = MODIFIER_MARK.get(d.get('modifier') or 0, '')
    if not mark and (d.get('quality') or 0):
        mark = '~'
    return f'{mark}{y:d}'


def date_gap(ev):
    """'' if the event has a structured year; else why it has none: no date at all, or text we cannot read as a year."""
    if year(ev):
        return ''
    text = ((ev.get('date') or {}).get('text') or '').strip()
    return f'has date text without an interpretable year ({text})' if text else 'has no date'


def place_chain(T, handle):
    """Every level of a place, the place itself first: ['Parish', 'Town', 'Province', 'Country']."""
    names = []
    seen = set()
    while handle and handle not in seen:
        seen.add(handle)
        pl = T.get('places', {}).get(handle) or {}
        n = pl.get('name')
        name = ((n.get('value') if isinstance(n, dict) else n) or pl.get('title') or '').strip()
        if name:
            names.append(name)
        refs = pl.get('placeref_list') or []
        handle = refs[0].get('ref') if refs and isinstance(refs[0], dict) else None
    return names


def place_name(T, handle, levels=2):
    """The place and the one it sits in, 'Parish, Town', from the dump's place hierarchy."""
    return ', '.join(place_chain(T, handle)[:levels])


def fold(text):
    """Names compare folded the way gwt-ledger's normalize folds them: case, accents and punctuation do not count,
    «Saint-Éxemple» == «saint exemple». A copy of that rule until the shared core lands."""
    text = unicodedata.normalize('NFKD', (text or '').casefold())
    text = ''.join(c for c in text if not unicodedata.combining(c))
    return ' '.join(re.findall(r'[^\W_]+', text))


def parse_years(text):
    """'1600-1792,1850' -> [(1600, 1792), (1850, 1850)]; '' -> []. Anything else is BadInput."""
    out = []
    for part in (x.strip() for x in text.split(',')) if text.strip() else ():
        m = re.fullmatch(r'(\d{1,4})(?:\s*-\s*(\d{1,4}))?', part)
        if not m or int(m.group(2) or m.group(1)) < int(m.group(1)):
            raise BadInput(f'years {text!r}: write ranges like 1793-1902, comma-separated')
        out.append((int(m.group(1)), int(m.group(2) or m.group(1))))
    return out


def load_archives(path):
    """An --archives TSV as {folded match: row}, the first row of a name kept: a header with match, archive, series,
    years, url (access optional), then one row per place name. Read as plain tab-separated text, like the ledger and
    the queue: a quote is a character, never a field delimiter. A missing, unknown or repeated column, a bad years
    cell, an access value outside ACCESS or a url placeholder other than {place} and {year} is BadInput, never a row
    skipped in silence."""
    header, rows = read_archive_rows(path)
    check_archive_header(path, header)
    index = {}
    for n, row in enumerate(rows, 2):
        where = f'{path} line {n}'
        row = clean_archive_row(row, where)
        check_archive_url(row['url'], where)
        years = archive_years(row['years'], where)
        index.setdefault(fold(row['match']), {'archive': row['archive'], 'series': row['series'], 'years': years,
                                              'url': row['url'], 'access': row['access']})  # the first row of a name wins
    return index


def read_archive_rows(path):
    """(header, rows) of an --archives file: stripped column names and one dict per line (BadInput when unreadable)."""
    real = checked_path(path)
    try:
        with open(real, encoding='utf-8-sig', newline='') as fh:
            reader = csv.DictReader(fh, delimiter='\t', quoting=csv.QUOTE_NONE)
            header = [h.strip() for h in reader.fieldnames or []]
            reader.fieldnames = header
            rows = list(reader)
    except UnicodeDecodeError:
        raise BadInput(f'{path}: the file is not UTF-8; save it as UTF-8 text, tab-separated')
    except (OSError, csv.Error) as exc:
        raise BadInput(f'{path}: {os_text(exc, real, path)}')
    return header, rows


def check_archive_header(path, header):
    """BadInput for a missing, unknown or repeated column of an --archives header."""
    missing = [c for c in ARCHIVE_COLUMNS if c not in header]
    unknown = [c or '(empty)' for c in header if c not in ARCHIVE_COLUMNS + ARCHIVE_OPTIONAL]
    if missing or unknown:
        problems = '; '.join(x for x in (f"missing column {', '.join(missing)}" if missing else '',
                                         f"unknown column {', '.join(unknown)}" if unknown else '') if x)
        raise BadInput(f"{path}: {problems}; the columns are {', '.join(ARCHIVE_COLUMNS)}, "
                       f"plus {', '.join(ARCHIVE_OPTIONAL)} if you want it")
    repeated = sorted({c for c in header if header.count(c) > 1})
    if repeated:
        raise BadInput(f"{path}: column {', '.join(repeated)} appears more than once")


def clean_archive_row(row, where):
    """An --archives row with stripped cells and its access resolved (empty reads as unknown); BadInput for a row
    with extra cells, an empty match or archive, or an access outside ACCESS."""
    if None in row:
        raise BadInput(f'{where}: the row has more cells than the header')
    row = {k: (v or '').strip() for k, v in row.items()}  # trailing empty cells may be left out
    if not fold(row['match']) or not row['archive']:
        raise BadInput(f'{where}: match and archive must not be empty')
    access = row.get('access') or 'unknown'
    if access not in ACCESS:
        raise BadInput(f"{where}: access {access!r} is not one of {', '.join(ACCESS)}")
    row['access'] = access
    return row


def check_archive_url(url, where):
    """BadInput for a url template with a placeholder other than {place} and {year}, or one that does not parse."""
    try:
        fields = [(f, spec, conv) for _, f, spec, conv in string.Formatter().parse(url) if f is not None]
    except ValueError as exc:
        raise BadInput(f'{where}: url: {exc}')
    bad = [f for f, spec, conv in fields if f not in URL_FIELDS or spec or conv]
    if bad:
        raise BadInput(f'{where}: url placeholder {{{bad[0]}}} unknown; only {{place}} and {{year}}')


def archive_years(text, where):
    """parse_years with the row's position in the message."""
    try:
        return parse_years(text)
    except BadInput as exc:
        raise BadInput(f'{where}: {exc}')


def exact_year(ev):
    """The year to test against online ranges: a plain or "about" (or estimated) year; None for before, after,
    a range or span, text only, or no year."""
    d = ev.get('date') or {}
    return year(ev) if (d.get('modifier') or 0) in (0, 3) else None


def where_of(T, ev, archives):
    """The archive row for an event's place (archives as load_archives returns it): the row whose match is the
    deepest level of the place hierarchy, the first such row in the file on a tie. None when no row matches, the event has no place or is private."""
    if not ev or ev.get('private') is True:
        return None
    chain = place_chain(T, ev.get('place'))
    folded = [fold(n) for n in chain]
    row = next((archives[n] for n in folded if n in archives), None)  # one lookup per level, the deepest first
    if row is None:
        return None
    y = exact_year(ev)
    url = row['url'].format(place=urllib.parse.quote(chain[0], safe=''), year=y or '')  # fields checked on load
    return {'archive': row['archive'], 'series': row['series'], 'url': url,
            'online': None if y is None else any(a <= y <= b for a, b in row['years']), 'access': row['access']}


def label(p):
    n = p.get('primary_name') or {}
    sn = ' '.join(s.get('surname', '') for s in n.get('surname_list', []) if s.get('surname'))
    return ' '.join(x for x in (n.get('first_name', ''), sn) if x) or p.get('gramps_id', '')


def weak_name(p):
    """A name too thin to search an outside site with: no given name, no surname, or none at all."""
    n = p.get('primary_name') or {}
    given = (n.get('first_name') or '').strip()
    surnames = [s.get('surname', '').strip() for s in n.get('surname_list', []) if s.get('surname', '').strip()]
    return not given or not surnames


def is_birth_relation(value):
    return text_of(value) in ('', 'Birth', 'Unknown')


def parents(T, p):
    """Birth parents only: an adoptive, step or foster family does not continue the blood line."""
    out = []
    for fh in p.get('parent_family_list', []):
        f = T['families'].get(fh) or {}
        ref = next((c for c in f.get('child_ref_list', []) if c.get('ref') == p['handle']), {})
        for h, rel in ((f.get('father_handle'), ref.get('frel')), (f.get('mother_handle'), ref.get('mrel'))):
            if h in T['people'] and is_birth_relation(rel):
                out.append(h)
    return out


def generations(T, root):
    """handle -> generation (0 = root) for the root and every ancestor, breadth first."""
    gen, queue = {root: 0}, collections.deque([root])
    while queue:
        h = queue.popleft()
        for par in parents(T, T['people'][h]):
            if par not in gen:
                gen[par] = gen[h] + 1
                queue.append(par)
    return gen


def own_events(T, p):
    """The person's own events, in ref order: refs with role Primary (a missing role reads as Primary).
    A godparent's or witness's ref to someone else's baptism is not their own."""
    return [T['events'][r['ref']] for r in p.get('event_ref_list', [])
            if r.get('ref') in T['events'] and text_of(r.get('role')) in ('', 'Primary')]


def vital(T, p, types, index_field):
    """The event that stands for a person's birth (or death): the one the person points at with
    birth_ref_index (or death_ref_index) when that ref is Primary, else the first own event of the first type in
    `types` that the person has."""
    refs = p.get('event_ref_list', [])
    i = p.get(index_field)
    if isinstance(i, int) and 0 <= i < len(refs):
        r = refs[i]
        e = T['events'].get(r.get('ref'))
        if e and text_of(r.get('role')) in ('', 'Primary') and etype(e) in types:
            return e
    evs = own_events(T, p)
    for t in types:
        for e in evs:
            if etype(e) == t:
                return e
    return None


def birth(T, p):
    return vital(T, p, BIRTH_LIKE, 'birth_ref_index')


def death(T, p):
    return vital(T, p, DEATH_LIKE, 'death_ref_index')


def birth_dated(T, p):
    """The birth event when it has a year, else the first own Baptism or Christening that has one."""
    b = birth(T, p)
    if b and year(b):
        return b
    return next((e for e in own_events(T, p) if etype(e) in BIRTH_LIKE[1:] and year(e)), b)


def not_living(T, living_after):
    """Handles of the people the dates prove were born in living_after or earlier: their own birth or baptism,
    their death, or the marriage of a family they head ends by then, or so does that of anyone descending from
    them. A descendant born in 1900 means every ancestor was born before 1900, so the direct line above any
    dated person is never held back."""
    seeds = dated_person_seeds(T, living_after)
    for f in T['families'].values():
        if marriage_by_cutoff(T, f, living_after):
            seeds.update(h for h in (f.get('father_handle'), f.get('mother_handle')) if h in T['people'])
    return with_ancestors(T, seeds)


def by_cutoff(e, living_after):
    """True when the event's latest possible year (see latest_year) is living_after or earlier."""
    y = latest_year(e)
    return y is not None and y <= living_after


def dated_person_seeds(T, living_after):
    """Handles of the people whose own birth or baptism, birth or death event ends by living_after."""
    return {h for h, p in T['people'].items()
            if any(by_cutoff(e, living_after) for e in own_events(T, p) if etype(e) in BIRTH_LIKE)
            or by_cutoff(birth(T, p), living_after) or by_cutoff(death(T, p), living_after)}


def marriage_by_cutoff(T, f, living_after):
    """True when a Marriage event of the family f ends by living_after."""
    return any(by_cutoff(T['events'].get(r.get('ref')), living_after)
               and etype(T['events'][r['ref']]) in MARRIAGE_LIKE
               for r in f.get('event_ref_list', []) if r.get('ref') in T['events'])


def with_ancestors(T, seeds):
    """The seeds and every person above them through any parent family, breadth first."""
    old, queue = set(seeds), collections.deque(seeds)
    while queue:
        p = T['people'][queue.popleft()]
        for fh in p.get('parent_family_list', []):
            f = T['families'].get(fh) or {}
            for h in (f.get('father_handle'), f.get('mother_handle')):
                if h in T['people'] and h not in old:
                    old.add(h)
                    queue.append(h)
    return old


def held_back(T, p, living_after, old=None):
    """Why a person stays out of every output, or '': marked private, or may be living: no death, burial or
    cremation, and nothing in `old` (see not_living) proves a birth in living_after or earlier."""
    if p.get('private') is True:
        return 'private'
    if death(T, p):
        return ''
    if old is None:
        old = not_living(T, living_after)
    return '' if p['handle'] in old else 'living'


def facts(T, p):
    """What the tree knows about a person's birth and death, as search filters: {'by': '~1860', 'bp': 'Parish,
    Town', 'dy': ...}. A baptism or burial stand-in is keyed 'bap' or 'bur'. Private events say nothing."""
    out = {}
    for e in (birth_dated(T, p), death(T, p)):
        if not e or e.get('private') is True:
            continue
        k = FACT_KEYS[etype(e)]
        if year_text(e):
            out[k + 'y'] = year_text(e)
        place = place_name(T, e.get('place'))
        if place:
            out[k + 'p'] = place
    return out


def gap_event(T, p, gap):
    """The event whose record a gap asks for: the birth (or the baptism that dates it) for a branch end, a birth
    gap or an unsupported link, the death (or burial) for a death gap, None for a marriage gap."""
    if gap == 'branch-end' or gap.startswith(('birth-', 'link-')):
        return birth_dated(T, p)
    if gap.startswith('death-'):
        return death(T, p)
    return None


def leads(T, root_gid=None, kinds=DEFAULT_KINDS, living_after=None, hold=True, links=None, archives=None):
    """(rows, held): one row per gap, highest priority first, and {gramps_id: why} for the people held back
    (none when hold is False). With archives (load_archives), a gap whose place matches a row gets 'where'."""
    living_after = default_living_after() if living_after is None else living_after
    gen = root_generations(T, root_gid)
    collector = GapCollector(T, gen, archives, living_after, hold, not_living(T, living_after))
    for p in T['people'].values():
        why = collector.hold_reason(p)
        if why:
            collector.held[p.get('gramps_id', p['handle'])] = why
            continue
        collector.person_gaps(p, kinds)
    if 'missing-vital' in kinds:
        collector.marriage_gaps()
    if 'unsupported-link' in kinds:
        collector.link_gaps(links)
    out = collector.rows
    out.sort(key=lambda r: (-r['priority'], id_order(r['person']), r['kind'], r['gap']))
    return out, collector.held


def root_generations(T, root_gid):
    """handle -> generation for the root person with Gramps id root_gid and its ancestors; {} without a root."""
    if not root_gid:
        return {}
    root = next((h for h, p in T['people'].items() if p.get('gramps_id') == root_gid), None)
    if root is None:
        raise BadInput(f'no person {root_gid} in the dump')
    return generations(T, root)


def check_link_row(row):
    """BadInput for a row of a links report that is malformed or from a level this version does not know."""
    if not (isinstance(row, dict) and all(isinstance(row.get(k), str) for k in ('family', 'child', 'level'))):
        raise BadInput('not a gwt-check links report: every row needs a family, a child and a level')
    if row['level'] not in LINK_GAPS and row['level'] not in LINK_NOT_GAPS:
        raise BadInput(f"links report: unknown level {row['level']!r} (is it from a newer gwt-check?)")


def link_gap_text(T, row, f, ref):
    """The gap a report row names for a family f and child ref, or None when the row is no gap here."""
    gap = LINK_GAPS.get(row['level'])
    if gap is None or not (is_birth_relation(ref.get('frel')) and is_birth_relation(ref.get('mrel'))):
        return None  # supported, listed by another kind, or not a birth link a birth record could prove
    if row['level'] == 'a record names one' and \
            sum(f.get(k) in T['people'] for k in ('father_handle', 'mother_handle')) < 2:
        return None  # the one parent the family has is named
    return gap


def link_what(row, gap):
    """The task text of an unsupported link, with the report row's flags to read first."""
    flags = [x for x in row.get('flags') or [] if isinstance(x, str)]
    read_first = f" [read first: {'; '.join(flags)}]" if flags else ''
    return (f"family {row['family']}: {gap}; transcribe the parents from the cited record into its citation, or "
            f'find a record that names both (words only: the link is not shown wrong){read_first}')


class GapCollector:
    """The rows of leads(): what the tree, the living cutoff and the archives say, and who is held back."""

    def __init__(self, T, gen, archives, living_after, hold, old):
        self.T, self.gen, self.archives = T, gen, archives
        self.living_after, self.hold, self.old = living_after, hold, old
        self.rows, self.held = [], {}

    def add(self, p, kind, gap, what):
        g = self.gen.get(p['handle'])
        prio = max(40, 100 - g) if g is not None else 10
        self.rows.append({'person': p.get('gramps_id', p['handle']), 'label': label(p), 'kind': kind, 'gap': gap,
                          'what': what, 'priority': prio, 'generation': g, 'weak': weak_name(p),
                          'facts': facts(self.T, p)})
        where = where_of(self.T, gap_event(self.T, p, gap), self.archives) if self.archives else None
        if where:
            self.rows[-1]['where'] = where

    def hold_reason(self, p):
        """Why p is held back ('' when it is not, or when holding back is off)."""
        return held_back(self.T, p, self.living_after, self.old) if self.hold else ''

    def person_gaps(self, p, kinds):
        """Every gap of the kinds asked for in one person's own record."""
        T = self.T
        b, d, by = birth(T, p), death(T, p), birth_dated(T, p)
        if 'branch-end' in kinds and not parents(T, p):
            self.add(p, 'branch-end', 'branch-end', 'no parents recorded')
        if 'missing-vital' in kinds:
            self.vital_gaps(p, b, d, by)
        if 'uncited' in kinds:
            for code, e in (('birth', b), ('death', d)):
                if e and not e.get('citation_list'):
                    self.add(p, 'uncited', code + '-uncited', f'{etype(e).lower()} has no citation')

    def vital_gaps(self, p, b, d, by):
        """Missing birth, death, date and place gaps of p (birth b, death d, dated birth or baptism by)."""
        if not b:
            self.add(p, 'missing-vital', 'birth-missing', 'no birth or baptism event')
        if not d and p['handle'] in self.old:
            shown = by and year(by) and by.get('private') is not True
            self.add(p, 'missing-vital', 'death-missing', 'no death or burial event' +
                     (f' ({etype(by).lower()} {year(by):d})' if shown else ''))
        for code, e in (('birth', b), ('death', d)):
            if e:
                self.event_gaps(p, code, e)

    def event_gaps(self, p, code, e):
        """The no-date and no-place gaps of one birth or death event."""
        if date_gap(e):
            gap = date_gap(e)
            if e.get('private') is True and gap != 'has no date':
                gap = 'has date text without an interpretable year'  # the text of a private date stays out
            self.add(p, 'missing-vital', code + '-date', f'{etype(e).lower()} {gap}')
        if not e.get('place'):
            self.add(p, 'missing-vital', code + '-place', f'{etype(e).lower()} has no place')

    def marriage_gaps(self):
        """One gap per family with children and no marriage event, on its first spouse not held back."""
        T = self.T
        for f in T['families'].values():
            if f.get('child_ref_list') and not any(
                    etype(T['events'].get(r.get('ref'), {})) in MARRIAGE_LIKE for r in f.get('event_ref_list', [])):
                self.marriage_gap(f)

    def marriage_gap(self, f):
        for h in (f.get('father_handle'), f.get('mother_handle')):
            p = self.T['people'].get(h)
            if p and not self.hold_reason(p):
                gid = f.get('gramps_id', f['handle'])
                self.add(p, 'missing-vital', f'marriage-missing-{gid}',
                         f"family {f.get('gramps_id', '')} has children but no marriage event")
                break

    def link_gaps(self, links):
        """One gap per row of a gwt-check links report that no record supports (see link_gap_text)."""
        T = self.T
        by_gid = {p.get('gramps_id'): p for p in T['people'].values()}
        fam_by_gid = {f.get('gramps_id'): f for f in T['families'].values()}
        stale = 0
        for row in (links or {}).get('rows', []):
            check_link_row(row)
            p, f = by_gid.get(row['child']), fam_by_gid.get(row['family'])
            ref = next((c for c in (f or {}).get('child_ref_list', []) if p and c.get('ref') == p['handle']), None)
            if ref is None:
                stale += 1
                continue
            gap = link_gap_text(T, row, f, ref)
            if gap is None:
                continue
            why = self.hold_reason(p)
            if why:
                self.held[p.get('gramps_id', p['handle'])] = why
                continue
            self.add(p, 'unsupported-link', f"link-{row['family']}", link_what(row, gap))
        if stale:
            raise BadInput(f'{stale} rows of the links report name a child who is not in that family in this dump: the '
                           'report is from another tree or an older dump, so run gwt-check links on this one')


def id_order(gid):
    """I2 before I10: the letters, then the number, then the ID itself (I01 before I1, whatever the input order)."""
    m = re.fullmatch(r'([A-Za-z]*)(\d+)', gid or '')
    return (m.group(1), int(m.group(2)), gid) if m else (gid, -1, gid)


def task_of(row):
    return 'verify' if row['kind'] in ('uncited', 'unsupported-link') else 'search'


def query_of(row):
    """What a lane types into a search: the name, the birth year and the birth place when the tree knows them."""
    f = row.get('facts') or {}
    y = f.get('by') or f.get('bapy') or ''
    place = (f.get('bp') or f.get('bapp') or '').split(',')[0]
    return ' '.join(x for x in (row['label'], y.lstrip('<>~'), place) if x)


def fact_note(f):
    """facts as 'by=~1860 bp=San_Juan,Villa': spaces as _, and %, |, ;, = and _ escaped so the note stays parseable."""
    def esc(v):
        return re.sub(r'[%|;=_\s]', lambda m: '_' if m.group().isspace() else f'%{ord(m.group()):02X}', str(v))
    return ' '.join(f'{k}={esc(v)}' for k, v in sorted(f.items()))


def where_note(w):
    """A where object in fact_note's escaping; online as true or false, and empty values left out."""
    shown = {k: ('true' if v else 'false') if isinstance(v, bool) else v for k, v in w.items()}
    return fact_note({k: v for k, v in shown.items() if v not in (None, '')})


def group_by_person(rows):
    """One task per person and task kind: the gaps of one person are one search, not one search per gap.
    Priority is per person, so every row of a group carries the same one."""
    groups = {}
    for r in rows:
        g = groups.setdefault((r['person'], task_of(r)), dict(r, task=task_of(r), gaps=[]))
        g['gaps'].append(r)
    return list(groups.values())


def queue_task(r, source=''):
    """One JSON object for `gwt-queue add --jsonl`. The dedupe key is person, task kind and source (plus the gap
    code with --per-gap), never the name, so a corrected name refreshes the open task instead of adding one."""
    gaps = r.get('gaps') or [r]
    task = task_of(r)
    note = '; '.join(f"{g['gap']}: {g['what']}" for g in gaps)
    wheres = []
    for g in gaps:
        if g.get('where') and g['where'] not in wheres:
            wheres.append(g['where'])
    if wheres:
        note = f"where: {'; '.join(where_note(w) for w in wheres)} | {note}"
    if r.get('facts'):
        note = f"facts: {fact_note(r['facts'])} | {note}"
    dedupe_key = ':'.join([r['person'].lower(), task, source.lower()] + ([] if 'gaps' in r else [r['gap']]))
    return {'person': r['person'], 'label': r['label'], 'kind': task, 'source': source, 'query': query_of(r),
            'priority': r['priority'], 'note': note, 'dedupe_key': dedupe_key}


def tsv_cell(value):
    return ' '.join(str(value).replace('\t', ' ').splitlines()).strip()


def build_parser():
    p = argparse.ArgumentParser(prog='gwt-leads', description=__doc__.split('\n')[0])
    p.add_argument('dump')
    p.add_argument('--root', help='Gramps id of the person whose ancestors rank first')
    p.add_argument('--kinds', default=','.join(DEFAULT_KINDS))
    p.add_argument('--links', metavar='FILE',
                   help='the JSON `gwt-check links --json` wrote from this same dump: adds the unsupported-link kind')
    p.add_argument('--archives', metavar='FILE',
                   help='TSV with match, archive, series, years, url (and optionally access): each gap whose place '
                        'matches a row gets where its record is kept and whether its year is online')
    p.add_argument('--limit', type=int, default=0, help='keep the N highest-priority rows (tasks, in queue format)')
    p.add_argument('--format', choices=('queue', 'tsv', 'json'), default='tsv')
    p.add_argument('--living-after', type=int, default=default_living_after(), metavar='YEAR',
                   help='with no death, burial or cremation, someone whose dates (or a descendant\'s) do not prove a '
                        'birth in YEAR or earlier may be living and is held back from every format '
                        '(default: this year minus 100, %(default)s)')
    p.add_argument('--include-living', action='store_true',
                   help='do not hold back the possibly living and the private: only for a local, private review')
    p.add_argument('--source', default='', help='queue format: the source of every task, part of its dedupe key')
    p.add_argument('--per-gap', action='store_true', help='queue format: one task per gap instead of one per person')
    p.add_argument('--skip-weak', action='store_true',
                   help='queue format: leave out people with no given name or no surname, unless they are '
                        'the root or one of its ancestors; their ids go to stderr (default since 2026-10: kept for compatibility)')
    p.add_argument('--keep-weak', action='store_true',
                   help='queue format: also queue people whose name is too thin to search (a one-word name '
                        'matches thousands of people); by default they are skipped and counted on stderr')
    return p


def check_queue_flags(p, args):
    """Refuse (argparse error, exit 2) a queue-only flag given with another format."""
    if args.format != 'queue':
        for flag, on in (('--source', args.source), ('--per-gap', args.per_gap), ('--skip-weak', args.skip_weak), ('--keep-weak', args.keep_weak)):
            if on:
                p.error(f'{flag} only applies to --format queue')


def resolve_kinds(p, args):
    """The kinds asked for, as a tuple; --links adds unsupported-link, and that kind without --links is an error."""
    check_queue_flags(p, args)
    kinds = tuple(k for k in args.kinds.split(',') if k)
    bad = [k for k in kinds if k not in KINDS]
    if bad:
        p.error(f"unknown kind: {', '.join(bad)}")
    if args.links and 'unsupported-link' not in kinds:
        kinds += ('unsupported-link',)  # --links can only mean this kind
    if 'unsupported-link' in kinds and not args.links:
        p.error('the unsupported-link kind needs --links FILE, the JSON `gwt-check links --json` writes')
    return kinds


def load_links(path):
    """The parsed `gwt-check links --json` report in a file (BadInput when unreadable or not such a report)."""
    real = checked_path(path)
    try:
        with open(real, encoding='utf-8') as fh:
            links = json.load(fh)
        if not isinstance(links, dict) or not isinstance(links.get('rows'), list):
            raise ValueError('not a gwt-check links report')
    except (OSError, ValueError) as exc:
        raise BadInput(f'{path}: {os_text(exc, real, path)}')
    return links


def report_held(held, living_after):
    """The one stderr line naming (ids only) the people held back, when there are any."""
    if held:
        n_private = sum(1 for why in held.values() if why == 'private')
        ids = ' '.join(sorted(held, key=id_order))
        print(f'gwt-leads: held back {len(held)} people ({n_private} marked private, {len(held) - n_private} who may '
              f'be living: born after {living_after} or in an unknown year, no death): {ids}', file=sys.stderr)


def queue_rows(rows, args):
    """The rows of --format queue: thin names left out unless --keep-weak (ids to stderr), grouped per person
    unless --per-gap."""
    if not args.keep_weak:
        weak = sorted({r['person'] for r in rows if r['weak'] and r['generation'] is None}, key=id_order)
        rows = [r for r in rows if not (r['weak'] and r['generation'] is None)]
        if weak:
            print(f"gwt-leads: skipped {len(weak)} people whose name is too thin to search "
                  f"(research them by hand, or --keep-weak): {' '.join(weak)}", file=sys.stderr)
    return rows if args.per_gap else group_by_person(rows)


def print_rows(rows, args):
    """One line per row in the format asked for."""
    for r in rows:
        if args.format == 'queue':
            print(json.dumps(queue_task(r, args.source), ensure_ascii=False))
        elif args.format == 'json':
            print(json.dumps(r, ensure_ascii=False))
        else:
            print('\t'.join(tsv_cell(r[c]) for c in ('priority', 'person', 'label', 'kind', 'what')))


def main(argv=None):
    p = build_parser()
    args = p.parse_args(argv)
    kinds = resolve_kinds(p, args)
    try:
        links = load_links(args.links) if args.links else None
        archives = load_archives(args.archives) if args.archives else None
        rows, held = leads(load(args.dump), args.root, kinds, args.living_after, hold=not args.include_living,
                           links=links, archives=archives)
    except BadInput as exc:
        print(f'gwt-leads: {exc}', file=sys.stderr)
        return 2
    report_held(held, args.living_after)
    if args.format == 'queue':
        rows = queue_rows(rows, args)
    if args.limit:
        rows = rows[:args.limit]
    print_rows(rows, args)
    return 0


if __name__ == '__main__':
    sys.exit(main())
