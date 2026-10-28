"""Republican dates written in notes with their Gregorian equivalent ("14 germinal an 13 = 04/04/1805"): is the
conversion right? A report: it never changes the tree.

An act from 1793 to 1805 is dated in the Republican calendar, and the note that transcribes it usually adds the
day it falls on. That conversion is done by hand, and a day off is easy to make and then copied into a date. Each
conversion found is recomputed (year 1 to 14, the same count Gramps uses) and the ones that differ are listed.
"""

from __future__ import annotations

import datetime
import re

from .dates import _french
from .fill import _fold
from .tree import Tree

MONTHS = ("vendemiaire", "brumaire", "frimaire", "nivose", "pluviose", "ventose", "germinal", "floreal", "prairial",
          "messidor", "thermidor", "fructidor")
_ROMAN = {"i": 1, "ii": 2, "iii": 3, "iv": 4, "v": 5, "vi": 6, "vii": 7, "viii": 8, "ix": 9, "x": 10, "xi": 11,
          "xii": 12, "xiii": 13, "xiv": 14}
# "<day> <month> an <year>", then up to 40 characters that are not an equals sign, then "= dd/mm/yyyy"
_CONVERSION = re.compile(
    r"(?<!\w)(\d{1,2})(?:er)?\s+(" + "|".join(MONTHS) + r")\s+an\s+(\d{1,2}|[ivx]+)(?!\w)[^=\n]{0,40}?=\s*"
    r"(\d{1,2})/(\d{1,2})/(\d{4})")


def _texts(tree: Tree):
    for h, n in tree.objs["notes"].items():
        t = n.get("text")
        yield "note", n.get("gramps_id"), (t.get("string", "") if isinstance(t, dict) else str(t or ""))
    for h, c in tree.objs["citations"].items():
        yield "citation", c.get("gramps_id"), c.get("page") or ""
    for h, e in tree.objs["events"].items():
        yield "event", e.get("gramps_id"), e.get("description") or ""


def republican_report(tree: Tree) -> dict:
    rows, seen = [], set()
    for obj, gid, text in _texts(tree):
        for m in _CONVERSION.finditer(_fold(text)):
            day, month, year, d2, m2, y2 = m.groups()
            year = _ROMAN.get(year) or (int(year) if year.isdigit() else 0)
            key = (obj, gid, m.group(0))
            if key in seen:
                continue
            seen.add(key)
            label = f"{int(day)} {MONTHS[MONTHS.index(m.group(2))]} an {year}"
            got = _french(year, MONTHS.index(m.group(2)) + 1, int(day))
            stated = f"{int(d2):02d}/{int(m2):02d}/{y2}"
            if not got:
                rows.append({"object": obj, "id": gid, "republican": label, "stated": stated, "computed": None,
                             "status": "unreadable"})
                continue
            computed = datetime.date.fromordinal(got[0]).strftime("%d/%m/%Y")
            rows.append({"object": obj, "id": gid, "republican": label, "stated": stated, "computed": computed,
                         "status": "ok" if computed == stated else "differs"})
    rows.sort(key=lambda r: (r["status"] == "ok", r["object"], r["id"] or ""))
    return {"rows": rows, "differs": sum(r["status"] == "differs" for r in rows),
            "unreadable": sum(r["status"] == "unreadable" for r in rows)}


def print_republican(rep: dict) -> None:
    total = len(rep["rows"])
    for r in rep["rows"]:
        if r["status"] == "differs":
            print(f"  {r['object']} {r['id']}: {r['republican']} = {r['stated']}, recomputed {r['computed']}")
        elif r["status"] == "unreadable":
            print(f"  {r['object']} {r['id']}: {r['republican']} = {r['stated']}, not a Republican day (year 1 to 14)")
    print(f"conversions found: {total}; differ: {rep['differs']}; unreadable: {rep['unreadable']}")
