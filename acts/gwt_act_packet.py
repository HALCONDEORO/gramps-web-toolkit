#!/usr/bin/env python3
"""gwt-act-packet: offline evidence packet for act (record) readings.

Reads a TSV of acts, each with an image, a crop and two independent human or
tool-supplied readings, and writes a writer-review TSV plus a JSON manifest.
It performs no OCR/HTR, calls no model or API, and uses no network. It never
promotes or verifies anything: every row leaves as pending_review, even when
the input carries approval metadata, which is only recorded. It does not check
image content; file signatures are reported as hints, not verification.
"""
import argparse
from pathlib import Path
import csv
import datetime
import hashlib
import json
import os
import re
import sys
import tempfile
import unicodedata

REQUIRED = ['person', 'date', 'type', 'image', 'crop',
            'reading_1', 'reader_1', 'reading_2', 'reader_2']
OPTIONAL = ['review', 'reviewer']
TYPES = {'birth', 'baptism', 'marriage', 'death', 'burial'}
PERSON_RE = re.compile(r'[A-Za-z][A-Za-z0-9_-]{0,31}')
DATE_RE = re.compile(r'([0-9]{4})(?:-([0-9]{2})(?:-([0-9]{2}))?)?')  # ASCII digits only; use fullmatch
READER_RE = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}')
# Matched after NFKC, so fullwidth '？' becomes '?' and '…' becomes '...'.
UNCERTAIN_RE = re.compile(r'\?|\.\.\.|…|？|unclear|illegible|ilegible', re.I)
# Rejected in every field: ASCII and C1 controls (tab and newline cannot occur in a split TSV
# cell), Unicode line/paragraph separators, and bidirectional controls that can reorder text
# on screen (ALM, LRM, RLM, embeddings/overrides, isolates).
CTRL_RE = re.compile('[\x00-\x08\x0b-\x1f\x7f-\x9f؜‎‏  '
                     '‪-‮⁦-⁩]')
IMAGE_EXT = {'.jpg', '.jpeg', '.png', '.tif', '.tiff'}

STATUS = 'pending_review'  # the only status this tool ever emits
FORMULA_START = ('=', '+', '-', '@')

TSV_COLUMNS = ['person', 'date', 'type', 'image', 'image_sha256', 'image_signature',
               'crop', 'crop_sha256', 'crop_signature',
               'reading_1', 'reader_1', 'reading_2', 'reader_2', 'uncertain',
               'agreement', 'review', 'reviewer', 'warnings', 'status']


class PacketError(Exception):
    pass


class RollbackError(OSError):
    """A write failed and at least one output could not be put back as it was."""


def _sig(head):
    """Name the container signature in the first bytes. This is not image verification."""
    if head.startswith(b'\xff\xd8\xff'):
        return 'jpeg-signature'
    if head.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'png-signature'
    if head[:4] in (b'II*\x00', b'MM\x00*'):
        return 'tiff-signature'
    return 'unrecognized'


def file_facts(path):
    """Return (signature, sha256) computed from one single read of the file's bytes."""
    h = hashlib.sha256()
    sig, size = None, 0
    with Path(path).open('rb') as f:
        for chunk in iter(lambda: f.read(65536), b''):
            if sig is None:
                sig = _sig(chunk[:8])
            size += len(chunk)
            h.update(chunk)
    if not size:
        raise PacketError(f'evidence file is empty: {os.path.basename(path)!r}')
    return sig, h.hexdigest()


def _valid_date(s):
    """True for a real calendar date written with ASCII digits: YYYY, YYYY-MM or YYYY-MM-DD."""
    m = DATE_RE.fullmatch(s)
    if not m:
        return False
    try:
        datetime.date(int(m.group(1)), int(m.group(2) or 1), int(m.group(3) or 1))
    except ValueError:
        return False
    return True


def resolve_evidence(root, rel, label):
    """Return the real path of a relative evidence file strictly inside root."""
    if not rel:
        raise PacketError(f'missing {label} path')
    if os.path.isabs(rel) or '\\' in rel or re.match(r'^[A-Za-z][A-Za-z0-9+.-]*:', rel):
        raise PacketError(f'{label} path must be relative: {rel!r}')
    if '..' in rel.split('/'):
        raise PacketError(f'{label} path must not contain "..": {rel!r}')
    if os.path.splitext(rel)[1].lower() not in IMAGE_EXT:
        raise PacketError(f'{label} path has no image extension: {rel!r}')
    real_root = os.path.realpath(root)
    real = os.path.realpath(os.path.join(real_root, rel))
    if os.path.commonpath([real_root, real]) != real_root:
        raise PacketError(f'{label} path escapes the evidence root: {rel!r}')
    if not os.path.isfile(real):
        raise PacketError(f'{label} file missing: {rel!r}')
    if os.path.getsize(real) == 0:
        raise PacketError(f'{label} file is empty: {rel!r}')
    return real


def normalize(text):
    """Comparison key for agreement. NOT an exact match: NFC, full Unicode casefold
    (so 'Straße' == 'STRASSE') and collapsed whitespace. Differences in spelling,
    accents or punctuation still count as a conflict. Stored readings stay verbatim."""
    return ' '.join(unicodedata.normalize('NFC', text).casefold().split())


def is_uncertain(text):
    return bool(UNCERTAIN_RE.search(text) or
                UNCERTAIN_RE.search(unicodedata.normalize('NFKC', text)))


def compare(r1, r2):
    return 'agree' if normalize(r1) == normalize(r2) else 'conflict'


def _check_fields(row):
    """Required fields are present and no field holds a control character."""
    for k in REQUIRED:
        v = row.get(k)
        if v is None or not v.strip():
            raise PacketError(f'missing field {k}')
    for k, v in row.items():
        if v and CTRL_RE.search(v):
            raise PacketError(f'control, bidirectional or line-separator character in {k}')


def _check_identity(row):
    """Validate person, date and type; return them normalised."""
    person, date, typ = row['person'].strip(), row['date'].strip(), row['type'].strip().lower()
    if not PERSON_RE.fullmatch(person):
        raise PacketError(f'bad person id {person!r}')
    if not _valid_date(date):
        raise PacketError(f'bad date {date!r} (want a real YYYY, YYYY-MM or YYYY-MM-DD)')
    if typ not in TYPES:
        raise PacketError(f'bad type {typ!r}')
    return person, date, typ


def _check_readers(row):
    """Validate both reader ids and their independence; return them."""
    r1, r2 = row['reader_1'].strip(), row['reader_2'].strip()
    for r in (r1, r2):
        if not READER_RE.fullmatch(r):
            raise PacketError(f'bad reader id {r!r}')
    if r1.casefold() == r2.casefold():
        raise PacketError(f'second reading is not independent (same reader {r1!r})')
    return r1, r2


def _evidence_facts(img, crop):
    """Return (img_sig, crop_sig, img_hash, crop_hash) for two resolved files."""
    if img == crop:
        raise PacketError('crop must be a different file from the image')
    (img_sig, img_hash), (crop_sig, crop_hash) = file_facts(img), file_facts(crop)
    return img_sig, crop_sig, img_hash, crop_hash


def check_row(row, root, seen=None):
    """Validate one row; return the packet record. Raises PacketError.

    If seen is a set, the act is also checked (by canonical evidence paths) against it.
    """
    _check_fields(row)
    person, date, typ = _check_identity(row)
    r1, r2 = _check_readers(row)
    # Order kept: evidence is resolved before review is validated (first error wins).
    img = resolve_evidence(root, row['image'].strip(), 'image')
    crop = resolve_evidence(root, row['crop'].strip(), 'crop')
    if seen is not None:
        key = (person, typ, date, img, crop)
        if key in seen:
            raise PacketError('duplicate act row (same canonical evidence)')
        seen.add(key)
    # Readings are kept verbatim (only outer whitespace trimmed); uncertainty marks stay.
    reading_1, reading_2 = row['reading_1'].strip(), row['reading_2'].strip()
    agreement = compare(reading_1, reading_2)
    uncertain = is_uncertain(reading_1) or is_uncertain(reading_2)
    review, reviewer = check_review(row, r1, r2)
    img_sig, crop_sig, img_hash, crop_hash = _evidence_facts(img, crop)
    warnings = evidence_warnings(img_sig, crop_sig, img_hash, crop_hash)
    if review == 'approved' and (agreement != 'agree' or uncertain):
        warnings.append('approval_ignored_conflict_or_uncertain')
    return {
        'person': person, 'date': date, 'type': typ,
        'image': row['image'].strip(), 'image_sha256': img_hash, 'image_signature': img_sig,
        'crop': row['crop'].strip(), 'crop_sha256': crop_hash, 'crop_signature': crop_sig,
        'reading_1': reading_1, 'reader_1': r1, 'reading_2': reading_2, 'reader_2': r2,
        'uncertain': uncertain, 'agreement': agreement,
        'review': review, 'reviewer': reviewer, 'warnings': ';'.join(warnings),
        'status': STATUS,
    }


def check_review(row, r1, r2):
    """Validate review metadata. It is recorded only; it never changes the status."""
    review = (row.get('review') or '').strip().lower()
    reviewer = (row.get('reviewer') or '').strip()
    if review not in ('', 'approved', 'rejected'):
        raise PacketError(f'bad review value {review!r} (want approved, rejected or empty)')
    if reviewer and not review:
        raise PacketError('reviewer given without a review value')
    if not review:
        return '', ''
    if not READER_RE.fullmatch(reviewer):
        raise PacketError('review given without a valid reviewer id')
    if reviewer.casefold() in (r1.casefold(), r2.casefold()):
        raise PacketError('reviewer must differ from both readers')
    return review, reviewer


def evidence_warnings(img_sig, crop_sig, img_hash, crop_hash):
    out = []
    if img_sig == 'unrecognized':
        out.append('image_signature_unrecognized')
    if crop_sig == 'unrecognized':
        out.append('crop_signature_unrecognized')
    if img_hash == crop_hash:
        out.append('crop_bytes_identical_to_image')
    return out


def _read_rows(path):
    """Return the non-blank TSV rows (header first)."""
    try:
        with Path(path).open(encoding='utf-8-sig', newline='') as f:  # a leading BOM is accepted
            rd = csv.reader(f, delimiter='\t', quoting=csv.QUOTE_NONE)
            rows = [r for r in rd if r and any(c.strip() for c in r)]
    except csv.Error as e:
        raise PacketError(f'unreadable TSV: {e}')
    if not rows:
        raise PacketError('input is empty')
    return rows


def _check_header(raw):
    header = [h.strip() for h in raw]
    missing = [c for c in REQUIRED if c not in header]
    if missing:
        raise PacketError('missing columns: ' + ', '.join(missing))
    unknown = [c for c in header if c not in REQUIRED + OPTIONAL]
    if unknown:
        raise PacketError('unknown columns: ' + ', '.join(unknown))
    if len(set(header)) != len(header):
        raise PacketError('duplicate columns')
    return header


def read_input(path):
    rows = _read_rows(path)
    header = _check_header(rows[0])
    out = []
    for i, r in enumerate(rows[1:], start=2):
        if len(r) > len(header):
            raise PacketError(f'line {int(i)}: too many fields')
        r = r + [''] * (len(header) - len(r))
        out.append((i, dict(zip(header, r))))
    if not out:
        raise PacketError('input has no data rows')
    return out


def build(input_path, root):
    """Return (records, errors). Any error means nothing should be written."""
    records, errors, seen = [], [], set()
    for line, row in read_input(input_path):
        try:
            records.append(check_row(row, root, seen))
        except PacketError as e:
            errors.append(f'line {int(line)}: {e}')
    return records, errors


def _tsv_cell(v):
    s = str(v).replace('\t', ' ').replace('\n', ' ').replace('\r', ' ')
    if s.lstrip().startswith(FORMULA_START):
        s = "'" + s  # neutralise spreadsheet formulas; the JSON manifest stays verbatim
    return s


def check_output_paths(input_path, root, tsv_path, json_path):
    """Refuse outputs that would overwrite the input, evidence, or each other.

    Returns the canonical (tsv, json) paths, which are the ones to write.
    """
    real_in = os.path.realpath(input_path)
    real_root = os.path.realpath(root)
    outs = {}
    for label, p in (('--out-tsv', tsv_path), ('--out-json', json_path)):
        real = os.path.realpath(p)
        if real == real_in or (os.path.exists(real) and os.path.exists(real_in)
                               and os.path.samefile(real, real_in)):
            raise PacketError(f'{label} would overwrite the input file')
        if os.path.commonpath([real_root, real]) == real_root:
            raise PacketError(f'{label} must not be inside the evidence root')
        if os.path.isdir(real):
            raise PacketError(f'{label} is a directory')
        if not os.path.isdir(os.path.dirname(real)):
            raise PacketError(f'{label}: parent directory does not exist')
        outs[label] = real
    a, b = outs['--out-tsv'], outs['--out-json']
    if a == b or (os.path.exists(a) and os.path.exists(b) and os.path.samefile(a, b)):
        raise PacketError('--out-tsv and --out-json are the same file')
    return a, b


def render_tsv(records):
    lines = ['\t'.join(TSV_COLUMNS)]
    for r in records:
        lines.append('\t'.join(_tsv_cell('true' if r[c] is True else 'false' if r[c] is False else r[c])
                               for c in TSV_COLUMNS))
    return '\n'.join(lines) + '\n'


def render_json(records):
    counts = {}
    for r in records:
        counts[r['status']] = counts.get(r['status'], 0) + 1
    manifest = {'version': 2, 'mode': 'writer_review_only', 'count': len(records),
                'status_counts': counts, 'auto_promoted': 0, 'auto_verified': 0,
                'acts': records}
    return json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + '\n'


def _stage(path, text):
    """Write text to a fresh, uniquely named temp file beside path; return its name."""
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(os.path.realpath(path)),
                               prefix='.gwt-act-', suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='') as f:
            f.write(text)
    except BaseException:
        _discard(tmp)
        raise
    return tmp


def _discard(path):
    try:
        os.unlink(path)
    except OSError:
        pass


def _snapshot(path):
    """Return the current bytes of path, or None if it does not exist."""
    try:
        with Path(path).open('rb') as f:
            return f.read()
    except FileNotFoundError:
        return None


def _restore(path, old):
    """Put path back to old (None = remove it). Raises OSError if that fails."""
    if old is None:
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
        return
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(os.path.realpath(path)),
                               prefix='.gwt-act-', suffix='.tmp')
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(old)
        os.replace(tmp, path)
    finally:
        _discard(tmp)  # no-op after a successful replace


def write_outputs(records, tsv_path, json_path):
    """Write both outputs or neither: stage both, then swap, rolling back on failure.

    Raises RollbackError if a rollback step itself failed (outputs may then differ from
    before); any other OSError means the previous state was restored.
    """
    tmps = []
    try:
        tmps.append(_stage(tsv_path, render_tsv(records)))
        tmps.append(_stage(json_path, render_json(records)))
        olds = [(tsv_path, _snapshot(tsv_path)), (json_path, _snapshot(json_path))]
        done = []
        try:
            for tmp, dst in zip(tmps, (tsv_path, json_path)):
                done.append(dst)  # counted before the call: a failed replace is restored too
                os.replace(tmp, dst)
        except BaseException as exc:
            failed = []
            for dst, old in olds:
                if dst in done:
                    try:
                        _restore(dst, old)
                    except OSError as e:
                        failed.append(f'{dst} ({e})')
            if failed and isinstance(exc, Exception):
                raise RollbackError('rollback failed for: ' + '; '.join(failed)) from exc
            raise
    finally:
        for t in tmps:
            _discard(t)


def main(argv=None):
    ap = argparse.ArgumentParser(prog='gwt-act-packet', description=__doc__.split('\n')[0])
    ap.add_argument('input', help='input TSV')
    ap.add_argument('--root', required=True, help='directory holding the image and crop files')
    ap.add_argument('--out-tsv', required=True, help='writer-review TSV to write')
    ap.add_argument('--out-json', required=True, help='JSON manifest to write')
    a = ap.parse_args(argv)
    try:
        out_tsv, out_json = check_output_paths(a.input, a.root, a.out_tsv, a.out_json)
        records, errors = build(a.input, a.root)
    except (PacketError, OSError, UnicodeDecodeError) as e:
        print(f'error: {e}', file=sys.stderr)
        return 2
    if errors:
        for e in errors:
            print('error: ' + e, file=sys.stderr)
        print('rejected: nothing written', file=sys.stderr)
        return 1
    try:
        write_outputs(records, out_tsv, out_json)
    except RollbackError as e:
        print(f'error: write failed and rollback incomplete; outputs may be inconsistent: {e}',
              file=sys.stderr)
        return 2
    except OSError as e:
        print(f'error: write failed, outputs left as before: {e}', file=sys.stderr)
        return 2
    print(f'{len(records)} acts written for writer review only; none promoted or verified')
    return 0


if __name__ == '__main__':
    sys.exit(main())
