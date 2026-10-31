#!/usr/bin/env python3
"""gwt-acts: check act cards offline before anything is written to the tree.

A reader of a register hands over one card per act (a TSV row). This checks the cards and, with a tree dump,
says whether the person is already in the tree. It reads; it never writes to a tree and never fetches anything.

  gwt-acts check CARDS.tsv [--dump dump.json]

Header (any order): card, type, given, surname, date, date_iso, republican, father, mother, state, cote, ark,
url, filiation, zoom, place, notes. Only the columns below are required in a card:

  state       verificado-zoom | dudoso | por-confirmar-acta
  cote ark url filiation date_iso   where the act is, what it says of the parents, the day it carries
  zoom        required when state is verificado-zoom (the image or crop the identity lines were read on)
  republican  when set, date_iso must be what `gwt-dates` computes for it

A card is rejected when a required column is empty, the state is unknown, "verificado-zoom" has no zoom or a
name with "(?)", a Republican date does not give date_iso, or an "acte delivre" is not dated "bef ...".
A card with parents and a state other than verificado-zoom is marked read-only: its parents are not to be written.

With --dump: the card is looked up in the tree (surname with spelling variants, given name, year, place) and
gets "nouveau", "already as P..." or "possible duplicate (?) P...", plus a spelling note when the surname matched
only by sound, and a CONFLICT line when the act's day differs from the tree's for the same person. A conflict
is reported, never replaced.

Output: one TSV line per card (card, verdict, detail). Exit 0 when nothing was rejected and no conflict was
found, 1 otherwise, 2 when a file cannot be read.
"""
import argparse, csv, datetime, json, os, re, sys

HERE = os.path.dirname(os.path.realpath(__file__))
for sibling in ('cross', 'dates'):
    sys.path.insert(0, os.path.join(HERE, '..', sibling))
import gwt_cross as cross
import gwt_dates as dates

STATES = ('verificado-zoom', 'dudoso', 'por-confirmar-acta')
REQUIRED = ('cote', 'ark', 'url', 'filiation', 'date_iso')
SAME, POSSIBLE = 10, 7
EVENTS = {'bautismo': 'Baptism', 'nacimiento': 'Birth', 'entierro': 'Burial', 'defuncion': 'Death'}
DATE = re.compile(r'^(?:(bef|aft|abt) )?(\d{4})(?:-(\d{2})(?:-(\d{2}))?)?$')


def split_date(text):
    """(qualifier, YYYY[-MM[-DD]]) of a card date, or None when it is not a real date."""
    m = DATE.match((text or '').strip())
    if not m:
        return None
    q, y, mo, d = m.groups()
    try:
        datetime.date(int(y), int(mo or 1), int(d or 1))
    except ValueError:
        return None
    return q or '', text.strip().split(' ')[-1]


def cell(text):
    """One output cell: one line, no tab, no leading spreadsheet-formula character."""
    out = re.sub(r'\s+', ' ', str(text or '')).strip()
    return "'" + out if out[:1] in ('=', '+', '-', '@') else out


def read_cards(path):
    with open(path, encoding='utf-8', newline='') as fh:  # skipcq: PTC-W6004 - a command line tool reads the file its user names
        rows = list(csv.DictReader(fh, delimiter='\t'))
    for i, r in enumerate(rows, 1):
        r['card'] = (r.get('card') or '').strip() or f'row{i}'
    return rows


def card_problems(card):
    """Why this card is rejected: a list of short messages, empty when it is fine."""
    def get(k):
        return (card.get(k) or '').strip()

    out = [f'missing {k}' for k in ('type', 'given', 'surname') + REQUIRED if not get(k)]
    if get('date_iso') and not split_date(get('date_iso')):
        out.append('date_iso is not a date: use YYYY, YYYY-MM or YYYY-MM-DD, optionally after bef, aft or abt')
    if get('type') and cross.norm(get('type')) not in EVENTS:
        out.append('type must be one of ' + ', '.join(EVENTS) + ' (other kinds are not looked up)')
    if get('state') not in STATES:
        out.append(f'state must be one of {", ".join(STATES)}')
    if get('state') == 'verificado-zoom':
        if not get('zoom'):
            out.append('verificado-zoom needs a zoom (the crop the identity lines were read on)')
        if any('?' in get(k) for k in ('given', 'surname', 'father', 'mother')):
            out.append('verificado-zoom with a name marked (?)')
    out += republican_problems(get)
    return out


def republican_problems(get):
    out = []
    if get('republican'):
        try:
            want = dates.parse_republican(get('republican')).isoformat()
        except ValueError as e:
            return [str(e)]
        if get('date_iso') and get('date_iso') != want:
            out.append(f'republican date gives {want}, the card says {get("date_iso")}')
    text = dates.fold(' '.join(get(k) for k in ('date', 'notes')))
    if 'delivre' in text and not get('date_iso').startswith('bef '):
        out.append('an acte delivre is a date of issue, not of the event: write date_iso as "bef YYYY-MM-DD"')
    return out


def tree_dates(dump):
    """gramps id -> {event type: [YYYY-MM-DD or YYYY-MM or YYYY]} for the events that carry a date."""
    objs = dump.get('objects', dump)
    events = {e['handle']: e for e in objs['events']}
    out = {}
    for p in objs['people']:
        for ref in p.get('event_ref_list') or []:
            e = events.get(ref.get('ref')) or {}
            v = (e.get('date') or {}).get('dateval') or []
            if len(v) > 2 and v[2]:
                iso = f'{v[2]:04d}' + (f'-{v[1]:02d}' if v[1] else '') + (f'-{v[0]:02d}' if v[1] and v[0] else '')
                out.setdefault(p.get('gramps_id'), {}).setdefault(e.get('type'), []).append(iso)
    return out


def spelled_differently(why):
    return any(k in why for k in ('apellido(fonetico)', 'apellido~', 'apellido(partes)'))


def judge(top, why, tied):
    """The verdict for the best match and the notes that go with it."""
    ids = ','.join(p['id'] for p in tied)
    named = 'nombre' in why and any(k in why for k in ('ano exacto', 'ano'))
    verdict = f'already as {ids}' if top >= SAME and named else f'possible duplicate (?) {ids}'
    notes = []
    if top >= SAME and not named:
        notes.append('not enough to say it is the same person: the given name and the year must both match')
    if spelled_differently(why):
        notes.append('spelling: the tree writes ' + '/'.join(sorted({x for p in tied for x in p['surnames']})))
    return verdict, notes


def lookup(card, people, by_id):
    """(verdict, details) for one card against the tree."""
    qual, iso = split_date(card.get('date_iso')) or ('', '')
    year = int(iso[:4]) if iso and not qual else None  # a bound is not a year to match on
    row = {'surname': cross.norm(card.get('surname')), 'given': cross.norm(card.get('given')),
           'place': cross.norm(card.get('place')), 'year': year}
    scored = [(cross.score(row, p), p) for p in people if row['surname']]
    top = max((s[0] for s, _ in scored), default=0)
    if top < POSSIBLE:
        return 'nouveau', []
    tied = [p for s, p in scored if s[0] == top]
    verdict, notes = judge(top, next((s[1] for s, _ in scored if s[0] == top), ''), tied)
    notes += conflicts(card, qual, iso, tied, by_id)
    return verdict, notes


def conflicts(card, qual, iso, tied, by_id):
    kind = EVENTS.get(cross.norm(card.get('type')))
    if not kind or qual == 'aft' or qual == 'abt' or (not qual and len(iso) != 10):
        return []
    out = []
    for p in tied:
        for theirs in by_id.get(p['id'], {}).get(kind, []):
            n = min(len(theirs), len(iso))  # a vaguer tree date that holds the day is no conflict
            if (qual == 'bef' and theirs[:n] > iso[:n]) or (not qual and theirs[:n] != iso[:n]):
                what = 'the act says at most' if qual else 'the act says'
                out.append(f'CONFLICT {p["id"]} {kind}: {what} {iso}, the tree says {theirs}')
    return out


def check(cards, dump=None):
    """A list of (card id, verdict, detail, failed)."""
    people = cross.tree_people(dump) if dump else []
    by_id = tree_dates(dump) if dump else {}
    results = []
    for card in cards:
        problems = card_problems(card)
        if problems:
            results.append((card['card'], 'rejected', '; '.join(problems), True))
            continue
        verdict, notes = lookup(card, people, by_id) if dump else ('ok', [])
        if (card.get('father') or card.get('mother')) and (card.get('state') or '').strip() != 'verificado-zoom':
            notes.append('read-only: the parents are not to be written (not verificado-zoom)')
        results.append((card['card'], verdict, '; '.join(notes), any(n.startswith('CONFLICT') for n in notes)))
    return results


def main(argv=None):
    ap = argparse.ArgumentParser(prog='gwt-acts', description=__doc__.split('\n')[0])
    sub = ap.add_subparsers(dest='cmd', required=True)
    c = sub.add_parser('check', help='check act cards, and look them up in a tree dump')
    c.add_argument('cards')
    c.add_argument('--dump', help='a gwt-check tree dump (JSON)')
    args = ap.parse_args(argv)
    try:
        cards = read_cards(args.cards)
        dump = json.load(open(args.dump, encoding='utf-8')) if args.dump else None
        results = check(cards, dump)
    except (OSError, ValueError, KeyError, csv.Error) as e:
        print(f'gwt-acts: cannot read the cards or the dump: {e}', file=sys.stderr)
        return 2
    print('card\tverdict\tdetail')
    for card, verdict, detail, _ in results:
        print(f'{cell(card)}\t{cell(verdict)}\t{cell(detail)}')
    return 1 if any(r[3] for r in results) else 0


if __name__ == '__main__':
    sys.exit(main())
