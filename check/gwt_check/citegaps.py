"""Events with no citation whose owner already has a citation that states the event's exact date. Proposals
only: nothing is written.

A person or family is often cited as a whole by the act that names the date of a birth, a marriage or a death,
while the event itself carries no citation. When the page or a note of one of the owner's citations writes the
event's full date (day, month and year, in the forms below), that citation is a candidate for the event. A
citation that says it is another kind of act or a marginal mention (gwt-check cites) is held back. The quote
is the proof to read in the record; the same date can be written in an act about someone else.
"""

from __future__ import annotations

import json
import re

from .cites import citation_problem
from .dates import kind
from .fill import _fold, _note_text
from .tree import Tree

# One table of month words. Add a language here, not a regex in a rule.
MONTHS = {
    1: ("enero", "ene", "janvier", "jan", "january"), 2: ("febrero", "feb", "fevrier", "february"),
    3: ("marzo", "mar", "mars", "march"), 4: ("abril", "abr", "avril", "apr", "april"),
    5: ("mayo", "may", "mai"), 6: ("junio", "jun", "juin", "june"), 7: ("julio", "jul", "juillet", "july"),
    8: ("agosto", "ago", "aout", "aug", "august"), 9: ("septiembre", "sept", "sep", "septembre", "september"),
    10: ("octubre", "oct", "octobre", "october"), 11: ("noviembre", "nov", "novembre", "november"),
    12: ("diciembre", "dic", "decembre", "dec", "december"),
}
# a citation that records that a search found nothing writes dates too, but proves nothing about the event
NEGATIVE = ("negativ", "sin fila", "sin acta", "no figura", "aucun", "introuvable", "not found")
_MONTH = {w: m for m, ws in MONTHS.items() for w in ws}
_NUM = re.compile(r"(?<!\d)(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{4})(?!\d)")
_ISO = re.compile(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)")
_WORD = re.compile(r"(?<!\d)(\d{1,2})(?:er)?\s+(?:de\s+|del\s+)?([a-z]+)\.?\s+(?:de\s+|del\s+)?(\d{4})(?!\d)")


def dates_in(text: str) -> dict[tuple[int, int, int], str]:
    """Every full date the text writes, as (year, month, day) -> the words as written."""
    f = _fold(text)
    out: dict[tuple[int, int, int], str] = {}
    for m in _NUM.finditer(f):
        out.setdefault((int(m[3]), int(m[2]), int(m[1])), m[0])
    for m in _ISO.finditer(f):
        out.setdefault((int(m[1]), int(m[2]), int(m[3])), m[0])
    for m in _WORD.finditer(f):
        if m[2] in _MONTH:
            out.setdefault((int(m[3]), _MONTH[m[2]], int(m[1])), m[0])
    return out


def _full(event: dict):
    v = (event.get("date") or {}).get("dateval")
    if isinstance(v, list) and len(v) >= 3 and all(isinstance(x, int) and x for x in v[:3]):
        return v[2], v[1], v[0]
    return None


def propose_citations(tree: Tree, *, living_after: int = 1926) -> dict:
    T = tree.objs
    E, C, N = T["events"], T["citations"], T["notes"]
    owners: dict[str, list[dict]] = {}
    for t in ("people", "families"):
        for o in T[t].values():
            for r in o.get("event_ref_list") or []:
                owners.setdefault(r.get("ref"), []).append(o)
    proposals, held, without, undated = [], [], 0, 0
    for h, e in E.items():
        if e.get("citation_list"):
            continue
        without += 1
        key = _full(e)
        if not key:
            undated += 1
            continue
        if key[0] > living_after:  # the 100-year rule: nothing about people who may be alive
            continue
        for ow in owners.get(h, []):
            for ch in ow.get("citation_list") or []:
                c = C.get(ch)
                if not c:
                    continue
                text = "\n".join([c.get("page") or ""] + [_note_text(N[n]) for n in c.get("note_list") or [] if n in N])
                found = dates_in(text)
                if key not in found:
                    continue
                if any(w in _fold(text) for w in NEGATIVE):
                    continue
                row = {"event": tree.gid("events", h), "kind": kind(e.get("type")), "citation": tree.gid("citations", ch),
                       "date": "%04d-%02d-%02d" % key, "quote": found[key]}
                prob = citation_problem(e, c)
                (held if prob else proposals).append({**row, "why": prob[1]} if prob else row)
    return {"events_without_citation": without, "undated": undated, "proposals": proposals, "held_back": held}


def print_gaps(rep: dict, limit: int) -> None:
    print(f"{rep['events_without_citation']} events without citation ({rep['undated']} without a full date): "
          f"{len(rep['proposals'])} with a citation of their owner that writes their date")
    for x in rep["proposals"][:limit]:
        print(f"  {x['event']} ({x['kind']}, {x['date']}): {x['citation']}  «{x['quote']}»")
    if rep["held_back"]:
        print(f"held back: {len(rep['held_back'])} (the citation says it is another act or a marginal mention)")
    print("proposals only: read each record before attaching one (the date can be written in an act about someone else)")


def write_ops(rep: dict, path: str) -> int:
    """One gwt-apply attach_existing op per proposal, with an empty ruling. Refuses to replace a file."""
    with open(path, "x", encoding="utf-8") as fh:
        for x in rep["proposals"]:
            fh.write(json.dumps({"op": "attach_existing", "key": f"cite-gap-{x['event']}-{x['citation']}", "ruling": "",
                                 "object": x["citation"], "attach": x["event"]}, ensure_ascii=False) + "\n")
    return len(rep["proposals"])
