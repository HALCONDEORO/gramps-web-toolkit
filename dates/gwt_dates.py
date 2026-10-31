#!/usr/bin/env python3
"""gwt-dates: French Republican dates and old-style month names, converted offline and by calculation.

A French act from 1792 to 1805 is dated in the Republican calendar, and acts of the 1700s and 1800s write
September as "7bre" (the seventh month of the old Roman year). Both are easy to misread and to convert by hand.

  gwt-dates r2g "14 germinal an XIII"        -> 1805-04-04
  gwt-dates r2g "3 jour complementaire an II" --as-note
  gwt-dates g2r 1805-04-04                   -> 14 germinal an XIII
  gwt-dates parse "30 7bre 1812"             -> 1812-09-30

Years 1 to 14 only (22 September 1792 to 31 December 1805). Years 3, 7 and 11 had a sixth complementary day,
as the calendar was applied. Output is one line; --as-note prints the "14 germinal an 13 = 04/04/1805" form
that `gwt-check republican` reads back. Exit 0 on success, 2 on a date it cannot read or that does not exist.
Standard library only.
"""
import argparse, datetime, re, sys, unicodedata

MONTHS = ('vendemiaire', 'brumaire', 'frimaire', 'nivose', 'pluviose', 'ventose', 'germinal', 'floreal',
          'prairial', 'messidor', 'thermidor', 'fructidor')
ROMAN = {'i': 1, 'ii': 2, 'iii': 3, 'iv': 4, 'v': 5, 'vi': 6, 'vii': 7, 'viii': 8, 'ix': 9, 'x': 10, 'xi': 11,
         'xii': 12, 'xiii': 13, 'xiv': 14}
NUMERALS = ('I', 'II', 'III', 'IV', 'V', 'VI', 'VII', 'VIII', 'IX', 'X', 'XI', 'XII', 'XIII', 'XIV')
SEXTILE = (3, 7, 11)
LAST_DAY = datetime.date(1805, 12, 31)  # 10 nivose an XIV, the last day of the calendar
OLD_STYLE = {'7bre': 9, '8bre': 10, '9bre': 11, '10bre': 12, 'xbre': 12}
GREGORIAN = ('janvier', 'fevrier', 'mars', 'avril', 'mai', 'juin', 'juillet', 'aout', 'septembre', 'octobre',
             'novembre', 'decembre')


def fold(text):
    """Lower case, no accents, single spaces."""
    text = unicodedata.normalize('NFD', text.lower())
    return re.sub(r'\s+', ' ', ''.join(c for c in text if unicodedata.category(c) != 'Mn')).strip()


def year_length(year):
    return 366 if year in SEXTILE else 365


def year_start(year):
    """The Gregorian day of 1 vendemiaire of a Republican year (1 to 14)."""
    day = datetime.date(1792, 9, 22)
    for y in range(1, year):
        day += datetime.timedelta(days=year_length(y))
    return day


def to_gregorian(day, month, year):
    """A Republican date as a date. month is 1 to 12, or 13 for the complementary days."""
    if not 1 <= year <= 14:
        raise ValueError(f'year {year} is outside the calendar (an I to an XIV)')
    if month == 13:
        if not 1 <= day <= (6 if year in SEXTILE else 5):
            raise ValueError(f'an {year} has no complementary day {day}')
        offset = 360 + day - 1
    else:
        if not (1 <= month <= 12 and 1 <= day <= 30):
            raise ValueError('a Republican month has 30 days, numbered 1 to 30')
        offset = (month - 1) * 30 + day - 1
    out = year_start(year) + datetime.timedelta(days=offset)
    if out > LAST_DAY:
        raise ValueError('the calendar ended on 10 nivose an XIV (31 December 1805)')
    return out


def to_republican(date):
    """A date as (day, month, year); month 13 is a complementary day."""
    if not datetime.date(1792, 9, 22) <= date <= LAST_DAY:
        raise ValueError(f'{date} is outside the calendar (22 September 1792 to 31 December 1805)')
    year = 1
    while year < 14 and date >= year_start(year + 1):
        year += 1
    offset = (date - year_start(year)).days
    return offset % 30 + 1, offset // 30 + 1, year


def roman(n):
    return NUMERALS[n - 1]


def label(day, month, year):
    if month == 13:
        return f'{day} jour complementaire an {roman(year)}'
    return f'{day} {MONTHS[month - 1]} an {roman(year)}'


def note(date):
    """The form `gwt-check republican` reads: '14 germinal an 13 = 04/04/1805'."""
    d, m, y = to_republican(date)
    shown = f'{d} jour complementaire an {y}' if m == 13 else f'{d} {MONTHS[m - 1]} an {y}'
    return f'{shown} = {date:%d/%m/%Y}'


REPUBLICAN = re.compile(r'(\d{1,2})\s*(?:er|e|eme|ere)?\s+(?:(' + '|'.join(MONTHS) + r')|'
                        r'(?:jour|jours)\s+(?:complementaires?|sans-?culottides?)|sans-?culottides?)\s+an\s+(\d{1,2}|[ivx]+)')


def parse_republican(text):
    m = REPUBLICAN.fullmatch(fold(text))
    if not m:
        raise ValueError(f'cannot read {text!r} as "<day> <month> an <year>"')
    year = ROMAN.get(m[3]) or (int(m[3]) if m[3].isdigit() else 0)
    return to_gregorian(int(m[1]), MONTHS.index(m[2]) + 1 if m[2] else 13, year)


def parse_gregorian_text(text):
    """'30 7bre 1812', '3 mars 1805', '3 de marzo de 1805' as a date."""
    words = fold(text).replace(',', ' ').replace('.', ' ').split()
    words = [w for w in words if w not in ('de', 'du', 'd')]
    if len(words) != 3 or not words[0].isdigit() or not words[2].isdigit():
        raise ValueError(f'cannot read {text!r} as "<day> <month> <year>"')
    names = {n: i + 1 for i, n in enumerate(GREGORIAN)}
    names.update({'enero': 1, 'febrero': 2, 'marzo': 3, 'abril': 4, 'mayo': 5, 'junio': 6, 'julio': 7,
                  'agosto': 8, 'septiembre': 9, 'setiembre': 9, 'octubre': 10, 'noviembre': 11, 'diciembre': 12})
    names.update(OLD_STYLE)
    month = names.get(words[1])
    if not month:
        raise ValueError(f'unknown month {words[1]!r}')
    return datetime.date(int(words[2]), month, int(words[0]))


def main(argv=None):
    ap = argparse.ArgumentParser(prog='gwt-dates', description=__doc__.split('\n')[0])
    sub = ap.add_subparsers(dest='cmd', required=True)
    r = sub.add_parser('r2g', help='Republican date to Gregorian')
    r.add_argument('text')
    r.add_argument('--as-note', action='store_true', help='print "14 germinal an 13 = 04/04/1805"')
    g = sub.add_parser('g2r', help='Gregorian date (YYYY-MM-DD) to Republican')
    g.add_argument('date')
    p = sub.add_parser('parse', help='a written Gregorian date ("30 7bre 1812") to YYYY-MM-DD')
    p.add_argument('text')
    args = ap.parse_args(argv)
    try:
        if args.cmd == 'r2g':
            d = parse_republican(args.text)
            print(note(d) if args.as_note else d.isoformat())
        elif args.cmd == 'g2r':
            print(label(*to_republican(datetime.date.fromisoformat(args.date))))
        else:
            print(parse_gregorian_text(args.text).isoformat())
    except ValueError as e:
        print(f'gwt-dates: {e}', file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
