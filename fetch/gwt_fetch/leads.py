"""Local, fixed-pattern collateral clues. A human must validate every literal."""
from __future__ import annotations

import csv
import datetime
import json
import io
import os
import re
import sys
from collections import Counter
from pathlib import Path

from .names import fold
from .touraine import clean_cell

COLUMNS = ['ref', 'date', 'name', 'role', 'declared_relation', 'confidence', 'literal']
# Deliberately bounded grammar: capitalised given name(s) and surname, optional particles.
WORD = r"[A-ZÀ-ÖØ-Þ][a-zA-ZÀ-ÖØ-öø-ÿ]*(?:[-'’][A-Za-zÀ-ÖØ-öø-ÿ]+)*"
NAME = re.compile(rf"(?<![\w'’])(?:[=+@-])?{WORD}(?:\s*\(\?\))?(?:[ \t]+(?:(?:de|du|des|d['’])[ \t]*)?{WORD}(?:\s*\(\?\))?)+")
RELATION = r"(?:\s+de\s+(?:l['’](?:epouse|épouse|epoux|époux)|la\s+mariee|la\s+mariée|lui|elle))?"
MARKER = re.compile(
    r"(?<!\w)(?:(?P<dead>(?:fils|fille)\s+de\s+feu[e]?)\b|"
    r"(?P<parent>(?:fils|fille)\s+de)\b|"
    r"(?P<god>parrain|marraine)\b|"
    r"(?P<witness>t[eé]moins?|pr[eé]sents?|pr[eé]sence\s+de|amis)\b" + RELATION + r"|"
    r"(?P<sibling>fr[eè]re)\b" + RELATION + r"|"
    r"(?P<grand>grand[- ](?:p[eè]re|m[eè]re))\b" + RELATION + r"|"
    r"(?P<spouse>veuf\s+de|veuve\s+de)\b|"
    r"(?P<other>oncle|cousin|feue?)\b" + RELATION + r")", re.I)
ROLES = {'dead': 'deceased_parent', 'parent': 'parent', 'god': 'godparent',
         'witness': 'witness', 'sibling': 'sibling', 'grand': 'grandparent',
         'spouse': 'spouse', 'other': 'other'}
PRINCIPAL = re.compile(r"(?:mariage|bapt[eê]me|bapt[iê]s?e|enterrement|inhumation)\s+de\s*$", re.I)
FRIENDS = re.compile(r"\s+amis\s+de\s+l['’][eé]pou(?:x|se)\b", re.I)


# Conservative clause-level guards: a hit anywhere in the clause withdraws the role.
DOUBT = re.compile(
    r"(?<!\w)n['’]|"  # elided negation: n'est, n'a
    r"(?<!\w)(?:ne|pas|jamais|sans|non|ni|aucune?|"  # negation
    r"probabl\w*|peut[- ]?[eê]tre|vraisemblabl\w*|possibl\w*|suppos\w*|semble\w*|"
    r"incertain\w*|douteu\w*|"  # uncertainty
    r"ou|soit)(?!\w)|\?", re.I)  # alternatives


def _doubtful(clause):
    return bool(DOUBT.search(clause.replace('(?)', ' ')))


def _blocks(text):
    if not text.strip():
        raise ValueError('empty transcription')
    blocks = []
    for block in re.split(r'^\s*---\s*$', text, flags=re.M):
        lines = block.strip().splitlines(keepends=True)
        headers = {}
        while lines and re.match(r'^(ref|date):', lines[0]):
            key, value = lines.pop(0).split(':', 1)
            if key in headers:
                raise ValueError(f'duplicate {key} header')
            headers[key] = value.strip()
        if not headers.get('ref') or '/' not in headers['ref']:
            raise ValueError('each act needs ref: <cote>/<image>')
        date = headers.get('date', '')
        if not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', date):
            raise ValueError('each act needs date: YYYY-MM-DD')
        datetime.date.fromisoformat(date)
        blocks.append((headers, ''.join(lines)))
    return blocks


PARENT_ROLES = ('parent', 'deceased_parent')
GAP_BETWEEN_NAMES = re.compile(r'\s*(?:,\s*)?(?:et\s+(?:de\s+)?)?')


def _marker_kind(marker):
    # Explicit resolution: a MARKER match always sets exactly one ROLES group.
    # Anything else is a grammar/table mismatch and must fail safely, not guess a role.
    kinds = [k for k in ROLES if marker.group(k) is not None]
    if not kinds:
        raise ValueError('leads marker matched without a known role group')
    return kinds[0]


def _same_name(name, subject):
    return bool(subject) and fold(name) == fold(subject)


def _is_principal(left, name, subject):
    return bool(PRINCIPAL.search(left) or (_same_name(name, subject) and not MARKER.search(left)))


def _advance_role(left, previous_end, active, chain):
    """Return (active role, subject-parent chain) after the text between two names."""
    markers = list(MARKER.finditer(left))
    if markers:
        marker = markers[-1]
        return (ROLES[_marker_kind(marker)], marker.group(), previous_end + marker.start()), chain
    if not GAP_BETWEEN_NAMES.fullmatch(left):
        return None, False
    return active, chain


def _shared_relation(body, role, relation, end):
    # A collective trailing declaration belongs to each listed witness.
    if role != 'witness':
        return relation, end
    tail = re.split(r'[.;]', body[end:], maxsplit=1)[0]
    friends = FRIENDS.search(tail)
    if friends:
        return friends.group().strip(), end + friends.end()
    return relation, end


def _confidence(left, name, role):
    return 'low' if '(?)' in left + name or role == 'other' else 'high'


def _clause(body, name_start, end):
    clause_start = max(body.rfind('.', 0, name_start), body.rfind(';', 0, name_start)) + 1
    clause_end = len(body)
    stop = re.search(r'[.;]', body[end:])
    if stop:
        clause_end = end + stop.start()
    return body[clause_start:clause_end]


def _withdraw_if_doubtful(body, name_start, end, role, confidence, literal):
    # Negated, doubtful or alternative wording anywhere in the clause
    # means the role is not declared: keep the whole clause as evidence.
    clause = _clause(body, name_start, end)
    if _doubtful(clause):
        role, confidence = 'other', 'low'
        literal = clause.strip()
    return role, confidence, literal


def _evidence_row(headers, body, found, left, declared, end):
    role, relation, start = declared
    name = found.group()
    literal = body[start:end]
    confidence = _confidence(left, name, role)
    role, confidence, literal = _withdraw_if_doubtful(body, found.start(), end, role, confidence, literal)
    return dict(ref=headers['ref'], date=headers['date'], name=name, role=role,
                declared_relation=relation, confidence=confidence,
                literal=literal)


def _extract_block(headers, body, subject):
    rows = []
    previous_end = 0
    active = None
    chain = False
    for found in NAME.finditer(body):
        name = found.group()
        left = body[previous_end:found.start()]
        if re.search(r'[.;]', left):
            active, chain = None, False
        if _is_principal(left, name, subject):
            active, chain = None, _same_name(name, subject)
            previous_end = found.end()
            continue
        active, chain = _advance_role(left, previous_end, active, chain)
        role, relation, start = active or ('other', '', found.start())
        relation, end = _shared_relation(body, role, relation, found.end())
        excluded_parent = chain and role in PARENT_ROLES
        if role not in PARENT_ROLES:
            chain = False
        if not excluded_parent and not _same_name(name, subject):
            rows.append(_evidence_row(headers, body, found, left, (role, relation, start), end))
        previous_end = found.end()
    return rows


def extract(text, subject=None):
    rows = []
    for headers, body in _blocks(text):
        rows.extend(_extract_block(headers, body, subject))
    return rows


def group_names(rows):
    return [{'name': name, 'count': count} for name, count in Counter(r['name'] for r in rows).items()]


def match_names(rows, catalogue):
    # Variants are explicit, pipe-separated; no fuzzy identity or kinship claim.
    variants = {fold(v.strip()) for line in catalogue for v in line.split('|') if v.strip()}
    result = []
    for row in rows:
        name = row['name']
        if name.startswith("'") and name[1:2] in ('=', '+', '-', '@'):
            name = name[1:]
        tokens = re.sub(r'\(\?\)', '', name).split()
        # Try surname suffixes (including particles), never the first given name.
        surnames = {fold(' '.join(tokens[i:])) for i in range(1, len(tokens))}
        result.append({**row, 'match': 'posible coincidencia' if surnames & variants else ''})
    return result


def write_rows(rows, columns, fmt, out=None):
    out = sys.stdout if out is None else out
    if fmt == 'json':
        json.dump(rows, out, ensure_ascii=False, indent=2)
        out.write('\n')
    else:
        writer = csv.writer(out, delimiter='\t', lineterminator='\n')
        writer.writerow(columns)
        for row in rows:
            writer.writerow([clean_cell(str(row.get(k, '')), markup=False, preserve_whitespace=True) for k in columns])


def add_parser(sub):
    p = sub.add_parser('leads', help='offline collateral clues from local transcriptions; human validation required')
    commands = p.add_subparsers(dest='action', required=True)
    ex = commands.add_parser('extract', help='fixed French patterns; clues only, never creates people or kinship')
    ex.add_argument('--in', dest='input', required=True, help='UTF-8 text; ref/date headers; acts separated by ---')
    ex.add_argument('--subject', help='exclude subject and explicitly declared parents in that subject clause')
    ex.add_argument('--format', choices=['tsv', 'json'], default='tsv')
    ex.add_argument('--group-by', choices=['name'], help='emit name/count instead of individual evidence rows')
    match = commands.add_parser('match', help='surname variants yield only "posible coincidencia"')
    match.add_argument('--leads', required=True, help='ungrouped extract TSV')
    match.add_argument('--catalogue-names', help='optional UTF-8 surnames, one per line; variants separated by |')
    match.add_argument('--format', choices=['tsv', 'json'], default='tsv')


def _run_extract(args):
    path = os.path.realpath(os.path.abspath(os.path.expanduser(args.input)))
    with open(path, encoding='utf-8', newline='') as source:
        rows = extract(source.read(), args.subject)
    if args.group_by:
        return group_names(rows), ['name', 'count']
    return rows, COLUMNS


def _read_leads(path):
    # CLI deliberately reads the local file the caller names. Resolve it before parsing.
    path = Path(path).expanduser().resolve(strict=True)
    with io.StringIO(path.read_text(encoding='utf-8'), newline='') as source:
        reader = csv.DictReader(source, delimiter='\t')
        if reader.fieldnames != COLUMNS:
            raise ValueError('leads TSV must have the ungrouped extract header')
        rows = list(reader)
    if any(None in r or any(v is None for v in r.values()) for r in rows):
        raise ValueError('malformed leads TSV row')
    return rows


def _run_match(args):
    rows = _read_leads(args.leads)
    catalogue = Path(args.catalogue_names).read_text(encoding='utf-8').splitlines() if args.catalogue_names else []
    return match_names(rows, catalogue), COLUMNS + ['match']


def run(args, _client=None):
    rows, columns = _run_extract(args) if args.action == 'extract' else _run_match(args)
    write_rows(rows, columns, args.format)
    return 0
