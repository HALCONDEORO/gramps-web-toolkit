"""Conservative before/after assertions on an event and its citation notes.

Only a whole note consisting of a bound is an assertion. Prose, quotations, negation,
questions and alternatives are deliberately left for a reader. No date is inferred.
"""
from __future__ import annotations

import re

from . import dates
from .fill import _fold, _note_text

# One grammar, shared by every note origin; folding handles case and accents.
_BOUND_NOTE = re.compile(
    r"(before|after|antes de|despues de|avant|apres)\s*"
    r"([0-9]{4})(?:-([0-9]{2})(?:-([0-9]{2}))?)?\.?"
)
_BEFORE = {"before", "antes de", "avant"}


def note_bound(text: str):
    """(modifier, boundary) for an unambiguous whole-note assertion, else None."""
    match = _BOUND_NOTE.fullmatch(_fold(text).strip())
    if not match:
        return None
    word, year, month, day = match.groups()
    if month == "00" or day == "00":
        return None
    return dates.modifier_boundary({"modifier": 1 if word in _BEFORE else 2,
                                    "dateval": [int(day or 0), int(month or 0), int(year), False]})


def check_modifier_notes(T, out, in_scope):
    """Compare every origin equally; report one stable finding with all conflicting evidence."""
    for event in T["events"].values():
        eid = event.get("gramps_id") or event["handle"]
        assertions = []
        related = [("events", event["handle"])]
        bound = dates.modifier_boundary(event.get("date"))
        if bound:
            assertions.append((bound, f"event {eid} date"))
        owners = [(event, f"event {eid}")]
        for handle in event.get("citation_list") or []:
            citation = T["citations"].get(handle)
            if citation:
                related.append(("citations", handle))
                owners.append((citation, f"citation {citation.get('gramps_id') or handle}"))
        for owner, origin in owners:
            for handle in owner.get("note_list") or []:
                note = T["notes"].get(handle)
                if note:
                    related.append(("notes", handle))
                    bound = note_bound(_note_text(note))
                    if bound:
                        assertions.append((bound, f"{origin} note {note.get('gramps_id') or handle}"))
        if not any(in_scope(t, h) for t, h in related):
            continue
        conflicts = set()
        for i, (a, origin_a) in enumerate(assertions):
            for b, origin_b in assertions[i + 1:]:
                if a[0] != b[0] and a[1] == b[1]:
                    conflicts.add((a, origin_a))
                    conflicts.add((b, origin_b))
        if conflicts:
            detail = "; ".join(f"{origin}: {'before' if bound[0] == 1 else 'after'} "
                               f"{dates._label(*bound[1])}" for bound, origin in sorted(conflicts))
            out.add("date-modifier-note", eid, detail)
