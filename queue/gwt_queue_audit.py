#!/usr/bin/env python3
"""gwt-queue-audit: offline, read-only audit of a gwt_queue TSV file.

Checks the schema (header, column count, enums, numbers, instants), task ids (duplicates, ids that collide
once case and spaces are ignored, shared dedupe keys), stale blocked work, impossible done states, missing
provenance and prerequisites (unknown ids, self references, cycles). Repeated header lines and repeated
column names are reported, rows with extra tabs are reported (not re-split), and a done row whose
prerequisite is not done counts as a false completion. Cycle search is linear (iterative Tarjan) and
long id lists in messages are truncated, so a giant queue or one huge cycle stays cheap.

The queue is opened for reading only: no lock file, no rewrite, no reopening of expired claims, no
reordering, no completing. Every finding carries a review-only suggestion for a person; nothing is applied.
Source and private flags are echoed as found. Finding messages never quote query, evidence or note text.

Prerequisites are not a column of the queue; by convention a row lists them in its note as
`needs:q1,q2` (also `after:` or `prereq:`). A row is private when its note has the word `private`
or its source starts with `private`.

  python3 gwt_queue_audit.py --queue cola.tsv [--json-out F] [--tsv-out F] [--stale-days N] [--format json|tsv]

Exit status: 0 no findings, 1 findings, 2 unusable input. Standard library only.
"""
from pathlib import Path
import argparse, collections, datetime, json, math, os, re, sys, tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gwt_queue as Q

DEFAULT_STALE_DAYS = 14
# Declared bounds (tested): a larger queue file is unusable input (exit 2); stale days must be finite and in
# range; text copied into a finding (ids, source) is cut at MAX_FIELD_CHARS; one row lists each prerequisite
# once and at most MAX_PREREQS of them are read.
MAX_QUEUE_BYTES = 64 * 1024 * 1024
MAX_STALE_DAYS = 36500.0
MAX_FIELD_CHARS = 200
MAX_PREREQS = 1000
_TOK = r'[A-Za-z0-9_.\-]+(?![A-Za-z0-9_.\-:])'  # an id that is not itself a `word:` keyword
PREREQ_RE = re.compile(rf'\b(?:needs|after|prereq):\s*({_TOK}(?:[,\s]+{_TOK})*)', re.I)
MAX_LISTED_LINES = 5
PRIVATE_RE = re.compile(r'\bprivate\b', re.I)
ID_RE = re.compile(r'q\d+')
TSV_COLUMNS = ('finding', 'severity', 'code', 'task_id', 'line', 'status', 'source', 'private',
               'message', 'review_only', 'suggested_action')
ACTIONS = {
    'schema-header': 'Compare the header with the gwt_queue columns by hand; do not rewrite the file from here.',
    'schema-columns': 'Inspect the line for stray tabs; fix it by hand in a copy, then re-audit.',
    'schema-value': 'Decide by hand whether the value is a typo; gwt_queue reads a bad number as 0.',
    'id-missing': 'Decide whether this row is junk; ids are never assigned by the audit.',
    'id-format': 'Check the id was typed by hand; gwt_queue only issues q<number> ids.',
    'id-duplicate': 'Keep one row per id after comparing them; renumber the other in a copy, never here.',
    'id-collision': 'Ids differ only by case or spaces and will be confused by people; pick one spelling.',
    'dedupe-collision': 'Compare the rows and decide which one stands; the others may be retired by a person.',
    'stale-blocked': 'Look at why the search was blocked and choose whether to re-queue it; nothing is re-opened.',
    'stale-claim': 'The lease ran out; the next gwt-queue claim/report reopens it by itself. Check the lane.',
    'done-state': 'Re-check the work behind this done row; do not edit status or result from the audit.',
    'open-state': 'A row that is not done holds done-only fields; check how it got there.',
    'provenance': 'Add the missing source/person/evidence by hand after finding the original lead.',
    'prereq-missing': 'The needed task id is not in the queue; fix the reference or re-add the task by hand.',
    'prereq-self': 'A task cannot wait for itself; remove the reference in a copy.',
    'prereq-cycle': 'Tasks wait on each other; choose which prerequisite to drop. No order is changed here.',
}


def finding(code, severity, row, message):
    """One finding. row is a parsed row dict (or None for file-wide findings)."""
    row = row or {}
    return {'code': code, 'severity': severity, 'task_id': short(row.get('id', '')), 'line': row.get('_line', 0),
            'status': row.get('status', '') if row.get('status', '') in Q.STATUS else '', 'source': short(row.get('source', '')),
            'private': is_private(row),
            'message': message, 'review_only': True, 'suggested_action': 'REVIEW ONLY: ' + ACTIONS[code]}


def short(text):
    """Cut text copied into a finding at MAX_FIELD_CHARS."""
    text = str(text or '')
    return text if len(text) <= MAX_FIELD_CHARS else text[:MAX_FIELD_CHARS] + '...'


def finite_days(value):
    """Stale days as a finite number in 0..MAX_STALE_DAYS; nan, inf, negative and huge values raise ValueError."""
    days = float(value)
    if not math.isfinite(days) or days < 0 or days > MAX_STALE_DAYS:
        raise ValueError(f'stale days must be a finite number from 0 to {int(MAX_STALE_DAYS)}')
    return days


def stale_days_arg(value):
    try:
        return finite_days(value)
    except ValueError as e:
        raise argparse.ArgumentTypeError(str(e))


def is_private(row):
    return bool(PRIVATE_RE.search(row.get('note', '') or '') or (row.get('source', '') or '').lower().startswith('private'))


def read_raw(path):
    """(header_parts_or_None, [(line_number, parts)]) read without touching the file."""
    if os.path.getsize(path) > MAX_QUEUE_BYTES:
        raise ValueError(f'queue file is larger than {int(MAX_QUEUE_BYTES)} bytes')
    with Path(path).open('rb') as f:  # bytes, so the cap holds even if the file grew after the size check
        data = f.read(MAX_QUEUE_BYTES + 1)
    if len(data) > MAX_QUEUE_BYTES:
        raise ValueError(f'queue file is larger than {int(MAX_QUEUE_BYTES)} bytes')
    text = data.decode('utf-8')
    header, lines = None, []
    for n, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        parts = line.split('\t')
        if header is None and not lines and parts[0] == Q.COLUMNS[0]:
            header = parts
            continue
        lines.append((n, parts))
    return header, lines


def to_row(n, parts):
    padded = (parts + [''] * len(Q.COLUMNS))[:len(Q.COLUMNS)]
    row = dict(zip(Q.COLUMNS, padded))
    row['_line'] = n
    row['_cols'] = len(parts)
    return row


def check_header(header):
    if header is None:
        return [finding('schema-header', 'error', None, 'no header line (first line must start with "id")')]
    if tuple(header) != Q.COLUMNS:
        dup = ' (a column name is repeated)' if len(set(header)) != len(header) else ''
        return [finding('schema-header', 'error', None,
                        f'header has {len(header)} columns and differs from the gwt_queue columns{dup}')]
    return []


def split_repeated_headers(raw):
    """(data lines, findings): a later line that is the header again is not a task; gwt_queue would read it as one."""
    keep, out = [], []
    for n, parts in raw:
        if tuple(parts) == Q.COLUMNS:
            out.append(finding('schema-header', 'warning', {'_line': n}, 'header line repeated'))
        else:
            keep.append((n, parts))
    return keep, out


def check_done_prereqs(rows):
    """A done row whose prerequisite is unknown-state (not done) was probably marked done too early."""
    states = collections.defaultdict(set)
    for r in rows:
        states[r['id']].add(r['status'])
    out = []
    for r in rows:
        if r['status'] != 'done':
            continue
        for need in prereqs(r):
            if need != r['id'] and need in states and states[need] != {'done'}:
                out.append(finding('done-state', 'error', r, 'done while a prerequisite is not done'))
    return out


def check_columns(row):
    if row['_cols'] != len(Q.COLUMNS):
        return [finding('schema-columns', 'error', row,
                        f"row has {int(row['_cols'])} columns, expected {len(Q.COLUMNS)}")]
    return []


def enum_problems(row):
    bad = []
    for col, allowed in (('status', Q.STATUS), ('kind', Q.KINDS)):
        if row[col] not in allowed:
            bad.append(f"{col} is not one of {'/'.join(allowed)}")
    if row['result'] and row['result'] not in Q.RESULTS:
        bad.append(f"result not one of {'/'.join(Q.RESULTS)}")
    return bad


def number_problems(row):
    bad = []
    for col in ('priority', 'tries'):
        if row[col] and not re.fullmatch(r'-?\d+', row[col].strip()):
            bad.append(f'{col} is not an integer')
    for col in ('claimed_at', 'done_at', 'next_at', 'lease_until'):
        if row[col] and Q.parse_iso(row[col]) is None:
            bad.append(f'{col} is not an ISO instant')
    return bad


def check_values(row):
    return [finding('schema-value', 'error', row, p) for p in enum_problems(row) + number_problems(row)]


def check_ids(rows):
    out, seen, folded = [], collections.defaultdict(list), collections.defaultdict(set)
    for r in rows:
        if not r['id'].strip():
            out.append(finding('id-missing', 'error', r, 'row has an empty id'))
            continue
        seen[r['id']].append(r)
        folded[r['id'].strip().casefold()].add(r['id'])
        if not ID_RE.fullmatch(r['id']):
            out.append(finding('id-format', 'info', r, 'id is not of the form q<number>'))
    out += dup_findings(seen)
    out += collision_findings(rows, folded)
    return out


def dup_findings(seen):
    out = []
    for group in seen.values():
        if len(group) > 1:
            lines = ', '.join(str(g['_line']) for g in group[:MAX_LISTED_LINES])
            lines += ', ...' if len(group) > MAX_LISTED_LINES else ''
            out += [finding('id-duplicate', 'error', g, f'id used by {len(group)} rows (lines {lines})')
                    for g in group]
    return out


def collision_findings(rows, folded):
    clash = {k for k, v in folded.items() if len(v) > 1}
    return [finding('id-collision', 'warning', r, 'id collides with another id once case/spaces are ignored')
            for r in rows if r['id'].strip().casefold() in clash]


def check_dedupe_keys(rows):
    groups = collections.defaultdict(list)
    for r in rows:
        if r['dedupe_key'] and r['status'] in ('open', 'claimed'):
            groups[r['dedupe_key']].append(r)
    out = []
    for group in groups.values():
        if len(group) > 1:
            out += [finding('dedupe-collision', 'warning', g,
                            f'dedupe_key shared by {len(group)} live rows') for g in group]
    return out


def age_days(value, now):
    when = Q.parse_iso(value)
    return None if when is None else (now - when).total_seconds() / 86400.0


def check_stale(row, now, stale_days):
    out = []
    if row['status'] == 'done' and row['result'] in ('blocked', 'denied'):
        age = age_days(row['done_at'], now)
        if age is None or age >= stale_days:
            detail = 'undated' if age is None else f'unattended for {int(age)} days'
            out.append(finding('stale-blocked', 'warning', row,
                               f"done as {row['result']} and {detail}"))
    if row['status'] == 'claimed':
        try:
            end = Q.lease_end(row)
        except OverflowError:
            return out + [finding('schema-value', 'error', row, 'lease end cannot be computed (date out of range)')]
        if end is None or now > end:
            out.append(finding('stale-claim', 'warning', row, 'claim has no live lease'))
    return out


def done_problems(row, now):
    bad = []
    if not row['result']:
        bad.append('done without a result')
    if not Q.parse_iso(row['done_at']):
        bad.append('done without a valid done_at')
    if row['token'] or row['lease_until']:
        bad.append('done row still holds a claim token or lease')
    bad += done_time_problems(row, now)
    return bad


def done_time_problems(row, now):
    done, claimed = Q.parse_iso(row['done_at']), Q.parse_iso(row['claimed_at'])
    bad = []
    if done and done > now:
        bad.append('done_at is in the future')
    if done and claimed and done < claimed:
        bad.append('done_at is before claimed_at')
    if Q.as_int(row['tries']) < 1:
        bad.append('done with no recorded attempt')
    return bad


def open_problems(row):
    bad = []
    if row['done_at']:
        bad.append(f"has done_at but is {row['status']}")
    if row['result']:
        bad.append(f"has a result but is {row['status']}")
    if row['status'] == 'open' and (row['token'] or row['lane']):
        bad.append('open but still holds a lane or token')
    if row['status'] == 'claimed' and not row['token']:
        bad.append('claimed without a token')
    return bad


def check_state(row, now):
    if row['status'] == 'done':
        return [finding('done-state', 'error', row, p) for p in done_problems(row, now)]
    if row['status'] in Q.STATUS:
        return [finding('open-state', 'warning', row, p) for p in open_problems(row)]
    return []


def check_provenance(row):
    out = []
    for col in ('source', 'person_key'):
        if not row[col].strip():
            out.append(finding('provenance', 'warning', row, f'{col} is empty'))
    if row['status'] == 'done' and row['result'] == 'hit' and not row['evidence'].strip():
        out.append(finding('provenance', 'warning', row, 'hit without evidence'))
    return out


def prereqs(row):
    """Distinct prerequisite ids in first-seen order. Repeats are dropped before the cap is applied and
    reading stops at MAX_PREREQS distinct ids, so memory stays bounded however long the note is."""
    seen = {}
    for m in PREREQ_RE.finditer(row['note']):
        for tok in re.finditer(r'[^,\s]+', m.group(1)):
            seen.setdefault(tok.group(), None)
            if len(seen) >= MAX_PREREQS:
                return list(seen)
    return list(seen)


def build_graph(rows):
    graph = collections.defaultdict(set)
    for r in rows:
        if r['id']:
            graph[r['id']].update(prereqs(r))
    return graph


def check_prereq_refs(rows):
    known = {r['id'] for r in rows if r['id']}
    out = []
    for r in rows:
        for need in prereqs(r):
            if need == r['id']:
                out.append(finding('prereq-self', 'error', r, 'task lists itself as a prerequisite'))
            elif need not in known:
                out.append(finding('prereq-missing', 'error', r, 'a prerequisite is not in the queue'))
    return out


def find_cycles(graph):
    """Sorted id groups that wait on each other (self loops excluded: they are reported on their own).
    Iterative Tarjan: linear in ids + references, no recursion limit, so giant queues stay cheap."""
    index, low, stack, on, out = {}, {}, [], set(), []

    def enter(node, work):
        index[node] = low[node] = len(index)
        stack.append(node)
        on.add(node)
        work.append((node, iter(sorted(graph.get(node, ())))))

    for root in sorted(set(graph) | {m for v in graph.values() for m in v}):
        if root in index:
            continue
        work = []
        enter(root, work)
        while work:
            node, it = work[-1]
            for nxt in it:
                if nxt not in index:
                    enter(nxt, work)
                    break
                if nxt in on:
                    low[node] = min(low[node], index[nxt])
            else:
                work.pop()
                if work:
                    low[work[-1][0]] = min(low[work[-1][0]], low[node])
                if low[node] == index[node]:
                    comp = []
                    while not comp or comp[-1] != node:
                        comp.append(stack.pop())
                        on.discard(comp[-1])
                    if len(comp) > 1:
                        out.append(sorted(comp))
    return sorted(out)


def check_cycles(rows):
    by_id = collections.defaultdict(list)
    for r in rows:
        by_id[r['id']].append(r)
    out = []
    for cycle in find_cycles(build_graph(rows)):
        text = ' -> '.join(short(i) for i in cycle[:8]) + (f' ... ({len(cycle)} tasks)' if len(cycle) > 8 else '')
        out += [finding('prereq-cycle', 'error', r, f'prerequisite cycle among: {text}')
                for i in cycle for r in by_id.get(i, [])]
    return out


def row_checks(row, now, stale_days):
    return (check_columns(row) + check_values(row) + check_stale(row, now, stale_days)
            + check_state(row, now) + check_provenance(row))


def audit_text_rows(header, raw, now=None, stale_days=DEFAULT_STALE_DAYS):
    now = now or datetime.datetime.now(datetime.timezone.utc)
    raw, found = split_repeated_headers(raw)
    rows = [to_row(n, parts) for n, parts in raw]
    found = check_header(header) + found
    for r in rows:
        found += row_checks(r, now, stale_days)
    found += check_ids(rows) + check_dedupe_keys(rows) + check_prereq_refs(rows) + check_cycles(rows)
    found += check_done_prereqs(rows)
    for i, f in enumerate(found, 1):
        f['finding'] = f'F{int(i):03}'
    return rows, found


def audit_file(path, now=None, stale_days=DEFAULT_STALE_DAYS):
    """Audit the queue at path and return the structured report. Never writes."""
    stale_days = finite_days(stale_days)
    header, raw = read_raw(path)
    rows, found = audit_text_rows(header, raw, now, stale_days)
    by_code = collections.Counter(f['code'] for f in found)
    by_sev = collections.Counter(f['severity'] for f in found)
    return {'queue': path, 'read_only': True, 'rows': len(rows), 'stale_days': stale_days,
            'summary': {'findings': len(found), 'by_severity': dict(sorted(by_sev.items())),
                        'by_code': dict(sorted(by_code.items()))},
            'findings': found}


def render_json(report):
    return json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True, allow_nan=False) + '\n'


def tsv_cell(value):
    """One TSV cell: no control characters, and no leading = + - @ that a spreadsheet would run as a formula."""
    if isinstance(value, bool):
        value = 'yes' if value else 'no'
    text = ''.join(' ' if (ord(c) < 32 or 0x7f <= ord(c) <= 0x9f) else c for c in str(value if value is not None else ''))
    text = Q.clean(text)
    return "'" + text if text[:1] in ('=', '+', '-', '@') else text


def render_tsv(report):
    lines = ['\t'.join(TSV_COLUMNS)]
    for f in report['findings']:
        lines.append('\t'.join(tsv_cell(f.get(c, '')) for c in TSV_COLUMNS))
    return '\n'.join(lines) + '\n'


def same_file(a, b):
    if os.path.realpath(a) == os.path.realpath(b) or os.path.abspath(a) == os.path.abspath(b):
        return True
    return os.path.exists(a) and os.path.exists(b) and os.path.samefile(a, b)


def bad_outputs(queue, outs):
    """Reason the report destinations are forbidden, or None: the queue itself (or an alias of it), a
    directory, a symlink, or the same destination given twice."""
    outs = [o for o in outs if o]
    for o in outs:
        if '\0' in o or '\0' in queue:
            return 'path contains a NUL character'
        if os.path.islink(o):
            return 'refusing to write the report through a symlink'
        if os.path.isdir(o):
            return 'refusing to write the report over a directory'
        if same_file(queue, o):
            return 'refusing to write the report over the queue file'
    if len(outs) == 2 and same_file(outs[0], outs[1]):
        return 'refusing to write both reports to the same file'
    return None


def snapshot(path):
    """Current bytes of path, or None when it does not exist (an unreadable file raises OSError)."""
    try:
        with Path(path).open('rb') as f:
            return f.read()
    except FileNotFoundError:
        return None


def restore(path, old):
    """Best effort: put the earlier bytes back (or remove a file that did not exist).
    Returns True when the destination is back to its earlier state, False when that failed. Never raises."""
    tmp = None
    try:
        if old is None:
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass
            return True
        fd, tmp = tempfile.mkstemp(prefix='.gwt-audit-', dir=os.path.dirname(os.path.abspath(path)))
        with os.fdopen(fd, 'wb') as f:
            f.write(old)
        os.replace(tmp, path)
        return True
    except OSError:
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass
        return False


class RollbackError(OSError):
    """A write failed and the earlier contents of some destinations could not be put back."""


def replace_all(temps, before):
    """Rename each temp into place; if one rename fails, restore the ones already replaced and re-raise.
    If a restore also fails, raise RollbackError naming the files left in the new state (no false 'restored')."""
    done = []
    try:
        for tmp, path in temps:
            os.replace(tmp, path)
            done.append(path)
    except OSError as exc:
        # the destination whose rename failed may be untouched; the ones in done were replaced
        failed = [path for path in reversed(done) if not restore(path, before[path])]
        if failed:
            raise RollbackError(f"rollback failed, these files may hold new contents: {', '.join(failed)}") from exc
        raise


def write_all(items):
    """Write [(path, text)] with best-effort all-or-nothing behaviour: every text goes to a temp file beside
    its destination first, the old contents are snapshotted, and only then are the temps renamed into place.
    If a rename fails, the destinations already replaced are restored from the snapshots.
    Limit: this is not a global transaction. A crash or kill of the process between two renames can leave
    some destinations new and others old. A failure while restoring raises RollbackError naming the files."""
    temps = []
    try:
        before = {path: snapshot(path) for path, _ in items}
        for path, text in items:
            fd, tmp = tempfile.mkstemp(prefix='.gwt-audit-', dir=os.path.dirname(os.path.abspath(path)))
            temps.append((tmp, path))
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                f.write(text)
        replace_all(temps, before)
        temps = []
    finally:
        for tmp, _ in temps:
            try:
                os.unlink(tmp)
            except OSError:
                pass


def main(argv=None):
    p = argparse.ArgumentParser(prog='gwt-queue-audit', description=__doc__.split('\n')[0])
    p.add_argument('--queue', default=os.environ.get('GWT_QUEUE', 'cola.tsv'))
    p.add_argument('--stale-days', type=stale_days_arg, default=DEFAULT_STALE_DAYS,
                   help='blocked/denied results older than this are stale (default %(default)s)')
    p.add_argument('--json-out', help='write the JSON report here (never the queue file)')
    p.add_argument('--tsv-out', help='write the TSV report here (never the queue file)')
    p.add_argument('--format', choices=('json', 'tsv'), default='json', help='what goes to stdout')
    args = p.parse_args(argv)
    try:
        why = bad_outputs(args.queue, (args.json_out, args.tsv_out))
    except (OSError, ValueError) as e:
        why = f'cannot check the report destinations: {e.__class__.__name__}'
    if why:
        p.error(why)
    try:
        report = audit_file(args.queue, stale_days=args.stale_days)
    except (OSError, ValueError) as e:  # UnicodeDecodeError is a ValueError
        sys.stderr.write(f'gwt-queue-audit: cannot read the queue: {e.__class__.__name__}\n')
        return 2
    try:
        text = render_json(report) if args.format == 'json' else render_tsv(report)
        items = [(args.json_out, render_json(report)), (args.tsv_out, render_tsv(report))]
        write_all([(path, body) for path, body in items if path])
        sys.stdout.write(text)
    except RollbackError as e:
        sys.stderr.write(f'gwt-queue-audit: cannot write the report and the rollback FAILED; do not trust the report files: {e}\n')
        return 2
    except (OSError, ValueError) as e:
        sys.stderr.write(f'gwt-queue-audit: cannot write the report: {e.__class__.__name__}\n')
        return 2
    return 1 if report['findings'] else 0


if __name__ == '__main__':
    sys.exit(main())
