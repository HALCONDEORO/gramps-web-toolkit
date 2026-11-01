"""Coverage of a register sweep: which views of a register were read, and which were not.

Every view of some portals is a double page, and the views are consecutive. Reading only the odd views, or only
the first of every pair, reads half of the register without any error. This is offline: it reads numbers."""
from __future__ import annotations

import re
import sys
from pathlib import Path

MAX_VIEWS = 100000
STEM = re.compile(r"^(\d{4,})_")


def parse_views(expr: str, total: int) -> set[int]:
    """'1-5,9,12-' as view numbers, 1-based. A number outside 1..total or a backwards range is an error."""
    out: set[int] = set()
    for part in expr.split(","):
        part = part.strip()
        if not part:
            continue
        a, dash, b = part.partition("-")
        try:
            lo = int(a)
            hi = int(b) if b.strip() else (total if dash else lo)
        except ValueError:
            raise ValueError(f"cannot read {part!r} as a view or a range of views") from None
        if not 1 <= lo <= hi <= total:
            raise ValueError(f"{part!r} is not inside 1-{total}, or runs backwards")
        out.update(range(lo, hi + 1))
    return out


def views_in_folder(folder: str, total: int) -> set[int]:
    """The views a `gwt-fetch` folder holds: files named NNNN_label.ext, as the downloader writes them."""
    path = Path(folder)
    if not path.is_dir():
        raise ValueError(f"{folder} is not a folder")
    out = set()
    for f in path.iterdir():
        m = STEM.match(f.name)
        if m and f.is_file() and f.stat().st_size > 0:
            n = int(m[1])
            if not 1 <= n <= total:
                raise ValueError(f"{f.name} is view {n}, outside 1-{total}")
            out.add(n)
    return out


def ranges(nums: list[int]) -> str:
    """[1, 2, 3, 7, 9, 10] as '1-3,7,9-10'."""
    out, start, prev = [], None, None
    for n in nums:
        if start is None:
            start = prev = n
        elif n == prev + 1:
            prev = n
        else:
            out.append((start, prev))
            start = prev = n
    if start is not None:
        out.append((start, prev))
    return ",".join(str(a) if a == b else f"{a}-{b}" for a, b in out)


def report(total: int, read: set[int]) -> tuple[list[str], bool]:
    """The lines to print and whether every view was read."""
    missing = [n for n in range(1, total + 1) if n not in read]
    lines = [f"read {len(read)} of {total} views"]
    if not missing:
        return lines + ["every view was read"], True
    lines.append(f"missing {len(missing)}: {ranges(missing)}")
    if len(missing) >= 3 and len(read) >= 3:
        for parity, name in ((0, "even"), (1, "odd")):
            if all(n % 2 == parity for n in missing) and all(n % 2 != parity for n in read):
                lines.append(f"warning: every {name} view is missing and every other one was read: a sweep of "
                             "alternate views. Each view can be a double page, so this reads half of the register.")
    return lines, False


def run(args, client) -> int:
    del client  # offline
    if not 1 <= args.total <= MAX_VIEWS:
        print(f"gwt-fetch: total must be between 1 and {MAX_VIEWS}", file=sys.stderr)
        return 2
    try:
        read: set[int] = set()
        for expr in args.read or []:
            read |= parse_views(expr, args.total)
        if args.dir:
            read |= views_in_folder(args.dir, args.total)
    except ValueError as e:
        print(f"gwt-fetch: {e}", file=sys.stderr)
        return 2
    if not args.read and not args.dir:
        print("gwt-fetch: give --read or --dir", file=sys.stderr)
        return 2
    lines, complete = report(args.total, read)
    print("\n".join(lines))
    return 0 if complete else 1
