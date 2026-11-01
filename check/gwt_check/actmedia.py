"""Which media a person's events carry, which notes transcribe the act, and a file name to propose. Read only.

The input is a dump (`gwt-check dump`) or a gwt-apply snapshot, plus the Gramps IDs of the persons (and,
optionally, events) to look at. Nothing here reads an image, opens a file, renames anything or writes to the
tree. Three answers are kept apart, so one can be used without the others:

- `media`: every media reference found, with the object that holds it and a linkage level. Only a reference on
  the event, or on a citation of the event, is a candidate for the act of that event; one on the person, on a
  citation of the person or on a source says nothing about this act and is never counted as the act's capture.
  Even a candidate is unverified: the image was not read. No media found in the dump is a fact about the dump,
  not proof that no capture exists.
- `transcription`: notes of type Transcript, or whose text says it is a literal transcription, found by the same
  levels. The text is never copied into the report.
- `filename`: a proposed stem (person, event, date, cote or ark, view), each part with where it came from. A part
  that is missing, qualified or contradictory is `unknown`, never guessed. It is a proposal: no file is looked
  for, renamed or created.

A private person, event, citation, source, media object or note is held back: only its ID is listed.
"""

from __future__ import annotations

import re
import unicodedata

from .dates import kind, span
from .tree import BadInput, Tree

EVENT, EVENT_CITATION, PERSON, PERSON_CITATION = "event", "event-citation", "person", "person-citation"
EVENT_SOURCE, PERSON_SOURCE = "event-source", "person-source"
DIRECT = (EVENT, EVENT_CITATION)  # the only levels that can point at the act of the event
CLAIMS = {
    EVENT: "media reference on the event itself; the image was not read",
    EVENT_CITATION: "media reference on a citation of the event; the image was not read",
    PERSON: "media on the person, not on the event: does not show this act",
    PERSON_CITATION: "media on a citation of the person, not of the event: does not show this act",
    EVENT_SOURCE: "media on the source of an event citation: the register, not necessarily this act",
    PERSON_SOURCE: "media on the source of a person citation: the register, not necessarily this act",
}
NO_MEDIA = "none in this dump (a gap in the dump, not proof that no capture exists)"
NO_NOTE = "none in this dump (a gap in the dump, not proof that no transcription exists)"
MARKERS = ("transcripcion literal", "transcription litterale", "literal transcription", "transcripcion paleografica")
UNKNOWN = "unknown"
_ARK = re.compile(r"ark:/?(\d{5,})/([A-Za-z0-9]{4,}(?:[-._][A-Za-z0-9]+)*)", re.I)
_COTE = re.compile(r"\bcote\s*[:.]?\s*([A-Za-z0-9][A-Za-z0-9./_-]*[A-Za-z0-9])", re.I)
_VUE = re.compile(r"\b(?:vue|view|image)\s*(?:n[°o.]?\s*)?[:.]?\s*(\d{1,6})\b", re.I)
CAPS = {"person": 30, "event": 20, "ref": 40}


def fold(s: str) -> str:
    return unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()


def slug(s: str, cap: int) -> str:
    """Letters and digits only, one dash between runs: safe on Windows, macOS and Linux file systems."""
    return re.sub(r"[^a-z0-9]+", "-", fold(s)).strip("-")[:cap].rstrip("-")


def note_text(n: dict) -> str:
    t = n.get("text")
    t = t.get("string") if isinstance(t, dict) else t
    return t if isinstance(t, str) else ""


def ref(t: str, o: dict) -> dict:
    return {"type": t, "gramps_id": o.get("gramps_id") or "", "handle": o["handle"]}


def _held(held: list, t: str, handle: str, why: str, o: dict | None = None) -> None:
    held.append({"type": t, "gramps_id": (o or {}).get("gramps_id") or "", "handle": handle, "why": why})


def _public(tree: Tree, t: str, handles, held: list) -> list[dict]:
    """The objects behind `handles` that are in the dump and not private; the others are listed in `held`."""
    out = []
    for h in handles or []:
        o = tree.objs[t].get(h)
        if o is None:
            _held(held, t, str(h), "not in the dump")
        elif o.get("private"):
            _held(held, t, h, "private", o)
        else:
            out.append(o)
    return out


def _chain(tree: Tree, holder: dict, cit_level: str, src_level: str, held: list) -> list[tuple]:
    out = []
    for c in _public(tree, "citations", holder.get("citation_list"), held):
        out.append((cit_level, "citations", c))
        out += [(src_level, "sources", s) for s in _public(tree, "sources", [c.get("source_handle")], held)
                if c.get("source_handle")]
    return out


def owners(tree: Tree, person: dict, event: dict, held: list) -> list[tuple]:
    """(level, type, object) of everything that can hold a media reference or a note for this person and event."""
    return ([(EVENT, "events", event)] + _chain(tree, event, EVENT_CITATION, EVENT_SOURCE, held)
            + [(PERSON, "people", person)] + _chain(tree, person, PERSON_CITATION, PERSON_SOURCE, held))


def media_rows(tree: Tree, found: list[tuple], held: list) -> tuple[list[dict], list[tuple]]:
    rows, objs = [], []
    for level, t, o in found:
        for r in o.get("media_list") or []:
            if r.get("private"):
                _held(held, "media", str(r.get("ref") or ""), "private media reference")
                continue
            [m] = _public(tree, "media", [r.get("ref")], held) or [None]
            if m is None:
                continue
            rows.append({"level": level, "via": ref(t, o), "field": "media_list", "rect": r.get("rect") or None,
                         "media": {**ref("media", m), "path": m.get("path") or "", "mime": m.get("mime") or "",
                                   "desc": m.get("desc") or ""},
                         "claim": CLAIMS[level]})
            objs.append((level, "media", m))
    return rows, objs


def media_status(rows: list[dict]) -> str:
    direct = {r["media"]["handle"] for r in rows if r["level"] in DIRECT}
    if not rows:
        return NO_MEDIA
    if len(direct) > 1:
        return "ambiguous: several media on the event or its citations; which one is the act is not known"
    if direct:
        return "candidate: a media reference on the event or its citation; unverified, the image was not read"
    return "indirect only: the media sits on the person, a person citation or a source; it does not show an attached act"


def transcript_basis(n: dict) -> str:
    text = note_text(n).strip()
    if kind(n.get("type")) == "transcript":
        return "type-transcript" if text else "type-transcript-empty"
    return "text-marker" if any(m in fold(text) for m in MARKERS) else ""


def note_rows(tree: Tree, found: list[tuple], held: list) -> list[dict]:
    rows = []
    for level, t, o in found:
        for n in _public(tree, "notes", o.get("note_list"), held):
            basis = transcript_basis(n)
            if basis:
                rows.append({"level": level, "via": ref(t, o), "field": "note_list", "basis": basis,
                             "note": {**ref("notes", n), "type": kind(n.get("type"))}})
    return rows


def transcription_status(rows: list[dict]) -> str:
    full = [r for r in rows if r["basis"] == "type-transcript"]
    if any(r["level"] in DIRECT for r in full):
        return "note of type Transcript on the event, its citation or its media; whether it is literal is not checked"
    if any(r["level"] in DIRECT and r["basis"] == "text-marker" for r in rows):
        return "unverified: a note says it is a literal transcription but is not of type Transcript"
    if full or any(r["basis"] == "text-marker" for r in rows):
        return "indirect only: found on the person, a person citation, a source or a media of those; not tied to this act"
    return "only an empty Transcript note" if rows else NO_NOTE


def date_part(event: dict) -> str:
    d = event.get("date") or {}
    v = d.get("dateval")
    if d.get("calendar") or not isinstance(v, list) or len(v) != 4:
        return UNKNOWN
    if any(type(x) is not int for x in v[:3]) or (v[0] and not v[1]):
        return UNKNOWN
    parsed = span(d)
    return parsed.text if parsed is not None and parsed.firm else UNKNOWN


def _texts(o: dict) -> list[tuple[str, str]]:
    keys = ("page", "title", "pubinfo", "abbrev")
    return [(k, o[k]) for k in keys if isinstance(o.get(k), str)] + \
           [("attribute_list", str(a.get("value") or "")) for a in o.get("attribute_list") or [] if isinstance(a, dict) and not a.get("private")]


def candidates(found: list[tuple]) -> dict[str, list[tuple[str, dict]]]:
    """cote, ark and vue as written in the event's own citations and their sources: (value, where it was read)."""
    out: dict[str, list] = {"cote": [], "ark": [], "vue": []}
    for level, t, o in found:
        if level not in (EVENT_CITATION, EVENT_SOURCE):
            continue
        at = ref(t, o)
        for field, text in _texts(o):
            src = {**at, "field": field}
            out["cote"] += [(m[1], src) for m in _COTE.finditer(text)]
            out["ark"] += [(f"{m[1]}-{m[2]}", src) for m in _ARK.finditer(text)]
            out["vue"] += [(m[1].lstrip("0") or "0", src) for m in _VUE.finditer(text)]
        for rr in o.get("reporef_list") or []:
            if rr.get("call_number") and not rr.get("private"):
                out["cote"].append((str(rr["call_number"]).strip(), {**at, "field": "reporef_list.call_number"}))
    return out


def pick(cands: list[tuple[str, dict]]) -> dict:
    values = list(dict.fromkeys(v for v, _ in cands))
    if len(values) > 1:
        return {"value": UNKNOWN, "status": "conflict", "candidates": values}
    if not values:
        return {"value": UNKNOWN, "status": "not found"}
    return {"value": values[0], "status": "found", "from": [w for v, w in cands if v == values[0]]}


def filename_person(person):
    n = person.get("primary_name") or {}
    private_name = n.get("private") or any(x.get("private") for x in n.get("surname_list") or [])
    surname = " ".join(s.get("surname") or "" for s in n.get("surname_list") or [])
    return UNKNOWN if private_name else slug(f"{n.get('first_name') or ''} {surname}", CAPS["person"]) or UNKNOWN


def filename_reference(cote, ark):
    ref_part = UNKNOWN
    if cote["status"] == "found":
        ref_part = "cote-" + (slug(cote["value"], CAPS["ref"] - 5) or UNKNOWN)
    elif cote["status"] == "not found" and ark["status"] == "found":
        ref_part = "ark-" + (slug(ark["value"], CAPS["ref"] - 4) or UNKNOWN)
    return ref_part


def filename(person: dict, event: dict, found: list[tuple]) -> dict:
    cands = candidates(found)
    cote, ark, vue = pick(cands["cote"]), pick(cands["ark"]), pick(cands["vue"])
    person_part = filename_person(person)
    ref_part = filename_reference(cote, ark)
    parts = {"person": person_part,
             "event": slug(kind(event.get("type")), CAPS["event"]) or UNKNOWN,
             "date": date_part(event), "ref": ref_part,
             "vue": f"vue{vue['value']}" if vue["status"] == "found" else UNKNOWN}
    return {"stem": "_".join(parts.values()), "parts": parts, "unknown": [k for k, v in parts.items() if v == UNKNOWN],
            "sources": {"cote": cote, "ark": ark, "vue": vue},
            "note": "a proposal: no file was looked for, renamed or created, and the stem may collide with another file"}


def target(tree: Tree, person: dict, event: dict) -> dict:
    held: list[dict] = []
    found = owners(tree, person, event, held)
    media, media_objs = media_rows(tree, found, held)
    notes = note_rows(tree, found + media_objs, held)
    return {"person": ref("people", person), "event": ref("events", event), "kind": kind(event.get("type")),
            "media": {"status": media_status(media), "rows": media},
            "transcription": {"status": transcription_status(notes), "rows": notes},
            "filename": filename(person, event, found), "held_back": held}


def own_events(tree: Tree, person: dict) -> list[dict]:
    E = tree.objs["events"]
    return [{**E[r["ref"]], "private": True} if r.get("private") else E[r["ref"]]
            for r in person.get("event_ref_list") or []
            if kind(r.get("role")) == "primary" and r.get("ref") in E]


def person_targets(tree, gid, wanted):
    rows = []
    p = tree.by_gid("people", gid)
    if p is None:
        raise BadInput(f"--person {gid}: no such person")
    if p.get("private"):
        rows.append({"person": ref("people", p), "private": True})
        return rows
    evs = [e for e in own_events(tree, p) if not wanted or e.get("gramps_id") in wanted]
    rows += [target(tree, p, e) for e in evs if not e.get("private")]
    rows += [{"person": ref("people", p), "event": ref("events", e), "private": True} for e in evs if e.get("private")]
    if not evs:
        rows.append({"person": ref("people", p), "event": None,
                     "note": "no own (Primary) event of this person matches in the dump"})
    return rows


def media_report(tree: Tree, persons: list[str], events: list[str] | None = None) -> dict:
    if tree.partial:
        raise BadInput(f"{tree.source}: a .gramps backup has no media or note details; use a dump")
    if not persons:
        raise BadInput("act-media needs at least one --person: nothing is searched for by name")
    wanted, rows = set(events or []), []
    for gid in persons:
        rows.extend(person_targets(tree, gid, wanted))
    missing = wanted - {r["event"]["gramps_id"] for r in rows if r.get("event")}
    if missing:
        raise BadInput(f"--event {', '.join(sorted(missing))}: not an own event of the named persons")
    return {"targets": rows, "read_only": True}


def print_media(rep: dict, limit: int = 30) -> None:
    for r in rep["targets"][:limit]:
        who = f"{r['person']['gramps_id']}" + (f" {r['event']['gramps_id']} ({r['kind']})" if r.get("kind") else "")
        if r.get("private"):
            print(f"{who} (marked private: held back)")
            continue
        if r.get("note"):
            print(f"{who}: {r['note']}")
            continue
        print(who)
        print(f"  media: {r['media']['status']}")
        for m in r["media"]["rows"]:
            print(f"    [{m['level']}] {m['media']['gramps_id']} {m['media']['path']} on {m['via']['type']} "
                  f"{m['via']['gramps_id']}")
        print(f"  transcription: {r['transcription']['status']}")
        for n in r["transcription"]["rows"]:
            print(f"    [{n['level']}] {n['note']['gramps_id']} {n['basis']} on {n['via']['type']} {n['via']['gramps_id']}")
        f = r["filename"]
        print(f"  filename proposal: {f['stem']}" + (f"  (unknown: {', '.join(f['unknown'])})" if f["unknown"] else ""))
        if r["held_back"]:
            print(f"  held back or not in the dump: {len(r['held_back'])} (--json lists them)")
    if len(rep["targets"]) > limit:
        print(f"first {limit} of {len(rep['targets'])} rows (--limit; --json has all)")
    print("report only: media linkage is not read from the image and proves no act; nothing was renamed or changed")
