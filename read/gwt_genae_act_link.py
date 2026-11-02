#!/usr/bin/env python3
"""gwt_genae_act_link: offline bridge from a Genae index candidate to a checklist for the original act.

A Genae row (see gwt_genae.candidates) is an index lead. It is not the image of the parish book and it is not a
zoom-verified act. This module reads candidates (JSON, as gwt_genae writes them) plus optional evidence a person
typed in (parish, book, page, cote, ark, image_url, readings), and writes a review TSV and JSON for gwt-acts.

Rules:
  * the candidate's index URL is kept verbatim; it is validated, never rewritten;
  * no path, ARK or image URL is ever built from another field: a missing field stays listed as missing;
  * stage is `indexed-lead` (default), `image-located` (a valid image URL or ARK, and a cote), or `readings-agree`
    when two complete readings by two different readers, each with its own valid crop, agree. This is a checklist
    stage only: the bridge NEVER emits `verificado-zoom`. State is always `por-confirmar-acta` until a human writer
    reviews the act; `blockers` lists missing evidence (date_iso, filiation, writer review). Nothing is guessed;
  * nothing is fetched, no OCR is run, no tree is written. Standard library only.
"""
import argparse, copy, csv, datetime, io, json, os, re, sys, tempfile, urllib.parse

STAGES = ('indexed-lead', 'image-located', 'readings-agree')
LOCATORS = ('locality', 'parish', 'book', 'page', 'cote', 'ark', 'image_url')
TEXT_KEYS = LOCATORS + ('date_iso', 'filiation')
READ_FIELDS = ('given', 'surname', 'date_iso')
INDEX_HOSTS = ('genae.org', 'www.genae.org')
ARK = re.compile(r'^ark:/\d{5,9}/[A-Za-z0-9]+(?:/[A-Za-z0-9._-]+)*$')
ISO = re.compile(r'^\d{4}-\d{2}-\d{2}$')
EVIDENCE_KEYS = LOCATORS + ('date_iso', 'filiation', 'readings')
COLUMNS = ('link_id', 'candidate_id', 'stage', 'index_url', 'name_in_index', 'date_in_index', 'locality', 'parish', 'book', 'page', 'cote', 'ark',
           'image_url', 'date_iso', 'filiation', 'state', 'zoom', 'readers', 'readings', 'missing', 'blockers',
           'problems', 'next_steps')


def cell(value):
    """One output cell: one line, no tab, no leading spreadsheet-formula character."""
    out = re.sub(r'\s+', ' ', str(value or '')).strip()
    return "'" + out if out[:1] in ('=', '+', '-', '@') else out


def _parts(url):
    """The parsed URL, or None when it holds whitespace, control characters or cannot be parsed."""
    if not isinstance(url, str) or not url or re.search(r'[\s\x00-\x1f\x7f]', url):
        return None
    try:
        p = urllib.parse.urlsplit(url)
        p.port  # noqa: B018 - raises ValueError on a bad port
    except ValueError:
        return None
    return p


def url_problem(url, hosts=None):
    """None when url is a plain https URL (on one of hosts, if given); else the reason. The URL is not changed."""
    p = _parts(url)
    if p is None:
        return 'not a single-token URL'
    if p.scheme != 'https' or not p.hostname:
        return 'not an https URL with a host'
    if p.username is not None or p.password is not None:
        return 'URL carries credentials'
    if hosts and p.hostname.lower() not in hosts:
        return f"host {p.hostname} is not one of {', '.join(hosts)}"
    return None


def ark_problem(ark):
    """None for a bare `ark:/NAAN/name[/qualifier]`; else the reason. A URL is not an ARK."""
    if not isinstance(ark, str) or not ARK.match(ark):
        return 'ark must look like ark:/NAAN/name'
    return None


def _clean(value):
    """Stripped text; anything that is not a string (bool, number, list...) counts as absent."""
    return value.strip() if isinstance(value, str) else ''


def _nontext(d, who, keys):
    """Problems for keys present in d whose value is neither text nor null."""
    return [f'{who} {k} is not text; ignored' for k in keys if d.get(k) is not None and not isinstance(d[k], str)]


def _url_key(url):
    """Comparison key for two URLs: scheme and host case-folded, fragment and trailing slash dropped."""
    p = _parts(_clean(url))
    if not p:
        return _clean(url)
    port = None if p.port == {'https': 443, 'http': 80}.get(p.scheme.lower()) else p.port  # :443 is the https default
    return (p.scheme.lower(), (p.hostname or '').lower(), port, p.path.rstrip('/'), p.query)


def _ark_of(url):
    """The ARK parsed from the path of an image URL ('' when the path holds none)."""
    p = _parts(url)
    m = re.search(r'/(ark:/.*)$', p.path.rstrip('/')) if p else None
    return m.group(1) if m else ''


def merge_fields(cand, evidence):
    """(fields, problems): candidate values first; evidence fills gaps and a difference is reported, not resolved."""
    fields, problems = {}, []
    for k in LOCATORS:
        a, b = _clean(cand.get(k)), _clean(evidence.get(k))
        if a and b and a != b:
            problems.append(f'{k} differs between candidate and evidence; candidate kept')
        fields[k] = a or b
    return fields, problems


def _locator_syntax(fields):
    """Syntax problems of the image_url and ark fields, each prefixed by its field name."""
    out = []
    for key, check in (('image_url', url_problem), ('ark', ark_problem)):
        p = check(fields[key]) if fields[key] else None
        out += [f'{key}: {p}'] if p else []
    return out


def _embedded_ark_problems(fields):
    """Problems of an ARK carried in the path of image_url: different from the ark field, or malformed."""
    img = fields['image_url']
    if not (img and '/ark:/' in img):
        return []
    found = _ark_of(img)
    if fields['ark'] and found != fields['ark']:
        return ['image_url names a different ark than the ark field']
    if found and ark_problem(found):
        return ['image_url carries an invalid ark']
    return []


def locator_problems(fields, index_url=''):
    out = _locator_syntax(fields) + _embedded_ark_problems(fields)
    img = fields['image_url']
    if img and index_url and _url_key(img) == _url_key(index_url):
        out.append('image_url is the index url, not an image')
    return out


def index_problems(url):
    if not url:
        return ['index url missing']
    p = url_problem(url, INDEX_HOSTS)
    out = ['index url: ' + p] if p else []
    path = urllib.parse.urlsplit(url).path if _parts(url) else ''
    if not out and path.rstrip('/') in ('', '/buscar', '/buscar/resultados', '/buscar/bloque'):
        out.append('index url is a search page, not a permalink of the row')
    return out


def _valid_locator(fields, key, check):
    return bool(fields[key]) and check(fields[key]) is None


def image_located(fields, index_url=''):
    """True when a valid image URL or ARK is present together with a cote. Nothing is inferred.

    Any invalid or conflicting image locator (bad image_url, bad ark, differing ark, image_url equal to the index
    URL) means never image-located, even when the other locator is valid."""
    if locator_problems(fields, index_url):
        return False
    has_image = _valid_locator(fields, 'image_url', url_problem) or _valid_locator(fields, 'ark', ark_problem)
    return has_image and bool(fields['cote'])


NAME_BAD = re.compile(r'[\d?\[\]{}()<>_*#@/\\|]|\.\.|…|\bx{2,}\b', re.IGNORECASE)


def name_problem(value):
    """None for a complete-looking name; else the reason. Initials, gaps, '?' and ellipses are incomplete."""
    v = _clean(value)
    if not v:
        return 'is empty'
    if NAME_BAD.search(v) or v.endswith('.'):
        return 'is incomplete or uncertain'
    if any(len(w.strip("'-")) < 2 for w in v.split()):
        return 'is an initial or too short'
    return None


def crop_problem(crop):
    """None for a plausible crop reference (single-token relative name or https URL); else the reason."""
    if not isinstance(crop, str) or not crop.strip():
        return 'has no crop provenance'
    c = crop.strip()
    if re.search(r'[\s\x00-\x1f\x7f\\]', c):
        return 'has invalid crop provenance (whitespace, control or backslash)'
    if '://' in c or c.lower().startswith(('file:', 'data:', 'javascript:')):
        return None if url_problem(c) is None else 'has invalid crop provenance (not https)'
    if c.startswith(('/', '~')) or '..' in c.split('/'):
        return 'has invalid crop provenance (absolute or parent path)'
    return None


def reading_problems(i, r):
    """Reasons reading number i cannot count as an explicit reading with crop provenance."""
    if not isinstance(r, dict):
        return [f'reading {int(i)} is not an object']
    out = []
    if not isinstance(r.get('reader'), str) or not r['reader'].strip():
        out.append(f'reading {int(i)} has no reader')
    cp = crop_problem(r.get('crop'))
    out += [f'reading {int(i)} {cp}'] if cp else []
    for k in ('given', 'surname'):
        np_ = name_problem(r.get(k)) if isinstance(r.get(k), str) else 'is not text'
        out += [f'reading {int(i)} {k} {np_}'] if np_ else []
    if not isinstance(r.get('date_iso'), str):
        out.append(f'reading {int(i)} date_iso is not text')
    elif not _clean(r.get('date_iso')):
        out.append(f'reading {int(i)} lacks date_iso')
    elif not valid_iso(_clean(r['date_iso'])):
        out.append(f'reading {int(i)} date_iso is not YYYY-MM-DD')
    return out


def valid_iso(value):
    """True for a real calendar date written YYYY-MM-DD."""
    if not ISO.match(value):
        return False
    try:
        datetime.date.fromisoformat(value)
    except ValueError:
        return False
    return True


def _norm(value):
    return re.sub(r'\s+', ' ', _clean(value)).casefold()


def reading_conflicts(good):
    """Fields on which any two complete readings disagree (compared case-insensitively, accents kept)."""
    return [k for k in READ_FIELDS if len({_norm(r[k]) for r in good}) > 1]


def verify(readings):
    """(zoom, problems): zoom is the crop reference when two independent complete readings agree, else ''."""
    if readings is None or readings == []:
        return '', []
    if not isinstance(readings, list):
        return '', ['readings must be a list of readings']
    problems, good = [], []
    for i, r in enumerate(readings, 1):
        bad = reading_problems(i, r)
        problems += bad
        if not bad:
            good.append(r)
    readers = {_norm(r['reader']) for r in good}
    crops = {_norm(r['crop']) for r in good}
    if len(readers) < 2 or len(crops) < 2:
        return '', problems + ['needs two complete readings by two different readers on two different crops']
    clash = reading_conflicts(good)
    if clash:
        return '', problems + ['readings disagree on ' + ', '.join(clash)]
    if problems:  # an incomplete reading may be a disagreeing one: it blocks agreement instead of being dropped
        return '', problems + ['every reading must be complete and valid for readings-agree']
    return ' | '.join(_clean(r['crop']) for r in good), problems


def next_steps(stage, fields):
    steps = [f"locate the {k.replace('_', ' ')}" for k in LOCATORS if not fields[k]]
    if stage == 'indexed-lead':
        steps.append('find the image of the act: an index row is only a lead')
    if stage != 'readings-agree':
        steps.append('two independent zoom readings, each with its crop, for the human writer to review')
    steps.append('a human writer reviews the act and decides; this tool never promotes')
    return steps


def _first(cand, evidence, key):
    """The original value of key: candidate first, then evidence; '' when absent. Never inferred."""
    return _clean(cand.get(key)) or _clean(evidence.get(key))


def _field_conflict(k, cand, evidence, rs):
    """The conflict message for field k, or None when candidate, evidence and readings do not clash on it."""
    given = [v for v in (_clean(cand.get(k)), _clean(evidence.get(k))) if v]
    from_readings = {_norm(r.get(k)) for r in rs if _clean(r.get(k))}
    seen = {_norm(v) for v in given} | from_readings
    if len(given) == 2 and _norm(given[0]) != _norm(given[1]) or (given and len(seen) > 1):
        return f'conflict: {k} differs between candidate, evidence and readings'
    if not given and k == 'filiation' and len(from_readings) > 1:
        return 'conflict: filiation differs between readings'
    return None


def act_conflicts(cand, evidence, readings):
    """Explicit conflicts on date_iso / filiation between candidate, evidence and readings (never resolved)."""
    rs = [r for r in readings if isinstance(r, dict)]
    found = [_field_conflict(k, cand, evidence, rs) for k in ('date_iso', 'filiation')]
    return [c for c in found if c]


def blockers(date_iso, filiation, conflicts=()):
    """Evidence still missing before a human writer could confirm the act."""
    out = list(conflicts)
    if not date_iso:
        out.append('date_iso missing: no ISO date provided from the act')
    elif not valid_iso(date_iso):
        out.append('date_iso provided is not a valid YYYY-MM-DD date')
    if not filiation:
        out.append('filiation missing: no filiation provided from the act')
    out.append('human writer review pending: explicit authority not given to this tool')
    return out


def _checked_evidence(cand, evidence, n):
    """The evidence object (empty when None); raises ValueError for a bad candidate, evidence or unknown key."""
    if not isinstance(cand, dict):
        raise ValueError(f'candidate {int(n)} is not an object')
    evidence = {} if evidence is None else evidence
    if not isinstance(evidence, dict):
        raise ValueError(f'evidence for candidate {int(n)} is not an object')
    unknown = sorted(set(evidence) - set(EVIDENCE_KEYS))
    if unknown:
        raise ValueError(f"evidence for candidate {int(n)} has unknown keys: {', '.join(map(str, unknown))}")
    return evidence


def _stage_zoom(located, raw, conflicts):
    """(stage, zoom, problems) once the locators are judged; readings count only for an image-located act."""
    stage, zoom, out = 'indexed-lead', '', []
    if located:
        stage = 'image-located'
        zoom, out = verify(raw)
        stage = 'readings-agree' if zoom else stage
    elif raw not in (None, []):
        out = ['readings ignored: ' + ('conflicting date_iso/filiation' if conflicts else
                                       'no valid, unconflicting image URL or ARK with a cote')]
    return stage, zoom, out


def _readings_readers(raw):
    """(copy of the readings, reader name of each); both empty when raw is not a list."""
    readings = copy.deepcopy(raw) if isinstance(raw, list) else []
    return readings, [_clean(r.get('reader')) if isinstance(r, dict) else '' for r in readings]


def link(cand, evidence=None, n=1):
    """One review record for one candidate. A non-object candidate or evidence raises ValueError; odd field
    values (bool, numbers...) are ignored and listed in problems.

    The state is always `por-confirmar-acta`: agreeing readings are reported, never promoted.
    """
    evidence = _checked_evidence(cand, evidence, n)
    problems = _nontext(cand, 'candidate', TEXT_KEYS + ('id', 'name', 'date', 'url')) + _nontext(evidence, 'evidence', TEXT_KEYS)
    fields, more = merge_fields(cand, evidence)
    problems += more
    index_url = cand.get('url') if isinstance(cand.get('url'), str) else ''
    problems += index_problems(index_url) + locator_problems(fields, index_url)
    raw = evidence.get('readings')
    conflicts = act_conflicts(cand, evidence, raw if isinstance(raw, list) else [])
    clash = [p for p in more if p.startswith(('image_url differs', 'ark differs'))]
    problems += conflicts + (['image locator conflicts between candidate and evidence; not image-located'] if clash else [])
    stage, zoom, extra = _stage_zoom(image_located(fields, index_url) and not clash and not conflicts, raw, conflicts)
    problems += extra
    readings, readers = _readings_readers(raw)
    missing = [k for k in LOCATORS if not fields[k]]
    date_iso, filiation = _first(cand, evidence, 'date_iso'), _first(cand, evidence, 'filiation')
    return {'link_id': f'gal-{int(n):04}', 'candidate_id': _clean(cand.get('id')), 'stage': stage,
            'index_url': index_url, 'name_in_index': _clean(cand.get('name')), 'date_in_index': _clean(cand.get('date')),
            **fields, 'date_iso': date_iso, 'filiation': filiation, 'state': 'por-confirmar-acta',
            'zoom': zoom, 'readers': readers, 'readings': readings, 'missing': missing, 'blockers': blockers(date_iso, filiation, conflicts), 'problems': problems,
            'next_steps': next_steps(stage, fields)}


def build(candidates, evidence=None):
    """Review records for a list of candidates; evidence is {candidate id: {...}}.

    Raises ValueError for a non-list, a non-object candidate or evidence, an empty/non-text id or a duplicate id."""
    evidence = {} if evidence is None else evidence
    if not isinstance(evidence, dict) or not isinstance(candidates, list):
        raise ValueError('candidates must be a list and evidence an object keyed by candidate id')
    seen, out = set(), []
    for i, c in enumerate(candidates, 1):
        cid = c.get('id') if isinstance(c, dict) else None
        if not isinstance(cid, str) or not cid.strip():
            raise ValueError(f'candidate {int(i)} has an empty or non-text id')
        if cid.strip() in seen:
            raise ValueError(f'duplicate candidate id {cid.strip()!r} (candidate {int(i)})')
        seen.add(cid.strip())
        out.append(link(c, evidence.get(cid.strip()), i))
    return out


def to_tsv(records):
    out = io.StringIO()
    w = csv.writer(out, delimiter='\t', lineterminator='\n')
    w.writerow(COLUMNS)
    for r in records:
        w.writerow([cell('; '.join(x if isinstance(x, str) else json.dumps(x, ensure_ascii=False, sort_keys=True)
                                   for x in r[c]) if isinstance(r[c], list) else r[c]) for c in COLUMNS])
    return out.getvalue()


def load_candidates(data):
    """A list of candidates, or an object holding one under `candidates`."""
    if isinstance(data, dict):
        data = data.get('candidates')
    if not isinstance(data, list):
        raise ValueError('expected a JSON list of candidates, or {"candidates": [...]}')
    return data


def _no_dupes(pairs):
    """object_pairs_hook: a JSON object repeating a key is ambiguous, so it is rejected."""
    keys = [k for k, _ in pairs]
    dup = sorted({k for k in keys if keys.count(k) > 1})
    if dup:
        raise ValueError('duplicate JSON key(s): ' + ', '.join(dup))
    return dict(pairs)


def _read_json(path):
    with open(path, encoding='utf-8') as fh:  # skipcq: PTC-W6004 - a command line tool reads the file its user names
        return json.load(fh, object_pairs_hook=_no_dupes)


def _same(a, b):
    """True when two paths name the same file (same real path, or same inode when both exist)."""
    if os.path.realpath(a) == os.path.realpath(b):
        return True
    return os.path.exists(a) and os.path.exists(b) and os.path.samefile(a, b)


def _rollback(done, snaps):
    """Restore each moved output to its snapshot (delete it when it did not exist). Returns the paths that failed."""
    failed = []
    for dest in reversed(done):
        try:
            if snaps[dest] is None:
                os.unlink(dest)
            else:
                with open(dest, 'wb') as fh:
                    fh.write(snaps[dest])
        except OSError:
            failed.append(dest)
    return failed


def _write_all(pairs):
    """Write every (path, text) to a temp file first, then move each into place: no half-written output file.

    Existing outputs are snapshotted; if a move fails midway the earlier ones are rolled back, and the error says
    honestly whether the rollback worked. A directory or a repeated destination is rejected before anything moves."""
    dests = [os.path.realpath(p) for p, _ in pairs]
    if len(set(dests)) != len(dests) or any(os.path.isdir(d) for d in dests):
        raise OSError('an output path is a directory or is repeated; nothing written')
    snaps = {}
    for d in dests:
        if os.path.exists(d):
            with open(d, 'rb') as fh:
                snaps[d] = fh.read()
        else:
            snaps[d] = None
    tmps, done = [], []
    try:
        for (_path, text), dest in zip(pairs, dests):
            fd, tmp = tempfile.mkstemp(dir=os.path.dirname(dest), prefix='.tmp-genae-')
            tmps.append((tmp, dest))
            with os.fdopen(fd, 'w', encoding='utf-8', newline='') as fh:
                fh.write(text)
        for tmp, dest in tmps:
            os.replace(tmp, dest)
            done.append(dest)
    except OSError as e:
        failed = _rollback(done, snaps)
        if failed:
            raise OSError(f"{e}; ROLLBACK FAILED, these outputs may hold new content: {', '.join(failed)}") from e
        raise OSError(f"{e}; {'earlier outputs restored from snapshot' if done else 'no output changed'}") from e
    finally:
        for tmp, _ in tmps:
            try:
                os.unlink(tmp)
            except OSError:
                pass


def _parse_args(argv):
    ap = argparse.ArgumentParser(prog='gwt-genae-act-link', description=__doc__.split('\n')[0])
    ap.add_argument('candidates', help='JSON list of gwt_genae candidates')
    ap.add_argument('--evidence', help='JSON {candidate id: {parish, book, page, cote, ark, image_url, readings}}')
    ap.add_argument('--tsv', help='write the review TSV here (default: standard output)')
    ap.add_argument('--json', help='write the review JSON here')
    return ap.parse_args(argv)


def _path_error(a):
    """The error message when an output path clashes with an input, the other output or a directory; else None."""
    ins, outs = [a.candidates] + ([a.evidence] if a.evidence else []), [p for p in (a.tsv, a.json) if p]
    if any(_same(o, x) for o in outs for x in ins) or (len(outs) == 2 and _same(outs[0], outs[1])):
        return 'error: an output path is the same file as an input or as the other output'
    if any(os.path.isdir(o) for o in outs):
        return 'error: an output path is an existing directory'
    return None


def _generate(a):
    """Build the records, write the requested files and return the TSV text."""
    records = build(load_candidates(_read_json(a.candidates)), _read_json(a.evidence) if a.evidence else None)
    tsv = to_tsv(records)
    pairs = ([(a.tsv, tsv)] if a.tsv else []) + (
        [(a.json, json.dumps(records, ensure_ascii=False, indent=2) + '\n')] if a.json else [])
    _write_all(pairs)
    return tsv


def main(argv=None):
    a = _parse_args(argv)
    path_error = _path_error(a)
    if path_error:
        print(path_error, file=sys.stderr)
        return 2
    try:
        tsv = _generate(a)
    except (OSError, ValueError) as e:  # json.JSONDecodeError is a ValueError; nothing is written before this point
        print(f'error: {e}', file=sys.stderr)
        return 2
    if not a.tsv:
        sys.stdout.write(tsv)
    return 0


if __name__ == '__main__':
    sys.exit(main())
