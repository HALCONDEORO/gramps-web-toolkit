#!/usr/bin/env python3
"""gwt-books: find a date in a register that has no index, with as few image openings as possible.

A parish register on a viewer (FamilySearch, an archive's own site) is a run of images in date order. You want
the page for one date. Each image you open tells you a date; this tool keeps those landmarks and says which image
to open next, like a binary search that uses the dates already seen. It is offline: it never fetches an image,
never talks to a site. A person opens the image, then types what it showed.

  gwt-books add  --book B --image N --date D [--note "..."]   record that image N of book B shows date D
  gwt-books next --book B --target D [--total N]               the image to open next (--total: images in the book)
  gwt-books show --book B [--target D]                         the landmarks and the window still to search
  gwt-books status --book B [--target D] [--total N]           how far the search is and the zone left
  gwt-books export --book B [--out FILE]                       the landmarks as CSV

Dates are YYYY, YYYY-MM or YYYY-MM-DD. The log is a TSV: --log, else $GWT_BOOKS, else ./libros.tsv.
Standard library only.
"""
import argparse, csv, datetime, os, re, sys

COLUMNS = ('book', 'image', 'date', 'note', 'seen')


DATE = re.compile(r'(\d{4})(?:-(\d{2})(?:-(\d{2}))?)?')


def parse_date(text):
    """YYYY, YYYY-MM or YYYY-MM-DD as a date; a short form means its first day. Nothing else around it."""
    m = DATE.fullmatch(text or '')
    try:
        if not m:
            raise ValueError
        return datetime.date(int(m[1]), int(m[2] or 1), int(m[3] or 1))
    except ValueError:
        raise SystemExit(f'bad date {text!r}: use YYYY, YYYY-MM or YYYY-MM-DD')


def log_path(args):
    return args.log or os.environ.get('GWT_BOOKS') or 'libros.tsv'


def read_log(path):
    if not os.path.exists(path):
        return []
    rows = []
    with open(path, encoding='utf-8') as fh:
        for line in fh.read().splitlines()[1:]:
            cells = line.split('\t') + [''] * len(COLUMNS)
            rows.append(dict(zip(COLUMNS, cells)))
    return rows


def landmarks(rows, book):
    """The latest reading of each image of one book, as (image, date, note), by image number."""
    seen = {}
    for r in rows:
        if r['book'] == book:
            seen[int(r['image'])] = (parse_date(r['date']), r['note'])
    return sorted((n, d, note) for n, (d, note) in seen.items())


def window(marks, target):
    """The tightest images around the target: (lo, hi) where lo is the last landmark dated on or before the
    target and hi the first dated on or after it; None for a side no landmark reaches. A landmark that
    equals the target is both sides."""
    lo = hi = None
    for n, d, _ in marks:
        if d <= target:
            lo = (n, d)
        if d >= target and hi is None:
            hi = (n, d)
    return lo, hi


def _between(lo, hi, target):
    if hi[0] - lo[0] <= 1:
        return None, f'images {lo[0]} and {hi[0]} are next to each other and bracket the target: review both'
    span = (hi[1] - lo[1]).days or 1
    guess = lo[0] + round((target - lo[1]).days / span * (hi[0] - lo[0]))
    guess = min(max(guess, lo[0] + 1), hi[0] - 1)
    return guess, f'between {lo[0]} ({lo[1]}) and {hi[0]} ({hi[1]})'


def _one_side(lo, hi, total):
    """Landmarks on one side of the target only: move toward the unexplored end."""
    if lo:
        if not total or lo[0] >= total:
            return None, f'every landmark is earlier than the target and the book has no image after {lo[0]}'
        return min(lo[0] + max((total - lo[0]) // 2, 1), total), 'all landmarks are earlier than the target: look later'
    if hi[0] <= 1:
        return None, 'every landmark is later than the target and image 1 is already seen'
    return max(hi[0] - max(hi[0] // 2, 1), 1), 'all landmarks are later than the target: look earlier'


def suggest(marks, target, total=None):
    """(image, why): the next image to open, never one already seen."""
    lo, hi = window(marks, target)
    if lo and hi and lo[1] == target:
        return lo[0], f'image {lo[0]} already shows the target date'
    if lo and hi:
        return _between(lo, hi, target)
    if marks:
        return _one_side(lo, hi, total)
    if not total:
        return None, 'no landmarks yet: pass --total (images in the book) to get a first image'
    return max(total // 2, 1), 'no landmarks yet: open the middle of the book'


def clean(text, what):
    """A book label or note lives in one TSV cell: no tab or line break."""
    if any(c in text for c in '\t\r\n'):
        raise SystemExit(f'{what} must not contain a tab or a line break')
    return text


def book_name(text):
    """The same label in every command: no tab or line break, no edge spaces, not empty."""
    name = clean(text, '--book').strip()
    if not name:
        raise argparse.ArgumentTypeError('--book must not be empty')
    return name


def positive(text):
    try:
        n = int(text)
    except ValueError:
        n = 0
    if n < 1:
        raise argparse.ArgumentTypeError(f'{text!r} is not a positive whole number')
    return n


def cmd_add(args):
    parse_date(args.date)
    book, note = args.book, clean(args.note, '--note')
    path = log_path(args)
    new = not os.path.exists(path) or os.path.getsize(path) == 0
    with open(path, 'a', encoding='utf-8') as fh:
        if new:
            fh.write('\t'.join(COLUMNS) + '\n')
        fh.write('\t'.join([book, str(args.image), args.date, note, datetime.date.today().isoformat()]) + '\n')
    print(f'noted: {book} image {args.image} = {args.date}')
    return 0


def zone(marks, target, total=None):
    """The images still to review for the target, as (first, last, count); last is None when the book size is
    unknown. None when no landmark is known yet or the target is already on an image."""
    lo, hi = window(marks, target)
    if lo and hi:
        return None if lo[1] == target else (lo[0] + 1, hi[0] - 1, max(hi[0] - lo[0] - 1, 0))
    if lo:
        return (lo[0] + 1, total, total - lo[0] if total else None)
    if hi:
        return (1, hi[0] - 1, hi[0] - 1)
    return None


def zone_line(z):
    first, last, count = z
    end = f'image {last}' if last else 'the end of the book'
    return f'remaining zone: image {first} to {end}' + (f' ({count} images)' if count is not None else '')


def check_total(marks, total):
    if total and marks and total < marks[-1][0]:
        raise SystemExit(f'--total {total} is below image {marks[-1][0]}, which is already marked')


def cmd_next(args):
    marks = landmarks(read_log(log_path(args)), args.book)
    check_total(marks, args.total)
    target = parse_date(args.target)
    image, why = suggest(marks, target, args.total)
    print(why if image is None else f'open image {image}: {why}')
    z = zone(marks, target, args.total)
    if z:
        print(zone_line(z))
    return 1 if image is None else 0


def cmd_show(args):
    marks = landmarks(read_log(log_path(args)), args.book)
    for n, d, note in marks:
        print(f'{n:6d}  {d}  {note}')
    if args.target:
        lo, hi = window(marks, parse_date(args.target))
        print(f"target {args.target}: from image {lo[0] if lo else '?'} to image {hi[0] if hi else '?'}")
    return 0


def cmd_status(args):
    marks = landmarks(read_log(log_path(args)), args.book)
    if not marks:
        print(f'{args.book}: no landmarks yet. Open an image, then: gwt-books add --book "{args.book}" --image N --date D')
        return 1
    check_total(marks, args.total)
    print(f'{args.book}: {len(marks)} landmarks, images {marks[0][0]} to {marks[-1][0]}, dates {marks[0][1]} to {marks[-1][1]}')
    if args.target:
        target = parse_date(args.target)
        image, why = suggest(marks, target, args.total)
        print(f'target {args.target}: ' + (f'next open image {image} ({why})' if image else why))
        z = zone(marks, target, args.total)
        if z:
            print(zone_line(z))
    return 0


def cmd_export(args):
    marks = landmarks(read_log(log_path(args)), args.book)
    out = open(args.out, 'w', newline='', encoding='utf-8') if args.out else sys.stdout
    try:
        writer = csv.writer(out, lineterminator='\n')
        writer.writerow(('book', 'image', 'date', 'note'))
        for n, d, note in marks:
            writer.writerow((args.book, n, d.isoformat(), note))
    finally:
        if args.out:
            out.close()
    return 0


EPILOG = """examples:
  gwt-books add --book register-1 --image 120 --date 1905-03 --note baptisms
  gwt-books next --book register-1 --target 1926-05 --total 640
  gwt-books status --book register-1 --target 1926-05
  gwt-books export --book register-1 --out register-1.csv
"""


def main(argv=None):
    ap = argparse.ArgumentParser(prog='gwt-books', description=__doc__.split('\n\n')[0], epilog=EPILOG,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--log')
    sub = ap.add_subparsers(dest='cmd', required=True)
    a = sub.add_parser('add')
    a.add_argument('--book', type=book_name, required=True)
    a.add_argument('--image', type=positive, required=True)
    a.add_argument('--date', required=True)
    a.add_argument('--note', default='')
    n = sub.add_parser('next')
    n.add_argument('--book', type=book_name, required=True)
    n.add_argument('--target', required=True)
    n.add_argument('--total', type=positive)
    s = sub.add_parser('show')
    s.add_argument('--book', type=book_name, required=True)
    s.add_argument('--target')
    t = sub.add_parser('status', help='landmarks, dates covered and the zone left for a target')
    t.add_argument('--book', type=book_name, required=True)
    t.add_argument('--target')
    t.add_argument('--total', type=positive)
    e = sub.add_parser('export', help='the landmarks as CSV')
    e.add_argument('--book', type=book_name, required=True)
    e.add_argument('--out')
    args = ap.parse_args(argv)
    return {'add': cmd_add, 'next': cmd_next, 'show': cmd_show, 'status': cmd_status, 'export': cmd_export}[args.cmd](args)


if __name__ == '__main__':
    sys.exit(main())
