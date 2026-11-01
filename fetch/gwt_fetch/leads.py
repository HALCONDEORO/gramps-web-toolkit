"""Local, fixed-pattern collateral clues. A human must validate every literal."""
from __future__ import annotations

import csv
import datetime
import json
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


def extract(text, subject=None):
    rows = []
    for headers, body in _blocks(text):
        previous_end = 0
        active = None
        subject_parent_chain = False
        for found in NAME.finditer(body):
            name = found.group()
            left = body[previous_end:found.start()]
            if re.search(r'[.;]', left):
                active = None
                subject_parent_chain = False
            principal = bool(PRINCIPAL.search(left) or (
                subject and fold(name) == fold(subject) and not MARKER.search(left)))
            if principal:
                active = None
                subject_parent_chain = bool(subject and fold(name) == fold(subject))
                previous_end = found.end()
                continue
            markers = list(MARKER.finditer(left))
            if markers:
                marker = markers[-1]
                kind = next(k for k in ROLES if marker.group(k) is not None)
                active = (ROLES[kind], marker.group(), previous_end + marker.start())
            elif not re.fullmatch(r'\s*(?:,\s*)?(?:et\s+(?:de\s+)?)?', left):
                active = None
                subject_parent_chain = False
            role, relation, start = active or ('other', '', found.start())
            end = found.end()
            # A collective trailing declaration belongs to each listed witness.
            tail = re.split(r'[.;]', body[end:], maxsplit=1)[0]
            friends = FRIENDS.search(tail)
            if role == 'witness' and friends:
                relation = friends.group().strip()
                end += friends.end()
            excluded_parent = subject_parent_chain and role in ('parent', 'deceased_parent')
            if role not in ('parent', 'deceased_parent'):
                subject_parent_chain = False
            if not excluded_parent and not (subject and fold(name) == fold(subject)):
                literal = body[start:end]
                confidence = 'low' if '(?)' in left + name or role == 'other' else 'high'
                # Negated, doubtful or alternative wording anywhere in the clause
                # means the role is not declared: keep the whole clause as evidence.
                clause_start = max(body.rfind('.', 0, found.start()), body.rfind(';', 0, found.start())) + 1
                clause_end = len(body)
                stop = re.search(r'[.;]', body[end:])
                if stop:
                    clause_end = end + stop.start()
                clause = body[clause_start:clause_end]
                if _doubtful(clause):
                    role, confidence = 'other', 'low'
                    literal = clause.strip()
                rows.append(dict(ref=headers['ref'], date=headers['date'], name=name, role=role,
                                 declared_relation=relation, confidence=confidence,
                                 literal=literal))
            previous_end = found.end()
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


def run(args, client=None):
    if args.action == 'extract':
        with open(args.input, encoding='utf-8', newline='') as source:
            rows = extract(source.read(), args.subject)
        columns = COLUMNS
        if args.group_by:
            rows, columns = group_names(rows), ['name', 'count']
    else:
        with open(args.leads, encoding='utf-8', newline='') as source:
            reader = csv.DictReader(source, delimiter='\t')
            if reader.fieldnames != COLUMNS:
                raise ValueError('leads TSV must have the ungrouped extract header')
            rows = list(reader)
        if any(None in r or any(v is None for v in r.values()) for r in rows):
            raise ValueError('malformed leads TSV row')
        catalogue = Path(args.catalogue_names).read_text(encoding='utf-8').splitlines() if args.catalogue_names else []
        rows, columns = match_names(rows, catalogue), COLUMNS + ['match']
    write_rows(rows, columns, args.format)
    return 0
