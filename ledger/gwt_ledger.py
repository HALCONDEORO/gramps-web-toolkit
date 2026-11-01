#!/usr/bin/env python3
"""gwt-ledger: shared search and image-reading ledger for parallel research agents.

One TSV file, one row per search: who was searched, where, with what query, when, and what came of it.
Agents run `check` before a search and `add` after it, so two lanes never pay for the same search twice.

  gwt-ledger check --person KEY --source SRC --query "..."   print matching rows; exit 0 if one is a search that ran
                                                             (hit, none, partial) and is not due again, else 1
                                                             (--exact: same source, identical normalized query;
                                                             --any-result: any matching row counts)
  gwt-ledger add --person KEY --label "..." --source SRC --query "..." --result none [--evidence F] [--agent A]
  gwt-ledger list --person KEY [--source SRC]
  gwt-ledger due [--on YYYY-MM-DD]                           rows whose next_date has arrived (appeal windows, retries)
  gwt-ledger stats
  gwt-ledger mark --ark ID --image-from N --image-to N --reader NAME --result negative --ref FILE
  gwt-ledger query --ark ID [--total N]                     offline JSON coverage, unread and double-read ranges

The ledger path comes from --ledger, else $GWT_LEDGER, else ./buscado.tsv. With --ledger-dir (or $GWT_LEDGER_DIR),
`add` writes to <dir>/<agent>.tsv and `mark` to <dir>/<key(reader)>.tsv;
read commands include the ledger plus all <dir>/*.tsv.
Standard library only.
"""
import argparse, collections, datetime, difflib, fcntl, glob, json, os, re, stat, sys, tempfile, unicodedata

COLUMNS = ('person_key', 'person_label', 'source', 'query', 'date', 'result', 'evidence', 'agent', 'next_date')
READING_COLUMNS = ('ark', 'image_from', 'image_to', 'reader', 'ref')
READING_RESULTS = ('negative', 'found', 'partial')
RESULTS = ('hit', 'none', 'partial', 'blocked', 'denied')
SEARCHED = ('hit', 'none', 'partial')  # results of a search that ran; blocked and denied did not answer it
DEFAULT_THRESHOLD = 0.75


def normalize(text):
    """Casefold, strip accents and punctuation, collapse spaces: «Peiró, José» -> «peiro jose». Letters and
    digits of every script stay (Иван -> иван, Ørsted -> ørsted); the underscore separates words."""
    text = unicodedata.normalize('NFKD', (text or '').casefold())
    text = ''.join(c for c in text if not unicodedata.combining(c))
    return ' '.join(re.findall(r'[^\W_]+', text))


def key(text):
    """Person keys and sources compare as slugs: «José Pérez» and «jose-perez» are the same person."""
    return normalize(text).replace(' ', '-')


# Particles that rewording drops or adds without changing the search.
PARTICLES = frozenset('a al con da de del di do du e el en la las le les los of the y'.split())


def one_edit(a, b):
    """True if `a` becomes `b` with at most one insertion, deletion or substitution."""
    if abs(len(a) - len(b)) > 1:
        return False
    i = 0
    while i < min(len(a), len(b)) and a[i] == b[i]:
        i += 1
    return a[i + 1:] == b[i + 1:] or a[i + 1:] == b[i:] or a[i:] == b[i + 1:]


def words_covered(new, logged):
    """Every word of `new` matches its own word of `logged`: exactly, or (words of five letters or more,
    no digits) with one typo, Villanueba for Villanueva. One logged word never covers two new words."""
    free = set(logged)
    rest = []
    for w in new:
        if w in free:
            free.discard(w)
        else:
            rest.append(w)
    for w in rest:
        if len(w) < 5 or any(c.isdigit() for c in w):
            return False
        hit = next((x for x in sorted(free) if len(x) >= 5 and not any(c.isdigit() for c in x) and one_edit(w, x)), None)
        if hit is None:
            return False
        free.discard(hit)
    return True


def similarity(a, b):
    """Fuzzy match of a new query `a` against a logged query `b`: the best of word-set overlap,
    character ratio, and (for queries of three words or more) how much of `a` the logged query covers.
    Every word of `a` except particles must be in `b`, give or take a typo: a different name, place
    or year after a long shared prefix is a different search, however high the overlap."""
    if a == b:
        return 1.0
    ta, tb = set(a.split()), set(b.split())
    if not words_covered(ta - PARTICLES, tb):
        return 0.0
    jaccard = len(ta & tb) / len(ta | tb) if ta | tb else 0.0
    covered = len(ta & tb) / len(ta) if len(ta) >= 3 else 0.0
    return max(jaccard, covered, difflib.SequenceMatcher(None, a, b).ratio())


def clean(value):
    return ' '.join(str(value or '').replace('\t', ' ').splitlines()).strip()


def read_rows(path):
    if not os.path.exists(path):
        return []
    rows = []
    with open(path, encoding='utf-8') as f:
        columns = COLUMNS
        for n, line in enumerate(f):
            parts = line.rstrip('\n').split('\t')
            if n == 0 and parts[0] == COLUMNS[0]:
                columns = tuple(parts)
                continue  # header; a ledger from before next_date has 8 columns and reads the same
            row = dict.fromkeys(COLUMNS + READING_COLUMNS, '')
            # Old add versions appended a ninth cell under an eight-column header.
            names = COLUMNS if columns == COLUMNS[:-1] else columns
            row.update(zip(names, parts))
            rows.append(row)
    return rows


def ledger_paths(ledger, ledger_dir=''):
    """The main ledger first, then every per-lane file of ledger_dir."""
    return [ledger] + (sorted(glob.glob(os.path.join(ledger_dir, '*.tsv'))) if ledger_dir else [])


def writable_ledger(path):
    """Reject inode aliases that replacement cannot coordinate safely."""
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(info.st_mode) or info.st_nlink > 1:
        raise OSError('ledger must be a regular file with one link; symlinks and hardlinks are refused')
    if info.st_uid != os.geteuid():
        raise OSError("shared ledger writers must use the same UID as the ledger owner")
    return info


def validate_write_schema(path):
    """Refuse unfamiliar layouts rather than discard columns during replacement."""
    if not os.path.exists(path):
        return
    with open(path, encoding='utf-8') as f:
        columns = COLUMNS
        for n, line in enumerate(f):
            parts = line.rstrip('\n').split('\t')
            if n == 0 and parts[0] == COLUMNS[0]:
                columns = tuple(parts)
                if columns not in (COLUMNS[:-1], COLUMNS, COLUMNS + READING_COLUMNS):
                    raise OSError('unsupported ledger header; refusing to discard columns')
                continue
            widths = (8, 9) if columns in (COLUMNS[:-1], COLUMNS) else (len(columns),)
            if len(parts) not in widths:
                raise OSError('unsupported ledger row width; refusing to discard cells')


def extended_attributes(path):
    """Include ACLs exposed as xattrs; inability to inspect them is a write refusal."""
    if not all(hasattr(os, name) for name in ('listxattr', 'getxattr', 'setxattr', 'removexattr')):
        raise OSError('cannot preserve ledger ACLs/extended attributes on this platform')
    return {name: os.getxattr(path, name) for name in os.listxattr(path)}


def preserve_metadata(fd, original, attributes):
    current = os.fstat(fd)
    if (current.st_uid, current.st_gid) != (original.st_uid, original.st_gid):
        os.fchown(fd, original.st_uid, original.st_gid)
    os.fchmod(fd, stat.S_IMODE(original.st_mode))
    # The temporary file may inherit a directory ACL or security attributes.
    for name in set(os.listxattr(fd)) - attributes.keys():
        os.removexattr(fd, name)
    for name, value in attributes.items():
        os.setxattr(fd, name, value)
    current = os.fstat(fd)
    if ((current.st_uid, current.st_gid, stat.S_IMODE(current.st_mode)) !=
            (original.st_uid, original.st_gid, stat.S_IMODE(original.st_mode)) or
            extended_attributes(fd) != attributes):
        raise OSError('cannot preserve ledger ownership, permissions or ACLs')


def file_version(info):
    # Reading can update atime without changing the ledger; exclude it.
    return None if info is None else (info.st_dev, info.st_ino, info.st_mode, info.st_nlink,
                                     info.st_uid, info.st_gid, info.st_size,
                                     info.st_mtime_ns, info.st_ctime_ns)


def open_sidecar(path):
    """Stable same-UID lock only; never delete or replace an existing lock inode."""
    fd = os.open(path + '.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    info = os.fstat(fd)
    if info.st_uid != os.geteuid() or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        os.close(fd)
        raise OSError("ledger sidecar must be a regular single-link file owned by the writer UID")
    return os.fdopen(fd, 'a', encoding='utf-8')


def add_row(path, row):
    """Add under a stable sidecar lock; atomic replacement also upgrades old headers.

    Every writer locks the sidecar, never the inode replaced by rename. Readers see
    the complete old or new file. All writers must use this version when sharing it.
    """
    path = os.fspath(path)
    writable_ledger(path)  # refuse file symlinks/hardlinks before creating even a lock
    path = os.path.realpath(path)  # directory aliases must use one read/write/lock path
    validate_write_schema(path)  # early refusal makes no files, including a sidecar
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    with open_sidecar(path) as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        tmp = None
        try:
            original = writable_ledger(path)
            attributes = extended_attributes(path) if original else None
            validate_write_schema(path)
            rows = read_rows(path)
            rows.append(row)
            columns = COLUMNS + READING_COLUMNS if any(r.get(c) for r in rows for c in READING_COLUMNS) else COLUMNS
            fd, tmp = tempfile.mkstemp(prefix='.gwt-ledger-', suffix='.tmp', dir=directory)
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                f.write('\t'.join(columns) + '\n')
                for entry in rows:
                    f.write('\t'.join(clean(entry.get(c)) for c in columns) + '\n')
                f.flush()
                if original:
                    preserve_metadata(f.fileno(), original, attributes)
                os.fsync(f.fileno())
            current = writable_ledger(path)
            if (file_version(original) != file_version(current) or
                    (original and extended_attributes(path) != attributes)):
                raise OSError('ledger changed outside the sidecar lock; refusing replacement')
            os.replace(tmp, path)
        finally:
            if tmp and os.path.exists(tmp):
                os.unlink(tmp)
            fcntl.flock(lock, fcntl.LOCK_UN)


def positive_image(value):
    n = int(value)
    if n < 1:
        raise ValueError('image numbers and totals must be positive')
    return n


def reading_events(rows, ark):
    """Validated marks of one source, plus reader-count changes at each interval endpoint."""
    marks = []
    events = collections.defaultdict(collections.Counter)
    for row in rows:
        if row.get('ark') != ark:
            continue
        start, end = positive_image(row['image_from']), positive_image(row['image_to'])
        if start > end or row['result'] not in READING_RESULTS or not clean(row['reader']):
            raise ValueError('invalid stored reading row')
        mark = {c: row[c] for c in ('ark', 'image_from', 'image_to', 'reader', 'date', 'result', 'ref')}
        mark.update(image_from=start, image_to=end)
        marks.append(mark)
        events[start][row['reader']] += 1
        events[end + 1][row['reader']] -= 1
    return marks, events


def append_range(ranges, lo, hi):
    if ranges and ranges[-1][1] + 1 == lo:
        ranges[-1][1] = hi
    else:
        ranges.append([lo, hi])


def sweep_ranges(events):
    """Covered, unread and distinct-reader double-read ranges between sorted endpoints."""
    covered, unread, double = [], [], []
    active = collections.Counter()
    points = sorted(events)
    for i, lo in enumerate(points[:-1]):
        active.update(events[lo])
        hi = points[i + 1] - 1
        readers = sum(count > 0 for count in active.values())
        append_range(covered if readers else unread, lo, hi)
        if readers >= 2:
            append_range(double, lo, hi)
    return covered, unread, double


def reading_coverage(rows, ark, total=None):
    """Sweep interval endpoints, retaining independent readers without expanding images."""
    marks, events = reading_events(rows, ark)
    upper = max((r['image_to'] for r in marks), default=0)
    if total is not None and total < upper:
        raise ValueError('--total is below a logged image')
    bound = total if total is not None else upper
    # Start at image 1, including any known leading gap, and end after the bound.
    events.setdefault(1, collections.Counter())
    events.setdefault(bound + 1, collections.Counter())
    covered, unread, double = sweep_ranges(events)
    return dict(ark=ark, rows=marks, covered=covered, unread=unread, double_read=double,
                total=total, unknown_tail=total is None)


def same_lookup(r, pk, sk, q, exact):
    """The row belongs to this person and source and, with exact, has the identical normalized query."""
    if pk and key(r['person_key']) != pk:
        return False
    if sk and key(r['source']) != sk:
        return False
    return not exact or normalize(r['query']) == q


def find(rows, person=None, source=None, query=None, threshold=DEFAULT_THRESHOLD, exact=False):
    """Rows for this person and source whose query is close enough, best match first and, among equal
    scores, newest first (by date, then by position), so an old `none` cannot hide a newer `hit`.
    With exact=True only the identical normalized query counts."""
    pk, sk, q = key(person) if person else None, key(source) if source else None, normalize(query or '')
    out = []
    for i, r in enumerate(rows):
        if r.get('ark'):
            continue  # reading marks never satisfy a person/search lookup
        if not same_lookup(r, pk, sk, q, exact):
            continue
        score = 1.0 if exact or not q else similarity(q, normalize(r['query']))
        if exact or score >= threshold:
            out.append((score, r['date'], i, r))
    if q:
        out.sort(key=lambda x: x[:3], reverse=True)
    return [(score, r) for score, _, _, r in out]


def print_rows(scored, with_score=False):
    for score, r in scored:
        cells = [r[c] for c in COLUMNS]
        sys.stdout.write((f'{score:.2f}\t' if with_score else '') + '\t'.join(cells) + '\n')


def iso_date(value):
    """YYYY-MM-DD or empty; anything else is an argparse error, so `due` never compares a typo as text."""
    return datetime.date.fromisoformat(value).isoformat() if value else ''


def build_parser():
    p = argparse.ArgumentParser(prog='gwt-ledger', description=__doc__.split('\n')[0])
    p.add_argument('--ledger', default=os.environ.get('GWT_LEDGER', 'buscado.tsv'))
    p.add_argument('--ledger-dir', default=os.environ.get('GWT_LEDGER_DIR', ''),
                   help='lane files: add uses agent, mark uses reader; read commands include all *.tsv here')
    sub = p.add_subparsers(dest='cmd', required=True)
    c = sub.add_parser('check', help='has this search been done? print the matching rows; exit 0 if one is a '
                                     'search that ran and is not due again, 1 if not')
    c.add_argument('--person'); c.add_argument('--source'); c.add_argument('--query', required=True)
    c.add_argument('--threshold', type=float, default=DEFAULT_THRESHOLD)
    c.add_argument('--exact', action='store_true',
                   help='only rows of this --source (required) with the identical normalized query; safe for automatic reuse')
    c.add_argument('--any-result', action='store_true',
                   help='exit 0 on any matching row, blocked, denied or due again included')
    a = sub.add_parser('add', help='append a row (safe with concurrent writers)')
    a.add_argument('--person', required=True); a.add_argument('--label', default='')
    a.add_argument('--source', required=True); a.add_argument('--query', required=True)
    a.add_argument('--result', required=True, choices=RESULTS)
    a.add_argument('--evidence', default=''); a.add_argument('--agent', default=os.environ.get('GWT_AGENT', ''))
    a.add_argument('--date', default=datetime.date.today().isoformat())
    a.add_argument('--next-date', default='', type=iso_date, help='ISO date to look again, e.g. the end of an appeal window')
    d = sub.add_parser('due', help='rows whose next_date is on or before a day (default today)')
    d.add_argument('--on', default=datetime.date.today().isoformat(), type=iso_date)
    l = sub.add_parser('list', help='rows for one person')
    l.add_argument('--person', required=True); l.add_argument('--source')
    sub.add_parser('stats', help='counts by result, source and agent')
    m = sub.add_parser('mark', help='record an inclusive image reading range offline; preserve every reading')
    m.add_argument('--ark', required=True, help='exact source identifier; never fetched or normalized')
    m.add_argument('--image-from', required=True, type=positive_image)
    m.add_argument('--image-to', required=True, type=positive_image)
    m.add_argument('--reader', required=True, help='reader identity; also selects the lane file')
    m.add_argument('--date', default=datetime.date.today().isoformat(), type=iso_date)
    m.add_argument('--result', required=True, choices=READING_RESULTS)
    m.add_argument('--ref', required=True, help='local evidence reference, stored without opening it')
    q = sub.add_parser('query', help='offline JSON: covered, unread, distinct-reader double_read ranges and rows')
    q.add_argument('--ark', required=True, help='exact source identifier')
    q.add_argument('--total', type=positive_image,
                   help='known final image; without it the tail after known coverage stays unknown')
    return p


def lane_path(p, args, lane, message):
    """The main ledger, or this lane's file inside --ledger-dir."""
    if not args.ledger_dir:
        return args.ledger
    if not key(lane):
        p.error(message)
    return os.path.join(args.ledger_dir, key(lane) + '.tsv')


def run_reading(p, args):
    """mark and query: offline reading index commands."""
    for name in (('ark', 'reader', 'ref', 'date') if args.cmd == 'mark' else ('ark',)):
        value = getattr(args, name)
        if not value.strip() or clean(value) != value:
            p.error(f'--{name} must be non-empty with no tabs, newlines or edge spaces')
    if args.cmd == 'mark' and args.image_from > args.image_to:
        p.error('--image-from must not exceed --image-to')
    try:
        if args.cmd == 'mark':
            path = lane_path(p, args, args.reader, '--reader needs a lane name after normalising')
            add_row(path, {c: getattr(args, c) for c in
                          ('ark', 'image_from', 'image_to', 'reader', 'date', 'result', 'ref')})
        else:
            rows = [r for path in ledger_paths(args.ledger, args.ledger_dir) for r in read_rows(path)]
            print(json.dumps(reading_coverage(rows, args.ark, args.total), ensure_ascii=False))
    except (OSError, UnicodeDecodeError) as exc:  # before ValueError: UnicodeDecodeError is a ValueError
        sys.stderr.write(f'gwt-ledger: cannot read/write the ledger: {exc}\n')
        return 2
    except ValueError as exc:
        p.error(str(exc))
    return 0


def run_add(p, args):
    path = lane_path(p, args, args.agent, 'add needs --agent (or $GWT_AGENT): rows go to <ledger-dir>/<agent>.tsv')
    try:
        add_row(path, {'person_key': key(args.person), 'person_label': args.label,
                       'source': key(args.source), 'query': normalize(args.query), 'date': args.date,
                       'result': args.result, 'evidence': args.evidence, 'agent': args.agent,
                       'next_date': args.next_date})
    except (OSError, UnicodeDecodeError) as exc:
        sys.stderr.write(f'gwt-ledger: cannot read/write the ledger: {exc}\n')
        return 2
    return 0


def print_stats(rows):
    print(f'rows\t{len(rows)}')
    print(f"persons\t{len({key(r['person_key']) for r in rows})}")
    for col in ('result', 'source', 'agent'):
        for value, n in collections.Counter(r[col] for r in rows).most_common():
            print(f"{col}\t{value or '-'}\t{n}")


def main(argv=None):
    p = build_parser()
    args = p.parse_args(argv)
    if args.cmd in ('mark', 'query'):
        return run_reading(p, args)
    # A query or person that normalizes to nothing (only punctuation, say) would match every row.
    for name in ('query', 'person'):
        value = getattr(args, name, None)
        if value is not None and not normalize(value):
            p.error(f'--{name} {value!r}: nothing left to match after normalising')

    if args.cmd == 'add':
        return run_add(p, args)
    try:
        rows = [r for path in ledger_paths(args.ledger, args.ledger_dir) for r in read_rows(path) if not r.get('ark')]
    except (OSError, UnicodeDecodeError) as exc:  # an unreadable ledger is an error, never "not searched yet"
        sys.stderr.write(f'gwt-ledger: cannot read the ledger: {exc}\n')
        return 2
    if args.cmd == 'check':
        if args.exact and not key(args.source or ''):
            p.error('check --exact needs a non-empty --source')
        scored = find(rows, args.person, args.source, args.query, args.threshold, args.exact)
        print_rows(scored, with_score=True)
        today = datetime.date.today().isoformat()
        done = [r for _, r in scored if r['result'] in SEARCHED and (not r['next_date'] or r['next_date'] > today)]
        return 0 if (scored if args.any_result else done) else 1
    if args.cmd == 'due':
        due = [(1.0, r) for r in rows if r['next_date'] and r['next_date'] <= args.on]
        due.sort(key=lambda x: x[1]['next_date'])
        print_rows(due)
        return 0
    if args.cmd == 'list':
        print_rows(find(rows, args.person, args.source))
        return 0
    print_stats(rows)
    return 0


if __name__ == '__main__':
    sys.exit(main())
