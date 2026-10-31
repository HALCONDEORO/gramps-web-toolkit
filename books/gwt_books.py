#!/usr/bin/env python3
"""gwt-books: find a date in a register that has no index, with as few image openings as possible.

A parish register on a viewer (FamilySearch, an archive's own site) is a run of images in date order. You want
the page for one date. Each image you open tells you a date; this tool keeps those landmarks and says which image
to open next, like a binary search that uses the dates already seen. It is offline: it never fetches an image,
never talks to a site. A person opens the image, then types what it showed.

  gwt-books add  --book B --image N --date D [--note "..."]   record that image N of book B shows date D
  gwt-books next --book B --target D [--total N]               the image to open next (--total: images in the book)
  gwt-books show --book B [--target D]                         the landmarks and the window still to search

Dates are YYYY, YYYY-MM or YYYY-MM-DD. The log is a TSV: --log, else $GWT_BOOKS, else ./libros.tsv.
Standard library only.
"""
import argparse, datetime, os, sys

COLUMNS = ('book', 'image', 'date', 'note', 'seen')


def parse_date(text):
    """YYYY, YYYY-MM or YYYY-MM-DD as a date; a short form means its first day."""
    parts = (text or '').strip().split('-')
    try:
        nums = [int(p) for p in parts]
        if not 1 <= len(nums) <= 3:
            raise ValueError
        return datetime.date(nums[0], nums[1] if len(nums) > 1 else 1, nums[2] if len(nums) > 2 else 1)
    except ValueError:
        raise SystemExit('bad date %r: use YYYY, YYYY-MM or YYYY-MM-DD' % text)


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


def suggest(marks, target, total=None):
    """(image, why): the next image to open, never one already seen."""
    seen = {n for n, _, _ in marks}
    lo, hi = window(marks, target)
    if lo and hi and lo[1] == target:
        return lo[0], 'image %d already shows the target date' % lo[0]
    if lo and hi:
        if hi[0] - lo[0] <= 1:
            return None, 'the target falls between images %d and %d, which are next to each other' % (lo[0], hi[0])
        span = (hi[1] - lo[1]).days or 1
        frac = (target - lo[1]).days / span
        guess = lo[0] + round(frac * (hi[0] - lo[0]))
        guess = min(max(guess, lo[0] + 1), hi[0] - 1)
        return guess, 'between %d (%s) and %d (%s)' % (lo[0], lo[1], hi[0], hi[1])
    if not marks:
        if not total:
            return None, 'no landmarks yet: pass --total (images in the book) to get a first image'
        return max(total // 2, 1), 'no landmarks yet: open the middle of the book'
    if lo and not hi:
        if not total or lo[0] >= total:
            return None, 'every landmark is earlier than the target and the book has no image after %d' % lo[0]
        step = max((total - lo[0]) // 2, 1)
        return min(lo[0] + step, total), 'all landmarks are earlier than the target: look later in the book'
    step = max(hi[0] // 2, 1)
    guess = max(hi[0] - step, 1)
    while guess in seen and guess > 1:
        guess -= 1
    if hi[0] <= 1:
        return None, 'every landmark is later than the target and image 1 is already seen'
    return guess, 'all landmarks are later than the target: look earlier in the book'


def cmd_add(args):
    parse_date(args.date)
    path = log_path(args)
    new = not os.path.exists(path) or os.path.getsize(path) == 0
    with open(path, 'a', encoding='utf-8') as fh:
        if new:
            fh.write('\t'.join(COLUMNS) + '\n')
        fh.write('\t'.join([args.book, str(args.image), args.date, args.note.replace('\t', ' '),
                            datetime.date.today().isoformat()]) + '\n')
    print('noted: %s image %d = %s' % (args.book, args.image, args.date))
    return 0


def cmd_next(args):
    marks = landmarks(read_log(log_path(args)), args.book)
    image, why = suggest(marks, parse_date(args.target), args.total)
    if image is None:
        print(why)
        return 1
    print('open image %d: %s' % (image, why))
    lo, hi = window(marks, parse_date(args.target))
    if lo and hi and hi[0] > lo[0]:
        print('%d images left between %d and %d' % (hi[0] - lo[0] - 1, lo[0], hi[0]))
    return 0


def cmd_show(args):
    marks = landmarks(read_log(log_path(args)), args.book)
    for n, d, note in marks:
        print('%6d  %s  %s' % (n, d, note))
    if args.target:
        lo, hi = window(marks, parse_date(args.target))
        print('target %s: from image %s to image %s' % (args.target, lo[0] if lo else '?', hi[0] if hi else '?'))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog='gwt-books', description=__doc__.split('\n\n')[0])
    ap.add_argument('--log')
    sub = ap.add_subparsers(dest='cmd', required=True)
    a = sub.add_parser('add')
    a.add_argument('--book', required=True)
    a.add_argument('--image', type=int, required=True)
    a.add_argument('--date', required=True)
    a.add_argument('--note', default='')
    n = sub.add_parser('next')
    n.add_argument('--book', required=True)
    n.add_argument('--target', required=True)
    n.add_argument('--total', type=int)
    s = sub.add_parser('show')
    s.add_argument('--book', required=True)
    s.add_argument('--target')
    args = ap.parse_args(argv)
    return {'add': cmd_add, 'next': cmd_next, 'show': cmd_show}[args.cmd](args)


if __name__ == '__main__':
    sys.exit(main())
