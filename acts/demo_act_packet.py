#!/usr/bin/env python3
"""Demonstration runner for gwt_act_packet.py (all fixtures embedded below).

Runs six synthetic scenarios (normal act, uncertain reading, conflict, path block, rollback,
writer handoff) through the public functions of gwt_act_packet, inside one temporary directory
that is deleted on exit. Every assertion is an exact expected safety outcome. Standard library
only, no network. Prints one brief summary line when everything holds; exits 1 on any failure.
All people, readers, readings and "images" are invented; images are dummy bytes.
"""
import contextlib
import hashlib
import io
import json
import os
import sys
import tempfile

import gwt_act_packet as gap

CHECKS = 0
HEADER = gap.REQUIRED + gap.OPTIONAL
EVIDENCE = {
    'a.jpg': b'synthetic-image-bytes', 'a-crop.png': b'synthetic-crop-bytes',
    'b.jpg': b'synthetic-image-bytes-b', 'b-crop.png': b'synthetic-crop-bytes-b',
    'c.jpg': b'synthetic-image-bytes-c', 'c-crop.png': b'synthetic-crop-bytes-c',
    'd.jpg': b'synthetic-image-bytes-d', 'd-crop.png': b'synthetic-crop-bytes-d',
}
SIG_WARN = 'image_signature_unrecognized;crop_signature_unrecognized'
IGNORED = 'approval_ignored_conflict_or_uncertain'


def check(cond, what):
    global CHECKS
    CHECKS += 1
    if not cond:
        raise AssertionError(what)


def eq(got, want, what):
    check(got == want, f'{what}: expected {want!r}, got {got!r}')


def row(person, date, typ, n, r1, r2, review='', reviewer='', **over):
    d = {'person': person, 'date': date, 'type': typ, 'image': f'img/{n}.jpg',
         'crop': f'img/{n}-crop.png', 'reading_1': r1, 'reader_1': 'alice',
         'reading_2': r2, 'reader_2': 'bob', 'review': review, 'reviewer': reviewer}
    d.update(over)
    return d


def tsv(rows):
    return '\n'.join(['\t'.join(HEADER)] + ['\t'.join(r[h] for h in HEADER) for r in rows]) + '\n'


class Case:
    """Fresh evidence root plus input/outputs beside it (outputs must be outside the root)."""

    def __init__(self, base, name):
        self.dir = os.path.join(base, name)
        self.root = os.path.join(self.dir, 'root')
        os.makedirs(os.path.join(self.root, 'img'))
        for fn, data in EVIDENCE.items():
            with open(os.path.join(self.root, 'img', fn), 'wb') as f:
                f.write(data)
        self.inp = os.path.join(self.dir, 'in.tsv')
        self.out_tsv = os.path.join(self.dir, 'out.tsv')
        self.out_json = os.path.join(self.dir, 'out.json')

    def write_input(self, rows, path=None):
        path = path or self.inp
        with open(path, 'w', encoding='utf-8', newline='') as f:
            f.write(tsv(rows))
        return path

    def run(self, inp=None, out_tsv=None, out_json=None, root=None):
        argv = [inp or self.inp, '--root', root or self.root,
                '--out-tsv', out_tsv or self.out_tsv, '--out-json', out_json or self.out_json]
        o, e = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(o), contextlib.redirect_stderr(e):
            rc = gap.main(argv)
        return rc, o.getvalue(), e.getvalue()

    def manifest(self):
        with open(self.out_json, encoding='utf-8') as f:
            return json.load(f)

    def no_outputs(self, what):
        check(not os.path.exists(self.out_tsv) and not os.path.exists(self.out_json),
              f'{what}: no output may exist')

    def tmp_left(self, d=None):
        d = d or self.dir
        return [n for n in os.listdir(d) if n.startswith('.gwt-act-')]


def read_bytes(p):
    with open(p, 'rb') as f:
        return f.read()


def never_promoted(m, n):
    eq(m['mode'], 'writer_review_only', 'mode')
    eq(m['count'], n, 'count')
    eq(m['status_counts'], {'pending_review': n}, 'status_counts')
    eq((m['auto_promoted'], m['auto_verified']), (0, 0), 'auto counters')
    check(all(a['status'] == 'pending_review' for a in m['acts']), 'every act pending_review')


def sha(b):
    return hashlib.sha256(b).hexdigest()


# --- scenarios -------------------------------------------------------------------------

def normal_act(base):
    c = Case(base, 'normalact')
    c.write_input([row('I0001', '1850-03-02', 'baptism', 'a', 'Juan Prueba', 'Juan  prueba')])
    rc, out, _err = c.run()
    eq(rc, 0, 'normal exit')
    eq(out.strip(), '1 acts written for writer review only; none promoted or verified', 'message')
    m = c.manifest()
    never_promoted(m, 1)
    a = m['acts'][0]
    eq((a['agreement'], a['uncertain'], a['review'], a['reviewer']), ('agree', False, '', ''), 'state')
    eq(a['image_sha256'], sha(EVIDENCE['a.jpg']), 'image hash')
    eq(a['crop_sha256'], sha(EVIDENCE['a-crop.png']), 'crop hash')
    check(a['image_sha256'] != a['crop_sha256'], 'hashes differ')
    eq((a['reading_1'], a['reading_2']), ('Juan Prueba', 'Juan  prueba'), 'readings verbatim')
    eq(a['warnings'], SIG_WARN, 'warnings')
    eq(read_bytes(c.out_tsv).decode('utf-8').split('\n')[0], '\t'.join(gap.TSV_COLUMNS), 'tsv header')


def uncertain_reading(base):
    c = Case(base, 'uncertainreading')
    c.write_input([
        row('I0002', '1851-06', 'marriage', 'b', 'Maria Pr[?]eba', 'Maria Pr[?]eba'),
        row('I0003', '1852', 'burial', 'c', 'Pedro [illegible]', 'Pedro [illegible]', 'approved', 'carol'),
    ])
    eq(c.run()[0], 0, 'uncertain exit')
    m = c.manifest()
    never_promoted(m, 2)
    a, b = m['acts']
    for x in (a, b):
        eq((x['agreement'], x['uncertain']), ('agree', True), 'agree but uncertain')
    eq(a['reading_1'], 'Maria Pr[?]eba', 'mark kept')
    eq(b['reading_1'], 'Pedro [illegible]', 'mark kept')
    eq((b['review'], b['reviewer'], b['status']), ('approved', 'carol', 'pending_review'), 'approval only recorded')
    eq(b['warnings'], SIG_WARN + ';' + IGNORED, 'approval ignored warning')
    eq(a['warnings'], SIG_WARN, 'no approval warning without approval')


def conflict(base):
    c = Case(base, 'conflict')
    c.write_input([
        row('I0004', '1850-03-02', 'baptism', 'a', 'Juan Prueba', 'Joan Prueba'),
        row('I0005', '1853-01-15', 'birth', 'd', 'José', 'Jose'),
    ])
    eq(c.run()[0], 0, 'conflict exit')
    m = c.manifest()
    never_promoted(m, 2)
    a, b = m['acts']
    eq((a['agreement'], a['uncertain']), ('conflict', False), 'I0004')
    eq(b['agreement'], 'conflict', 'accents not folded')
    eq((a['reading_1'], a['reading_2'], b['reading_1'], b['reading_2']),
       ('Juan Prueba', 'Joan Prueba', 'José', 'Jose'), 'readings verbatim')
    eq(gap.compare('Calle Straße', 'CALLE  STRASSE'), 'agree', 'casefold agreement is loose')


def path_block(base):
    good = row('I0001', '1850-03-02', 'baptism', 'a', 'Juan Prueba', 'Juan Prueba')
    cases = [
        ('dotdot', {'image': '../x.jpg'}, 'must not contain ".."'),
        ('abs', {'image': '/etc/x.jpg'}, 'path must be relative'),
        ('url', {'image': 'http://example.invalid/a.jpg'}, 'path must be relative'),
        ('missing', {'image': 'img/missing.jpg'}, 'file missing'),
        ('ext', {'image': 'img/notes.txt'}, 'no image extension'),
        ('samefile', {'crop': 'img/a.jpg'}, 'crop must be a different file'),
        ('samereader', {'reader_2': 'ALICE'}, 'not independent'),
    ]
    for name, over, reason in cases:
        c = Case(base, 'pb-' + name)
        c.write_input([dict(good, **over)])
        rc, out, err = c.run()
        eq(rc, 1, name + ' exit')
        check(reason in err, f'{name}: stderr names the reason {reason!r}')
        check('rejected: nothing written' in err, name + ': nothing written message')
        c.no_outputs(name)
    # symlink escaping the root (skipped where symlinks are unavailable)
    c = Case(base, 'pb-symlink')
    outside = os.path.join(c.dir, 'outside.jpg')
    with open(outside, 'wb') as f:
        f.write(b'synthetic-outside-bytes')
    try:
        os.symlink(outside, os.path.join(c.root, 'img', 'link.jpg'))
    except (OSError, NotImplementedError, AttributeError):
        pass
    else:
        c.write_input([dict(good, image='img/link.jpg')])
        rc, out, err = c.run()
        eq(rc, 1, 'symlink exit')
        check('escapes the evidence root' in err, 'symlink reason')
        c.no_outputs('symlink')
    # one bad row blocks the whole input
    c = Case(base, 'pb-whole')
    c.write_input([good, row('I0002', '1851-06', 'marriage', 'b', 'x', 'x', image='img/missing.jpg')])
    rc, out, err = c.run()
    eq(rc, 1, 'whole-input exit')
    check('line 3:' in err and 'line 2:' not in err, 'only the bad row is reported')
    c.no_outputs('whole input')
    # unsafe output paths: exit 2, input and evidence untouched
    c = Case(base, 'pb-outputs')
    c.write_input([good])
    before_in = read_bytes(c.inp)
    before_ev = {n: read_bytes(os.path.join(c.root, 'img', n)) for n in EVIDENCE}
    os.mkdir(os.path.join(c.dir, 'outdir'))
    inside = os.path.join(c.root, 'img', 'out.tsv')
    bad = [
        ('tsv=input', dict(out_tsv=c.inp), 'would overwrite the input'),
        ('json=input', dict(out_json=c.inp), 'would overwrite the input'),
        ('tsv=json', dict(out_json=c.out_tsv), 'are the same file'),
        ('inside root', dict(out_tsv=inside), 'inside the evidence root'),
        ('directory', dict(out_tsv=os.path.join(c.dir, 'outdir')), 'is a directory'),
        ('no parent', dict(out_json=os.path.join(c.dir, 'nope', 'o.json')), 'parent directory does not exist'),
    ]
    for name, kw, reason in bad:
        rc, out, err = c.run(**kw)
        eq(rc, 2, name + ' exit')
        check(reason in err, f'{name}: stderr names the reason {reason!r}')
        eq(out, '', name + ' prints no success message')
    c.no_outputs('output blocks')
    eq(read_bytes(c.inp), before_in, 'input unchanged')
    check(not os.path.exists(inside), 'nothing written inside root')
    eq({n: read_bytes(os.path.join(c.root, 'img', n)) for n in EVIDENCE}, before_ev, 'evidence unchanged')
    eq(c.tmp_left(), [], 'no temp files')


def _fail_second_replace():
    real, state = os.replace, {'n': 0}

    def fake(src, dst):
        state['n'] += 1
        if state['n'] == 2:
            raise OSError('injected replace failure')
        return real(src, dst)
    return real, fake


def rollback(base):
    # 1. second swap fails, previous outputs existed: both byte-identical to before
    c = Case(base, 'rb-prev')
    c.write_input([row('I0001', '1850-03-02', 'baptism', 'a', 'Juan Prueba', 'Juan  prueba')])
    eq(c.run()[0], 0, 'first run')
    t0, j0 = read_bytes(c.out_tsv), read_bytes(c.out_json)
    in2 = c.write_input([row('I0009', '1850-03-02', 'baptism', 'a', 'Juan Prueba', 'Juan Prueba')],
                        os.path.join(c.dir, 'in2.tsv'))
    real, fake = _fail_second_replace()
    os.replace = fake
    try:
        rc, _out, err = c.run(inp=in2)
    finally:
        os.replace = real
    eq(rc, 2, 'rollback 1 exit')
    check('outputs left as before' in err, 'rollback 1 message')
    eq((read_bytes(c.out_tsv), read_bytes(c.out_json)), (t0, j0), 'outputs identical to before')
    eq(c.tmp_left(), [], 'rollback 1 no temp files')

    # 2. second swap fails, no previous outputs: neither exists
    c = Case(base, 'rb-fresh')
    c.write_input([row('I0001', '1850-03-02', 'baptism', 'a', 'Juan Prueba', 'Juan Prueba')])
    real, fake = _fail_second_replace()
    os.replace = fake
    try:
        rc, _out, err = c.run()
    finally:
        os.replace = real
    eq(rc, 2, 'rollback 2 exit')
    check('outputs left as before' in err, 'rollback 2 message')
    c.no_outputs('rollback 2')
    eq(c.tmp_left(), [], 'rollback 2 no temp files')

    # 3. restore itself fails: must not claim a clean state
    c = Case(base, 'rb-broken')
    c.write_input([row('I0001', '1850-03-02', 'baptism', 'a', 'Juan Prueba', 'Juan Prueba')])
    real, fake = _fail_second_replace()
    real_restore = gap._restore

    def broken_restore(path, old):
        raise OSError('injected restore failure')
    os.replace = fake
    gap._restore = broken_restore
    try:
        rc, _out, err = c.run()
    finally:
        os.replace = real
        gap._restore = real_restore
    eq(rc, 2, 'rollback 3 exit')
    check('rollback incomplete' in err, 'rollback 3 says incomplete')
    check('injected restore failure' in err, 'rollback 3 carries the restore error')
    check('left as before' not in err, 'rollback 3 does not claim a clean state')
    eq(c.tmp_left(), [], 'rollback 3 no temp files')


def writer_handoff(base):
    c = Case(base, 'writerhandoff')
    rows = [
        row('I0010', '1850-03-02', 'baptism', 'a', 'Juan Prueba', 'Juan Prueba', 'approved', 'carol'),
        row('I0011', '1851-06', 'marriage', 'b', 'Ana Ejemplo', 'Ana Ejemplo', 'rejected', 'carol'),
        row('I0012', '1852', 'death', 'c', '=1+1', '=1+1'),
        row('I0013', '1853-01-15', 'burial', 'd', 'Luis Muestra', 'Luis Muestra'),
    ]
    c.write_input(rows)
    eq(c.run()[0], 0, 'handoff exit')
    m = c.manifest()
    never_promoted(m, 4)
    acts = {a['person']: a for a in m['acts']}
    eq((acts['I0010']['review'], acts['I0010']['status']), ('approved', 'pending_review'), 'approved only recorded')
    eq((acts['I0011']['review'], acts['I0011']['status']), ('rejected', 'pending_review'), 'rejected only recorded')
    eq(acts['I0012']['reading_1'], '=1+1', 'json keeps formula verbatim')
    text = read_bytes(c.out_tsv).decode('utf-8')
    lines = [ln.split('\t') for ln in text.rstrip('\n').split('\n')]
    eq(lines[0], gap.TSV_COLUMNS, 'tsv header')
    i = gap.TSV_COLUMNS.index('reading_1')
    eq(lines[3][i], "'=1+1", 'formula neutralised in tsv')
    check(all(not cell.startswith(gap.FORMULA_START) for ln in lines for cell in ln),
          'no tsv cell starts with = + - @')
    check('promotable' not in text and 'verified' not in text, 'no promotion vocabulary in tsv')
    # review metadata errors: exit 1, nothing written
    for name, over in (('reviewer is a reader', {'review': 'approved', 'reviewer': 'alice'}),
                       ('bad review value', {'review': 'maybe', 'reviewer': 'carol'}),
                       ('reviewer without review', {'review': '', 'reviewer': 'carol'})):
        e = Case(base, 'wh-' + name.replace(' ', '-'))
        e.write_input([dict(rows[3], **over)])
        rc, _out, err = e.run()
        eq(rc, 1, name + ' exit')
        check('rejected: nothing written' in err, name + ' message')
        e.no_outputs(name)


def main():
    scenarios = [normal_act, uncertain_reading, conflict, path_block, rollback, writer_handoff]
    with tempfile.TemporaryDirectory(prefix='gwt-act-demo-') as base:
        try:
            for s in scenarios:
                s(base)
        except AssertionError as e:
            print(f'demo FAILED in {s.__name__}: {e}', file=sys.stderr)
            return 1
    print(f'demo_act_packet OK: {len(scenarios)} scenarios, {CHECKS} checks; '
          'all synthetic, nothing promoted or verified, nothing persisted')
    return 0


if __name__ == '__main__':
    sys.exit(main())
