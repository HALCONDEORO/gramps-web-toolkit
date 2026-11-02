"""Whole-tree checks. Each finding is (check, key, detail); the key is stable across runs."""

from __future__ import annotations

import datetime
import re
import unicodedata
from collections import defaultdict

from . import dates, homonyms
from .tree import Tree

# list of handles -> type they point at
HANDLE_LISTS = {
    "citation_list": "citations",
    "note_list": "notes",
    "family_list": "families",
    "parent_family_list": "families",
    "tag_list": "tags",
}
# list of {"ref": handle, ...} -> type they point at
REF_LISTS = {
    "event_ref_list": "events",
    "child_ref_list": "people",
    "person_ref_list": "people",
    "media_list": "media",
    "placeref_list": "places",
    "reporef_list": "repositories",
}
# single handle -> type
SCALARS = {
    "father_handle": "people",
    "mother_handle": "people",
    "source_handle": "sources",
    "place": "places",
    "famc": "families",
}
# what makes two refs in one list "the same ref twice"
REF_IDENTITY = {
    "event_ref_list": ("ref", "role"),
    "media_list": ("ref", "rect"),
    "person_ref_list": ("ref", "rel"),
}
SKIP_KEYS = {"text", "extended", "profile", "backlinks", "date"}
VITAL = {"Birth", "Death", "Baptism", "Burial", "Marriage"}
# left out when one name is compared with another (after norm(), so «y» is already «i»)
PARTICLES = {"de", "del", "la", "las", "los", "i", "e"}

CHECKS = {
    "same-person-event": "same person with matching stored event type, date, place and role; review only",
    "same-person-citation": "same owner on one person with matching citation source, literal page and date; review only",
    "orphan-event": "event no person or family refers to",
    "orphan-citation": "citation nothing refers to",
    "orphan-note": "note nothing refers to",
    "orphan-media": "media object nothing refers to",
    "orphan-repository": "repository no source lists",
    "dangling-ref": "reference to an object that does not exist",
    "place-not-place": "a place field that holds something other than a place",
    "citation-no-source": "citation without a source",
    "birth-death-pointer": "birth or death pointer wrong, out of range or missing",
    "repeated-ref": "the same reference twice in one list",
    "family-backlink": "person and family disagree about who is in it",
    "empty-family": "family with no father, mother or child",
    "dup-couple": "two families with the same father and mother",
    "dup-couple-names": "two families whose couples have the same two names",
    "homonym-birth-gap": "two people with the same name whose possible birth dates are more than 10 years apart; "
                         "not a safe merge candidate",
    "dup-person-birth": "two people with the same name and a birth or baptism in the same year",
    "dup-person-relative": "two people with the same name and a parent, spouse or child of the same name",
    "dup-person-fuzzy": "two people whose names differ by an extra given name or surname, born within 2 years "
                        "or with a parent, spouse or child of the same name",
    "dup-vital-event": "one person or family with two vital events of one type",
    "living-media-sensitive": "media of a possibly living person, not marked private, whose path, description or "
                              "attributes look like an ID document, passport or home address",
    "dup-repository": "two repositories with the same name, compared like people's names",
    "dup-media": "two media objects with the same file: the same checksum",
    "dup-citation": "two citations of one source with the same page text, ignoring case and spacing",
    "dup-source-ark": "two sources that name the same archive ark (in the title, abbreviation, publication "
                      "info, a note or an attribute), written as `ark:/NAAN/id` or `ark NAAN/id`",
    "date-modifier-note": "opposite before/after assertions at the same boundary in an event date or its "
                          "event/citation notes; review only, no corrections",
    "date-false-precision": "an event date that hedges twice (`about` and `estimated`) or marks a date with a day "
                            "as `estimated`, so it reads as more exact than the source made it",
    "private": "object or sub-reference marked private",
    "place-no-coords": "place used by an event without coordinates",
    "place-vs-description": "an event whose description names another place of the tree and not its own place "
                            "(or one enclosing it), not as a name of someone on the event or as where someone is "
                            "from or lives",
    "surname-marker": "surname with a « [ » marker",
    "note-marker-no-tag": "note whose text says «hipótesis» or «homónimo» but lacks the tag of that name, in a "
                          "tree that has such a tag",
    "missing-object": "object of the earlier tree that is gone",
    "ahnentafel-lost": "Ahnentafel number of the earlier tree that is gone",
    "ahnentafel-changed": "Ahnentafel number that now holds another person",
    "ahnentafel-truncated": "pedigree too large to compare whole; only the generations named were compared",
    "unconnected": "family component with no family link to the root's component (or the largest one)",
    "event-order": "a person's own dates in an impossible order: death before birth or baptism, baptism before "
                   "birth, burial before death",
    "child-before-parent": "child born before a parent",
    "child-after-parent-death": "child born after the mother's death, or more than 300 days after the father's",
    "marriage-before-birth": "marriage before a spouse was born",
    "marriage-after-death": "marriage after a spouse's death",
    "own-ancestor": "a person who is their own ancestor",
    "parent-age": "mother under 14 or over 52, or father under 14 or over 85, at a child's birth",
    "marriage-age": "bride under 12 or groom under 14 at the marriage",
    "sibling-spacing": "two children of one mother born less than 240 days apart, not twins",
    "lifespan": "a life of more than 105 years",
}

# Checks reported without failing: a finding of these is never new, so it never sets the exit code.
INFO_CHECKS = {"same-person-event", "same-person-citation", "homonym-birth-gap", "unconnected", "parent-age", "marriage-age", "sibling-spacing", "lifespan", "dup-citation",
               "dup-source-ark", "date-false-precision", "place-vs-description", "note-marker-no-tag"}

# words in a note that a tag of the same stem is meant to mark; matched without accents, whole word
NOTE_MARKERS = ("hipotesis", "homonimo")
# a marker with one of these words within three words after it («hipótesis descartada») or before it
# («queda descartada la hipótesis») was discarded or replaced and no longer needs its tag. A «no», «ni»,
# «sin» or «nada» between them denies the discard: «hipótesis no descartada» is still open. All in one sentence
_DISCARD = r"(?:descartad|refutad|superad|sustituid|desmentid)[oa]s?"
_SEP = r"[^\w.;:!?]+"                      # the words must sit in one sentence
_GAP = rf"(?:(?!(?:no|ni|sin|nada)\b)\w+{_SEP}){{0,3}}"
NOTE_DISCARDED_AFTER = re.compile(rf"{_SEP}{_GAP}{_DISCARD}\b")
NOTE_DISCARDED_BEFORE = re.compile(rf"\b{_DISCARD}{_SEP}{_GAP}$")

# words in a note that a tag of the same stem is meant to mark; matched without accents, whole word
NOTE_MARKERS = ("hipotesis", "homonimo")
# a note that says the hypothesis was discarded or replaced no longer needs its tag
NOTE_DISCARDED = re.compile(r"(?<!sin )\b(?:descartad[oa]s?|refutad[oa]s?|superad[oa]s?|sustituid[oa]s?|desmentid[oa]s?)\b")

# place-vs-description: a place named as where a person comes from or lives, not where the event happened.
# Words that open such a phrase («natural de», «vecino del lugar de», «domicilié à»), compared by their start
# («vecino», «domiciliado»; never «vecindad» or «domicilio», as in «murió en su domicilio de X»)
ORIGIN_WORDS = ("natural", "vecino", "vecina", "vezino", "vezina", "residente", "domicilie", "domiciliad",
                "demeurant", "originaire", "habitant")
# words that may stand between such a word, or a «de» that opens the clause, and the place name
ORIGIN_FILLER = {"de", "del", "d", "la", "el", "l", "lugar", "villa", "ciudad", "commune", "en", "a", "au"}
# marks after which a «de» opens a clause («Juan, de Otropueblo»); a dot is not one, it may end «parr.» or «Sra.»
ORIGIN_CLAUSE_BREAK = {",", ";", ":", "(", "«", "»", '"', "“", "”"}

# limits of the dated checks
POSTHUMOUS_DAYS = 300                       # a child can be born this long after the father's death
PARENT_AGE = {"mother": (14, 52), "father": (14, 85)}
MARRIAGE_AGE = {0: 12, 1: 14}               # by gender; canon law allowed girls 12 and boys 14 until 1917
TWIN_DAYS = 1
MIN_SPACING = 240
MAX_LIFESPAN = 105
# child_ref relations whose parent is taken as the biological one
BIOLOGICAL = {"", "birth", "unknown"}


# What gives away an identity document or a home address. Metadata only: the check cannot read pixels.
# Spanish first. Another country adds its ID number as a function to ID_NUMBERS (with its check digit, so
# a dated file name does not match) and its words to ID_WORDS.
def spanish_id(text: str) -> bool:
    """A DNI (8 digits) or NIE (X, Y or Z and 7 digits) whose check letter is right."""
    for m in re.finditer(r"(?<![A-Za-z0-9])([XYZ]\d{7}|\d{8})([A-Z])(?![A-Za-z0-9])", text, re.I):
        n = m.group(1).upper()
        n = str("XYZ".index(n[0])) + n[1:] if n[0] in "XYZ" else n
        if "TRWAGMYFPDXBNJZSQVHLCKE"[int(n) % 23] == m.group(2).upper():
            return True
    return False


ID_NUMBERS = (spanish_id,)
ID_WORDS = re.compile(r"\b(?:pasaporte|passport|domicilio|empadronamiento|carnet de identidad|identity card|"
                      r"id card|carte d.identite|home address)\b|(?<![\w/])c/ ?[A-Za-z]", re.I)
SENSITIVE_PATTERNS = (
    ("id number", lambda s: any(f(s) for f in ID_NUMBERS)),
    ("id or address word", ID_WORDS.search),
)
# a person with no death event is presumed dead once born this many years ago or more; younger, or never dated, is
# taken as possibly living
LIVING_YEARS = 100


def sensitive_kind(*texts: str) -> str:
    """Name the first kind of sensitive text found, never the text itself."""
    for text in texts:
        flat = "".join(c for c in unicodedata.normalize("NFD", text or "") if unicodedata.category(c) != "Mn")
        for kind, found in SENSITIVE_PATTERNS:
            if found(flat):
                return kind
    return ""


def norm(s: str) -> str:
    """Lowercase, no accents, b/v and i/y folded, the particle «de» dropped."""
    s = "".join(c for c in unicodedata.normalize("NFD", s.lower()) if unicodedata.category(c) != "Mn")
    s = f" {' '.join(s.split())} ".replace(" de ", " ").replace("y", "i").replace("b", "v")
    return " ".join(s.split())


def possibly_living(p: dict, events: dict, this_year: int, *, undated: bool = True) -> bool:
    """No death or burial of their own, and a birth or baptism of their own less than LIVING_YEARS ago. A person
    with no dated birth or baptism counts only when `undated` is true. Their own events only: a godparent at a
    2001 baptism was not born in 2001."""
    own = [events.get(r.get("ref")) for r in p.get("event_ref_list", []) if r.get("role") == "Primary"]
    if any(e and e.get("type") in ("Death", "Burial") for e in own):
        return False
    years = [((e.get("date") or {}).get("dateval") or [0, 0, 0])[2] for e in own
             if e and e.get("type") in ("Birth", "Baptism")]
    years = [y for y in years if y]
    if not years:
        return undated
    return min(years) >= this_year - LIVING_YEARS


def person_name(p: dict) -> str:
    n = p.get("primary_name") or {}
    return norm(" ".join([n.get("first_name", "")] + [s.get("surname", "") for s in n.get("surname_list", [])]))


class Findings:
    def __init__(self):
        self.items: list[dict] = []
        self._seen: set[tuple[str, str]] = set()
        self.duplicate_guard = lambda key: False

    def add(self, check: str, key: str, detail: str = "") -> None:
        if check in {"dup-person-birth", "dup-person-relative"} and self.duplicate_guard(key):
            return
        if (check, key) not in self._seen:
            self._seen.add((check, key))
            self.items.append({"check": check, "key": key, "detail": detail})


def id_order(gid: str) -> tuple:
    """Gramps IDs in numeric order: I9001 before I10000. The ID itself breaks a tie (O01 and O001), so a finding key
    does not depend on the order the dump lists the objects in."""
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", gid)], gid


# `ark:/12345/abc123` or the written-out `ark 12345/abc123`. The id is the whole run of characters up to a space or a
# `/` (a view or a path follows a `/`), less a closing stop or bracket. It is taken only when it is at least 8 letters
# or digits, joined by single `-`, `.`, `_`, `=`, `~`, `*`, `+`, `@` or `$`. Anything else (`--`, `%2D`, a stray mark)
# is not read as an ark at all, and is never cut down to a prefix that could match another ark.
_ARK = re.compile(r"\bark(?::/?|\s+)(\d{4,9})/([^\s/]+)", re.I)
_ARK_ID = re.compile(r"[A-Za-z0-9]{8,}(?:[-._=~*+@$][A-Za-z0-9]+)*")


def source_arks(src: dict, notes: dict) -> set[tuple[str, str]]:
    """The archive arks a source names, by its own text, its attributes and its notes."""
    texts = [src.get("title") or "", src.get("abbrev") or "", src.get("pubinfo") or ""]
    texts += [str(a.get("value") or "") for a in src.get("attribute_list", [])]
    for h in src.get("note_list", []):
        t = (notes.get(h) or {}).get("text")
        texts.append(t.get("string", "") if isinstance(t, dict) else str(t or ""))
    out = set()
    for m in _ARK.finditer("\n".join(texts)):
        name = m[2].rstrip(".,;:!?)]}'\"")
        if _ARK_ID.fullmatch(name):
            out.add((m[1], name.lower()))
    return out


def source_ark_checks(T: dict, out: Findings, in_scope) -> None:
    """One register entered as two sources: the same archive ark in both. Sources made one per act can share a
    register on purpose, so this is for information only."""
    by_ark: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for src in T["sources"].values():
        for ark in source_arks(src, T["notes"]):
            by_ark[ark].append(src)
    for (naan, name), ss in by_ark.items():
        if len(ss) > 1 and any(in_scope("sources", x["handle"]) for x in ss):
            out.add("dup-source-ark", ",".join(sorted((x.get("gramps_id") or x["handle"] for x in ss), key=id_order)),
                    f"ark {naan}/{name}")


def date_precision_checks(T: dict, out: Findings, in_scope) -> None:
    """A date copied from a table or computed from an age: "about" and "estimated" together hedge twice, and an
    "estimated" date that still has a day claims a precision nobody read. For information only."""
    for e in T["events"].values():
        dt = e.get("date") or {}
        if dt.get("quality") != 1 or not in_scope("events", e["handle"]):
            continue
        v = dt.get("dateval") or [0, 0, 0, False]
        if dt.get("modifier") == 3:
            out.add("date-false-precision", e.get("gramps_id") or e["handle"], "about and estimated together")
        elif v[0]:
            out.add("date-false-precision", e.get("gramps_id") or e["handle"], "estimated, yet it names a day")


def hygiene_checks(T: dict, out: Findings, in_scope) -> None:
    """The checks for information only that read sources and dates; one call in `run_checks`."""
    source_ark_checks(T, out, in_scope)
    date_precision_checks(T, out, in_scope)
    homonyms.install(T, out, in_scope, person_name, id_order)


def check_vital_pointers(T, out):
    # birth and death pointers
    E = T["events"]
    for p in T["people"].values():
        erl = p.get("event_ref_list", [])
        for field, typ in (("birth_ref_index", "Birth"), ("death_ref_index", "Death")):
            i = p.get(field, -1)
            primaries = [r for r in erl if r.get("role") == "Primary" and E.get(r.get("ref"), {}).get("type") == typ]
            gid = p.get("gramps_id")
            if i == -1:
                if primaries:
                    out.add("birth-death-pointer", f"{gid} {field}", f"Primary {typ} but the pointer is -1")
            elif not 0 <= i < len(erl):
                out.add("birth-death-pointer", f"{gid} {field}", f"{i} is out of range")
            else:
                r = erl[i]
                e = E.get(r.get("ref"))
                fallbacks = ({"Baptism", "Christening", "Stillbirth"} if typ == "Birth" else
                             {"Burial", "Cremation", "Cause Of Death", "Probate", "Stillbirth"})
                accepted = {typ} if primaries else {typ}.union(fallbacks)
                if not e or e.get("type") not in accepted or r.get("role") != "Primary":
                    got = f"{e.get('gramps_id')} {e.get('type')} {r.get('role')}" if e else "a missing event"
                    out.add("birth-death-pointer", f"{gid} {field}", f"points at {got}")



class _Refs:
    """What the reference walk shares: the tree, the findings, and the handles found referenced, by type."""

    def __init__(self, tree: Tree, out: Findings):
        self.tree = tree
        self.T = tree.objs
        self.out = out
        self.referenced: dict[str, set] = defaultdict(set)


def _label(T: dict, t: str, h: str) -> str:
    return T[t][h].get("gramps_id") or h if h in T[t] else h


def _mark_ref(w: _Refs, owner: str, p: str, t: str, h) -> None:
    if h in w.T[t]:
        w.referenced[t].add(h)
    else:
        w.out.add("dangling-ref", f"{owner} {p} -> {t} {h}")


def _walk_handle_list(w: _Refs, owner: str, p: str, k: str, v: list) -> None:
    t = HANDLE_LISTS[k]
    if t == "tags" and not w.tree.has_tags:
        return
    seen = set()
    for h in v:
        if h in seen:
            w.out.add("repeated-ref", f"{owner} {p} {_label(w.T, t, h)}")
        seen.add(h)
        _mark_ref(w, owner, p, t, h)


def _walk_ref_list(w: _Refs, owner: str, p: str, k: str, v: list) -> None:
    t = REF_LISTS[k]
    ident = REF_IDENTITY.get(k, ("ref",))
    seen = set()
    for r in v:
        if not isinstance(r, dict):
            continue
        h = r.get("ref")
        sig = tuple(repr(r.get(f)) for f in ident)
        if sig in seen:
            w.out.add("repeated-ref", f"{owner} {p} {_label(w.T, t, h)}")
        seen.add(sig)
        _mark_ref(w, owner, p, t, h)
        _walk(w, r, owner, f"{p}[{_label(w.T, t, h)}]")


def _walk_scalar(w: _Refs, owner: str, p: str, k: str, v) -> None:
    t = SCALARS[k]
    if not v:
        if k == "source_handle":
            w.out.add("citation-no-source", owner)
    elif v in w.T[t]:
        w.referenced[t].add(v)
    elif k == "place":
        other = next((tt for tt in w.T if v in w.T[tt]), None)
        w.out.add("place-not-place", f"{owner} {p}", f"holds {other or 'nothing'} {v}")
    else:
        w.out.add("dangling-ref", f"{owner} {p} -> {t} {v}")


def _walk_item(w: _Refs, owner: str, p: str, k: str, v) -> None:
    if k in HANDLE_LISTS and isinstance(v, list):
        _walk_handle_list(w, owner, p, k, v)
    elif k in REF_LISTS and isinstance(v, list):
        _walk_ref_list(w, owner, p, k, v)
    elif k in SCALARS:
        _walk_scalar(w, owner, p, k, v)
    elif k not in SKIP_KEYS and isinstance(v, (dict, list)):
        _walk(w, v, owner, p)


def _walk(w: _Refs, x, owner: str, path: str) -> None:
    if isinstance(x, list):
        for y in x:
            _walk(w, y, owner, path)
        return
    if not isinstance(x, dict):
        return
    if x.get("private") is True:
        w.out.add("private", f"{owner} {path or '(object)'}")
    for k, v in x.items():
        _walk_item(w, owner, f"{path}.{k}" if path else k, k, v)


def collect_references(tree: Tree, out: Findings) -> dict[str, set]:
    """Walk every object: private, repeated, dangling and misplaced references. Returns the referenced handles."""
    w = _Refs(tree, out)
    for objs in w.T.values():
        for o in objs.values():
            _walk(w, o, o.get("gramps_id") or o["handle"], "")
    return w.referenced


_ORPHAN_TYPES = (("events", "orphan-event"), ("citations", "orphan-citation"),
                 ("notes", "orphan-note"), ("media", "orphan-media"), ("repositories", "orphan-repository"))


def _event_owners(T: dict) -> set[str]:
    owners: set[str] = set()
    for t in ("people", "families"):
        for o in T[t].values():
            owners.update(r.get("ref") for r in o.get("event_ref_list", []))
    return owners


def orphan_checks(T: dict, out: Findings, referenced: dict[str, set]) -> None:
    # orphans: an event must hang from a person or a family, not only from a citation's backlink
    event_owner = _event_owners(T)
    for t, check in _ORPHAN_TYPES:
        owners = event_owner if t == "events" else referenced[t]
        for h, o in T[t].items():
            if h not in owners:
                out.add(check, o.get("gramps_id") or h)


def repository_info(T: dict, info: dict) -> None:
    # repositories: where each source is kept. A source is linked only when its repository is certain, so a
    # source without one is counted, not a finding; a tree with no repository at all does not use them.
    if T["repositories"]:
        info["sources without repository"] = sum(1 for o in T["sources"].values() if not o.get("reporef_list"))


def dup_media_checks(T: dict, out: Findings, in_scope) -> None:
    # the same file entered twice: Gramps keeps an md5 checksum per media object, the same identity gwt-apply's
    # add_media uses to skip a file already in the tree. Empty means the file was never read: nothing to compare
    by_checksum: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for h, o in T["media"].items():
        if o.get("checksum"):
            by_checksum[o["checksum"]].append((h, o.get("gramps_id") or h))
    for group in by_checksum.values():
        if len(group) > 1 and any(in_scope("media", h) for h, _ in group):
            out.add("dup-media", ",".join(sorted((i for _, i in group), key=id_order)), "same checksum")


def dup_repository_checks(T: dict, out: Findings) -> None:
    by_name: dict[str, list[str]] = defaultdict(list)
    for h, o in T["repositories"].items():
        if norm(o.get("name") or ""):
            by_name[norm(o["name"])].append(o.get("gramps_id") or h)
    for ids in by_name.values():
        if len(ids) > 1:
            out.add("dup-repository", ",".join(sorted(ids)))


def dup_citation_checks(T: dict, out: Findings, in_scope) -> None:
    # citations of one record entered twice: same source, same page. Not folded further (no b/v, no accents),
    # since "folio 12v" and "folio 12" are different pages
    by_page: dict[tuple, list[dict]] = defaultdict(list)
    for c in T["citations"].values():
        page = " ".join((c.get("page") or "").casefold().split())
        if page and c.get("source_handle"):
            by_page[(c["source_handle"], page)].append(c)
    for cs in by_page.values():
        if len(cs) > 1 and any(in_scope("citations", c["handle"]) for c in cs):
            out.add("dup-citation", ",".join(sorted((c.get("gramps_id") or c["handle"] for c in cs), key=id_order)))


def _family_parent_links(T: dict, out: Findings, f: dict) -> None:
    P = T["people"]
    fid = f.get("gramps_id")
    for k in ("father_handle", "mother_handle"):
        h = f.get(k)
        if h in P and f["handle"] not in P[h].get("family_list", []):
            out.add("family-backlink", f"{fid} {k} {_label(T, 'people', h)}", "missing from the person's family_list")


def _family_child_links(T: dict, out: Findings, f: dict) -> None:
    P = T["people"]
    fid = f.get("gramps_id")
    for c in f.get("child_ref_list", []):
        h = c.get("ref")
        if h in P and f["handle"] not in P[h].get("parent_family_list", []):
            out.add("family-backlink", f"{fid} child {_label(T, 'people', h)}", "missing from the person's parent_family_list")


def _family_side_backlinks(T: dict, out: Findings, f: dict) -> None:
    _family_parent_links(T, out, f)
    _family_child_links(T, out, f)
    if not f.get("father_handle") and not f.get("mother_handle") and not f.get("child_ref_list"):
        out.add("empty-family", f.get("gramps_id"))


def _person_side_backlinks(T: dict, out: Findings, p: dict) -> None:
    F = T["families"]
    pid = p.get("gramps_id")
    for fh in p.get("family_list", []):
        f = F.get(fh)
        if f and p["handle"] not in (f.get("father_handle"), f.get("mother_handle")):
            out.add("family-backlink", f"{pid} family_list {f.get('gramps_id')}", "not the father or mother there")
    for fh in p.get("parent_family_list", []):
        f = F.get(fh)
        if f and p["handle"] not in [c.get("ref") for c in f.get("child_ref_list", [])]:
            out.add("family-backlink", f"{pid} parent_family_list {f.get('gramps_id')}", "no child_ref there")


def family_backlink_checks(T: dict, out: Findings) -> None:
    # person <-> family, both ways
    for f in T["families"].values():
        _family_side_backlinks(T, out, f)
    for p in T["people"].values():
        _person_side_backlinks(T, out, p)


def _couples(T: dict) -> tuple[dict, dict]:
    P = T["people"]
    by_handles: dict[tuple, list] = defaultdict(list)
    by_names: dict[tuple, list] = defaultdict(list)
    for f in T["families"].values():
        a, b = f.get("father_handle"), f.get("mother_handle")
        if a in P and b in P:
            by_handles[(a, b)].append(f)
            by_names[(person_name(P[a]), person_name(P[b]))].append(f)
    return by_handles, by_names


def couple_checks(T: dict, out: Findings, in_scope) -> None:
    by_handles, by_names = _couples(T)
    for fs in by_handles.values():
        if len(fs) > 1:  # exact, so whole tree like the structural checks
            out.add("dup-couple", ",".join(sorted(f["gramps_id"] for f in fs)))
    for (na, nb), fs in by_names.items():
        if not na.strip() or not nb.strip():
            continue
        distinct = {(f["father_handle"], f["mother_handle"]) for f in fs}
        if len(distinct) > 1 and any(in_scope("families", f["handle"]) for f in fs):
            out.add("dup-couple-names", ",".join(sorted(f["gramps_id"] for f in fs)), f"{na} × {nb}")


# people with the same name
def _births(E: dict, p: dict) -> set[tuple[int, int, int]]:
    got = set()
    for r in p.get("event_ref_list", []):
        e = E.get(r.get("ref"))
        if not e or r.get("role") != "Primary" or e.get("type") not in ("Birth", "Baptism"):
            continue
        d = e.get("date") or {}
        dv = d.get("dateval") or [0, 0, 0]
        # plain and "about" dates only: "before 1800" says nothing about 1800
        if d.get("modifier", 0) in (0, 3) and dv[2]:
            got.add((dv[2], dv[1], dv[0]))
    return got


def _compatible(xs, ys) -> bool:
    return any(x[0] == y[0] and (not x[1] or not y[1] or x[1] == y[1]) and (not x[2] or not y[2] or x[2] == y[2])
               for x in xs for y in ys)


def _parent_names(T: dict, p: dict) -> set:
    P, F = T["people"], T["families"]
    got: set = set()
    for fh in p.get("parent_family_list", []):
        f = F.get(fh) or {}
        got |= {person_name(P[h]) for h in (f.get("father_handle"), f.get("mother_handle")) if h in P}
    return got


def _spouse_names(T: dict, p: dict) -> set:
    P, F = T["people"], T["families"]
    got: set = set()
    for fh in p.get("family_list", []):
        f = F.get(fh) or {}
        got |= {person_name(P[h]) for h in (f.get("father_handle"), f.get("mother_handle"))
                if h in P and h != p["handle"]}
    return got


def _child_names(T: dict, p: dict) -> set:
    P, F = T["people"], T["families"]
    got: set = set()
    for fh in p.get("family_list", []):
        f = F.get(fh) or {}
        got |= {person_name(P[c.get("ref")]) for c in f.get("child_ref_list", []) if c.get("ref") in P}
    return got


def _kin(T: dict, p: dict) -> tuple[set, set, set]:
    return (_parent_names(T, p) - {""}, _spouse_names(T, p) - {""}, _child_names(T, p) - {""})


def _parent_handles(T: dict, p: dict) -> set:
    P, F = T["people"], T["families"]
    out_: set = set()
    for fh in p.get("parent_family_list", []):
        f = F.get(fh) or {}
        out_ |= {h for h in (f.get("father_handle"), f.get("mother_handle")) if h in P}
    return out_


def _parent_and_child(T: dict, a: str, b: str) -> bool:
    """A person is never their own parent: a father and a son with one name are two people."""
    P = T["people"]
    return a in _parent_handles(T, P[b]) or b in _parent_handles(T, P[a])


def _kin_why(pp: set, pq: set, sp: set, sq: set, _cp: set, _cq: set) -> str:
    return "same parent" if pp & pq else "same spouse" if sp & sq else "same child"


def _years_differ(bp: set, bq: set):
    return bp and bq and not ({x[0] for x in bp} & {x[0] for x in bq})


def _same_name_pair(T: dict, out: Findings, n: str, facts: dict, p: dict, q: dict) -> None:
    bp, pp, sp, cp = facts[p["handle"]]
    bq, pq, sq, cq = facts[q["handle"]]
    pair = ",".join(sorted((p["gramps_id"], q["gramps_id"])))
    if bp and bq and _compatible(bp, bq):
        out.add("dup-person-birth", pair, n)
    # a shared relative proves nothing when one is the other's parent; a shared birth date still does
    if (pp & pq or sp & sq or cp & cq) and not _years_differ(bp, bq) \
            and not _parent_and_child(T, p["handle"], q["handle"]):
        out.add("dup-person-relative", pair, f"{n}, {_kin_why(pp, pq, sp, sq, cp, cq)}")


def _name_groups(P: dict) -> dict[str, list]:
    names: dict[str, list] = defaultdict(list)
    for p in P.values():
        n = person_name(p)
        if len(n.split()) >= 2:  # a lone first name matches half the tree
            names[n].append(p)
    return names


def same_name_checks(T: dict, out: Findings, in_scope) -> None:
    E = T["events"]
    for n, group in _name_groups(T["people"]).items():
        if len(group) < 2 or not any(in_scope("people", p["handle"]) for p in group):
            continue
        facts = {p["handle"]: (_births(E, p), *_kin(T, p)) for p in group}
        for i, p in enumerate(group):
            for q in group[i + 1:]:
                if not (in_scope("people", p["handle"]) or in_scope("people", q["handle"])):
                    continue
                _same_name_pair(T, out, n, facts, p, q)


# the same person written with an extra given name or surname: «Vicente» and «Miguel Vicente»
def _tokens(s: str) -> frozenset:
    return frozenset(t for t in norm(s).split() if t not in PARTICLES)


def _name_parts(p: dict) -> tuple[frozenset, frozenset]:
    n = p.get("primary_name") or {}
    surnames = " ".join(s.get("surname", "") for s in n.get("surname_list", []))
    return _tokens(n.get("first_name", "")), _tokens(surnames)


def _name_index(P: dict) -> tuple[dict, dict, dict]:
    parts = {h: _name_parts(p) for h, p in P.items()}
    by_surname: dict[str, list] = defaultdict(list)
    by_given: dict[str, list] = defaultdict(list)
    for h, (g, s) in parts.items():
        if g and s:
            for t in s:
                by_surname[t].append(h)
            for t in g:
                by_given[t].append(h)
    return parts, by_surname, by_given


def _fact(T: dict, facts: dict, h: str) -> tuple:
    if h not in facts:
        P = T["people"]
        facts[h] = ({x[0] for x in _births(T["events"], P[h])}, *_kin(T, P[h]))
    return facts[h]


def _parents_by_name(T: dict, h: str) -> dict[str, set]:
    P, F = T["people"], T["families"]
    got: dict[str, set] = defaultdict(set)
    for fh in P[h].get("parent_family_list", []):
        f = F.get(fh) or {}
        for x in (f.get("father_handle"), f.get("mother_handle")):
            if x in P:
                got[person_name(P[x])].add(x)
    return got


def _is_birth_child(c: dict, h: str) -> bool:
    return c.get("ref") == h and dates.kind(c.get("frel")) in BIOLOGICAL \
        and dates.kind(c.get("mrel")) in BIOLOGICAL


def _parent_pair_names(P: dict, f: dict) -> tuple[str, str] | None:
    fa, mo = (person_name(P[x]) if x in P else ""
              for x in (f.get("father_handle"), f.get("mother_handle")))
    return (fa, mo) if fa and mo else None


def _birth_parents(T: dict, h: str) -> tuple[str, str] | None:
    """Names of the father and mother of the first family the person is a birth child of, both named."""
    P, F = T["people"], T["families"]
    for fh in P[h].get("parent_family_list", []):
        f = F.get(fh) or {}
        for c in f.get("child_ref_list", []):
            if _is_birth_child(c, h):
                return _parent_pair_names(P, f)
    return None


def _touched(T: dict, in_scope, h: str) -> bool:
    """In scope, or in a family that is: an old person the run gave a new spouse or parents."""
    p = T["people"][h]
    return in_scope("people", h) or any(
        in_scope("families", fh) for fh in p.get("family_list", []) + p.get("parent_family_list", []))


def _fuzzy_candidates(parts: dict, by_surname: dict, by_given: dict, h: str) -> set:
    g, s = parts[h]
    # a match shares a surname word and a given-name word: walk the shorter index, test the other
    by_s, by_g = sum(len(by_surname[t]) for t in s), sum(len(by_given[t]) for t in g)
    pool = (by_surname[t] for t in s) if by_s <= by_g else (by_given[t] for t in g)
    return {c for lst in pool for c in lst if parts[c][0] & g and parts[c][1] & s} - {h}


def _fuzzy_related(g: frozenset, s: frozenset, gc: frozenset, sc: frozenset) -> bool:
    if (g, s) == (gc, sc):
        return False
    return (g <= gc or gc <= g) and (s <= sc or sc <= s)


def _fuzzy_why(T: dict, facts: dict, h: str, c: str) -> str | None:
    yp, pp, sp, cp = _fact(T, facts, h)
    yq, pq, sq, cq = _fact(T, facts, c)
    near = any(abs(a - b) <= 2 for a in yp for b in yq)
    # siblings share their parents: a parent counts only when each side has its own copy of them
    if pp & pq:
        ph, qh = _parents_by_name(T, h), _parents_by_name(T, c)
        pp = {n for n in pp & pq if ph[n] != qh[n]}
    kin_shared = (pp & pq or sp & sq or cp & cq) and not _parent_and_child(T, h, c)
    if near or (kin_shared and not (yp and yq)):
        return "birth within 2 years" if near else _kin_why(pp, pq, sp, sq, cp, cq)
    return None


def _fuzzy_pair(T: dict, out: Findings, facts: dict, skipped: set, h: str, c: str) -> None:
    why = _fuzzy_why(T, facts, h, c)
    if why is None:
        return
    P = T["people"]
    pair = ",".join(sorted((P[h]["gramps_id"], P[c]["gramps_id"])))
    # namesakes: each has a father and a mother, and both differ by name, so two people
    bh, bc = _birth_parents(T, h), _birth_parents(T, c)
    if bh and bc and bh[0] != bc[0] and bh[1] != bc[1]:
        skipped.add(pair)
        return
    out.add("dup-person-fuzzy", pair, f"{person_name(P[h])} / {person_name(P[c])}, {why}")


def fuzzy_person_checks(T: dict, out: Findings, in_scope, info: dict) -> None:
    parts, by_surname, by_given = _name_index(T["people"])
    facts: dict[str, tuple] = {}
    skipped: set[str] = set()
    for h, (g, s) in parts.items():
        if not (g and s and _touched(T, in_scope, h)):
            continue
        for c in _fuzzy_candidates(parts, by_surname, by_given, h):
            gc, sc = parts[c]
            if not _fuzzy_related(g, s, gc, sc):
                continue
            if _touched(T, in_scope, c) and c < h:  # both in scope: look at the pair once
                continue
            _fuzzy_pair(T, out, facts, skipped, h, c)
    info["dup-person-fuzzy skipped, parents differ by name"] = sorted(skipped, key=id_order)


def _vital_by_type(E: dict, o: dict, role: str) -> dict[str, list]:
    by_type: dict[str, list] = defaultdict(list)
    for r in o.get("event_ref_list", []):
        e = E.get(r.get("ref"))
        if e and e.get("type") in VITAL and r.get("role") == role:
            by_type[e["type"]].append(e)
    return by_type


def dup_vital_event_checks(T: dict, out: Findings, in_scope) -> None:
    # two vital events of one type on one person or family
    E = T["events"]
    for t, role in (("people", "Primary"), ("families", "Family")):
        for o in T[t].values():
            for typ, es in _vital_by_type(E, o, role).items():
                if len(es) > 1 and (in_scope(t, o["handle"]) or any(in_scope("events", e["handle"]) for e in es)):
                    ids = ",".join(sorted(e["gramps_id"] for e in es))
                    out.add("dup-vital-event", f"{o['gramps_id']} {typ} {ids}")


def place_coord_checks(T: dict, out: Findings) -> None:
    # places and events
    used = {e.get("place") for e in T["events"].values() if e.get("place") in T["places"]}
    for h in used:
        pl = T["places"][h]
        if not (str(pl.get("lat") or "").strip() and str(pl.get("long") or "").strip()):
            out.add("place-no-coords", pl.get("gramps_id") or h)


# an event whose description names a place of the tree, but not the one it points at
def chain_names(T: dict, h: str) -> set[str]:
    """The place's own name and the names of every place that encloses it, by every placeref."""
    seen: set[str] = set()
    names: set[str] = set()
    todo = [h]
    while todo:
        h = todo.pop()
        if h in seen or h not in T["places"]:
            continue
        seen.add(h)
        pl = T["places"][h]
        names.add(norm(re.sub(r"[^\w]+", " ", (pl.get("name") or {}).get("value") or "")))
        todo += [r.get("ref") for r in pl.get("placeref_list") or []]
    return names - {""}


def named_places(T: dict) -> dict[str, str]:
    """The folded name of every place whose name is long enough to look for in a description."""
    place_words = {}
    for h, pl in T["places"].items():
        n = norm(re.sub(r"[^\w]+", " ", (pl.get("name") or {}).get("value") or ""))
        if len(n) >= 4:
            place_words[h] = n
    return place_words


def place_label(T: dict, h: str) -> str:
    """The place as the tree writes it, with its ID, so a person can find it (norm folds the name)."""
    return f"{(T['places'][h].get('name') or {}).get('value')} ({_label(T, 'places', h)})"


def event_holders(T: dict) -> dict[str, list]:
    # who is on each event: the people who point at it, and both spouses of a family that points at it
    on_event: dict[str, list] = defaultdict(list)
    for p in T["people"].values():
        for r in p.get("event_ref_list") or []:
            on_event[r.get("ref")].append(p)
    for f in T["families"].values():
        for r in f.get("event_ref_list") or []:
            on_event[r.get("ref")] += [T["people"][x] for x in (f.get("father_handle"), f.get("mother_handle"))
                                       if x in T["people"]]
    return on_event


def own_names(on_event: dict[str, list], h: str) -> set[str]:
    """Every given name and surname of the people on the event, whole and word by word, folded."""
    names: set[str] = set()
    for p in on_event.get(h, []):
        n = p.get("primary_name") or {}
        for part in [n.get("first_name") or ""] + [s.get("surname") or "" for s in n.get("surname_list", [])]:
            part = norm(re.sub(r"[^\w]+", " ", part))
            names |= {part, *part.split()}
    return names - {""}


def _words_before(raw: list[str], i: int) -> tuple[list[str], str | None]:
    before, stop = [], None
    for t in reversed(raw[:i]):
        if t in ("'", "’"):
            continue
        if not t[0].isalnum():
            stop = t
            break
        before.insert(0, t)
    return before, stop


def origin(raw: list[str], i: int) -> bool:
    """The words before raw[i], back to the last punctuation, name a person's origin or home: «natural de»,
    «vecino del lugar de», «domicilié à», or a clause that opens with «de» («Juan, de Otropueblo»)."""
    before, stop = _words_before(raw, i)
    k = len(before)
    while k and before[k - 1] in ORIGIN_FILLER:
        k -= 1
    if k == len(before):
        return False
    return ((k == 0 and before[0] in ("de", "del", "d") and (stop is None or stop in ORIGIN_CLAUSE_BREAK))
            or (k > 0 and before[k - 1].startswith(ORIGIN_WORDS)))


def as_event_place(n: str, words: list, raw: list[str], residence: bool) -> bool:
    """Some match of n is not a person's origin or home."""
    ns = n.split()
    return any([w for w, _ in words[j:j + len(ns)]] == ns and (residence or not origin(raw, words[j][1]))
               for j in range(len(words)))


def _description_words(e: dict) -> tuple[list[str], list, str]:
    text = "".join(c for c in unicodedata.normalize("NFD", (e.get("description") or "").lower())
                   if unicodedata.category(c) != "Mn")
    raw = re.findall(r"[^\W_]+|[^\w\s]", text)
    # each folded word with the position of its raw word, so a match can look at the words before it
    words = [(w, i) for i, t in enumerate(raw) if t[0].isalnum() for w in [norm(t)] if w]
    desc = " ".join(w for w, _ in words)
    return raw, words, desc


def _place_hits(place_words: dict, own: set, people: set, padded: str, words: list, raw: list[str],
                residence: bool) -> dict[str, str]:
    # a name that is a given name or surname of someone on the event is that person, not a place
    hits = {oh: n for oh, n in place_words.items()
            if n not in own and n not in people and f" {n} " in padded and as_event_place(n, words, raw, residence)}
    # a name inside a longer one that also matched («Otropueblo» in «Parroquia de Otropueblo») adds nothing
    hits = {oh: n for oh, n in hits.items() if not any(f" {n} " in f" {m} " and n != m for m in hits.values())}
    return hits


def _event_place_check(T: dict, out: Findings, in_scope, place_words: dict, on_event: dict, e: dict) -> None:
    h = e.get("place")
    raw, words, desc = _description_words(e)
    if h not in T["places"] or not desc or not in_scope("events", e["handle"]):
        return
    own = chain_names(T, h)
    padded = f" {desc} "
    if any(f" {n} " in padded for n in own):
        return
    people = own_names(on_event, e["handle"])
    # on a residence, where someone lives («vecino de X») is the event's own place
    residence = e.get("type") == "Residence"
    hits = _place_hits(place_words, own, people, padded, words, raw, residence)
    if hits:
        out.add("place-vs-description", e["gramps_id"],
                f"place {place_label(T, h)}, description names {', '.join(sorted(place_label(T, oh) for oh in hits))}")


def place_vs_description_checks(T: dict, out: Findings, in_scope) -> None:
    place_words = named_places(T)
    on_event = event_holders(T)
    for e in T["events"].values():
        _event_place_check(T, out, in_scope, place_words, on_event, e)


def event_place_info(T: dict, info: dict, scope, in_scope) -> None:
    no_place = [e for e in T["events"].values() if not e.get("place")]
    info["events without place"] = len(no_place)
    if scope is not None:
        info["events without place, in scope"] = sorted(
            e["gramps_id"] for e in no_place if in_scope("events", e["handle"]))


def _sensitive_media(T: dict, out: Findings, in_scope, p: dict) -> None:
    for r in p["media_list"]:
        m = T["media"].get(r.get("ref"))
        if not m or r.get("private") or m.get("private"):  # already private: nothing left to fix
            continue
        if not (in_scope("people", p["handle"]) or in_scope("media", m["handle"])):
            continue
        attrs = [str(a.get("value", "")) for a in m.get("attribute_list", [])]
        kind = sensitive_kind(m.get("path", ""), m.get("desc", ""), *attrs)
        if kind:
            out.add("living-media-sensitive", f"{p['gramps_id']} {m['gramps_id']}", kind)


def living_media_checks(T: dict, out: Findings, in_scope) -> None:
    # media of a possibly living person that looks like an ID document or an address
    this_year = datetime.date.today().year
    for p in T["people"].values():
        if not p.get("media_list") or p.get("private"):
            continue
        if not possibly_living(p, T["events"], this_year):
            continue
        _sensitive_media(T, out, in_scope, p)


def surname_marker_checks(T: dict, out: Findings) -> None:
    # placeholder markers in surnames
    for p in T["people"].values():
        for n in [p.get("primary_name") or {}] + (p.get("alternate_names") or []):
            for s in n.get("surname_list", []):
                if "[" in (s.get("surname") or ""):
                    out.add("surname-marker", f"{p['gramps_id']} {s['surname']}")


def _fold_text(t: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", t.lower()) if unicodedata.category(c) != "Mn")


def _note_text(n: dict) -> str:
    text = n.get("text")
    return text.get("string", "") if isinstance(text, dict) else str(text or "")


def _marker_open(word: re.Pattern, text: str) -> bool:
    # each mention counts on its own: one discarded hypothesis does not hide another still open
    if any(not NOTE_DISCARDED_AFTER.match(text, m.end())
           and not NOTE_DISCARDED_BEFORE.search(text[:m.start()]) for m in word.finditer(text)):
        return True
    return False


def _note_marker_stem(T: dict, out: Findings, in_scope, tag_names: dict[str, str], stem: str) -> None:
    word = re.compile(rf"\b{stem}s?\b")
    wanted = {h for h, name in tag_names.items() if word.search(name)}
    if not wanted:
        return
    for n in T["notes"].values():
        if not in_scope("notes", n["handle"]) or wanted & set(n.get("tag_list") or []):
            continue
        text = _fold_text(_note_text(n))
        if _marker_open(word, text):
            out.add("note-marker-no-tag", f"{n['gramps_id']} {stem}")


def note_marker_checks(T: dict, out: Findings, in_scope) -> None:
    # a note that announces a hypothesis or a namesake but carries no such tag. Only where the tree already
    # has a tag of that name: a tree that does not use the convention is not blamed for it
    tag_names = {h: _fold_text(t.get("name") or "") for h, t in T["tags"].items()}
    for stem in NOTE_MARKERS:
        _note_marker_stem(T, out, in_scope, tag_names, stem)


def _find(parent: dict[str, str], h: str) -> str:
    while parent[h] != h:
        parent[h] = parent[parent[h]]
        h = parent[h]
    return h


def _family_members(P: dict, f: dict) -> list:
    members = [f.get("father_handle"), f.get("mother_handle")]
    members += [r.get("ref") for r in f.get("child_ref_list", []) if isinstance(r, dict)]
    return [m for m in members if m in P]


def family_components(P: dict, F: dict) -> tuple[dict, dict, dict]:
    """People joined by a family as father, mother or child. Godparents, witnesses and associations do not join
    anyone, and people in no family are left out: they are not a batch."""
    parent: dict[str, str] = {}
    fam_member: dict[str, str] = {}
    for f in F.values():
        members = _family_members(P, f)
        for m in members:
            parent.setdefault(m, m)
        for m in members[1:]:
            parent[_find(parent, m)] = _find(parent, members[0])
        if members:
            fam_member[f["handle"]] = members[0]
    comps: dict[str, list[str]] = defaultdict(list)
    for h in parent:
        comps[_find(parent, h)].append(h)
    return parent, fam_member, comps


def _main_component(P: dict, root: str | None, parent: dict, comps: dict, lowest: dict) -> str:
    root_h = next((h for h, p in P.items() if p.get("gramps_id") == root), None) if root else None
    if root_h in parent:
        return _find(parent, root_h)
    return min(comps, key=lambda c: (-len(comps[c]), id_order(lowest[c])))


def _hit_components(scope, parent: dict, fam_member: dict, comps: dict) -> set:
    if scope is None:
        return set(comps)
    return ({_find(parent, h) for t, h in scope if t == "people" and h in parent}
            | {_find(parent, fam_member[h]) for t, h in scope if t == "families" and h in fam_member})


def unconnected_checks(T: dict, out: Findings, scope, root: str | None) -> None:
    P = T["people"]
    parent, fam_member, comps = family_components(P, T["families"])
    if len(comps) <= 1:
        return
    lowest = {c: min((_label(T, "people", h) for h in hs), key=id_order) for c, hs in comps.items()}
    main = _main_component(P, root, parent, comps, lowest)
    hit = _hit_components(scope, parent, fam_member, comps)
    for c in sorted(hit - {main}, key=lambda c: id_order(lowest[c])):
        ids = sorted((_label(T, "people", h) for h in comps[c]), key=id_order)
        out.add("unconnected", lowest[c],
                f"{len(ids)} people: {', '.join(ids[:5])}{', ...' if len(ids) > 5 else ''}; "
                f"main component ({lowest[main]}) has {len(comps[main])}")


def sort_findings(out: Findings) -> None:
    # the same tree gives the same report: checks keep their order, keys inside one check go in ID order
    first = {c: i for i, c in reversed(list(enumerate(dict.fromkeys(i["check"] for i in out.items))))}
    out.items.sort(key=lambda i: (first[i["check"]], id_order(i["key"])))


def run_checks(tree: Tree, scope: set[tuple[str, str]] | None = None,
               root: str | None = None) -> tuple[list[dict], dict]:
    """Structural checks over the whole tree; duplicate checks need one object of each pair in scope.

    `scope` is the set of (type, handle) a run created or changed; None means every object.
    `root` is the Gramps ID whose family component is the main one; None means the largest.
    """
    T = tree.objs
    out = Findings()
    info: dict = {}
    in_scope = (lambda t, h: True) if scope is None else (lambda t, h: (t, h) in scope)
    referenced = collect_references(tree, out)
    orphan_checks(T, out, referenced)
    repository_info(T, info)
    dup_media_checks(T, out, in_scope)
    dup_repository_checks(T, out)
    dup_citation_checks(T, out, in_scope)

    from .event_duplicates import check_person_duplicates
    check_person_duplicates(T, out, in_scope)
    hygiene_checks(T, out, in_scope)
    from .modifier_notes import check_modifier_notes
    check_modifier_notes(T, out, in_scope)

    check_vital_pointers(T, out)
    family_backlink_checks(T, out)
    couple_checks(T, out, in_scope)
    same_name_checks(T, out, in_scope)
    fuzzy_person_checks(T, out, in_scope, info)
    dup_vital_event_checks(T, out, in_scope)
    place_coord_checks(T, out)
    place_vs_description_checks(T, out, in_scope)
    event_place_info(T, info, scope, in_scope)
    living_media_checks(T, out, in_scope)
    surname_marker_checks(T, out)
    note_marker_checks(T, out, in_scope)
    unconnected_checks(T, out, scope, root)
    dated_checks(T, out, info)
    sort_findings(out)
    return out.items, info


def dated_checks(T: dict, out: Findings, info: dict) -> None:
    """Dates that contradict each other. Whole tree, like the structural checks.

    Every date is an interval (dates.span); a finding needs the intervals to be disjoint, so a vague date only
    widens what is allowed. The detail says `firm` when every date behind it is a plain day, month or year.
    A person with no Birth event is taken as born up to a year before their baptism (an infant baptism).
    """
    vit, unread = _vitals(T)
    _own_event_checks(T, out, vit)
    _parent_child_checks(T, out, vit)
    _marriage_checks(T, out, vit)
    _spacing_checks(T, out, vit)
    _ancestor_checks(T, out)
    info["dates not read"] = unread


def _gid(T: dict, t: str, h: str) -> str:
    return (T[t].get(h) or {}).get("gramps_id") or h


def _pick_events(E: dict, p: dict, types: set, pointer: str | None) -> list:
    """The person's Primary events of these types, the pointed-at one first."""
    refs = [r for r in p.get("event_ref_list", []) if r.get("role") == "Primary"]
    events = [E.get(r.get("ref")) for r in refs]
    events = [e for e in events if e and dates.kind(e.get("type")) in types]
    i = p.get(pointer, -1) if pointer else -1
    erl = p.get("event_ref_list", [])
    pointed = E.get(erl[i].get("ref")) if 0 <= i < len(erl) else None
    if pointed in events:
        events.remove(pointed)
        events.insert(0, pointed)
    return events


def _pick(E: dict, p: dict, types: set, pointer: str | None) -> tuple:
    """((event, span) or None, number of unreadable dates passed over): the first readable event."""
    unread = 0
    for e in _pick_events(E, p, types, pointer):
        s = dates.span(e.get("date"))
        if s:
            return (e, s), unread
        d = e.get("date") or {}
        if any((d.get("dateval") or [])[:3]) or d.get("text"):
            unread += 1
    return None, unread


def _vitals(T: dict) -> tuple[dict, int]:
    """({handle: {birth, baptism, death, burial: (event, span)}}, dates not read)."""
    specs = (("birth", dates.BIRTH, "birth_ref_index"), ("baptism", dates.BAPTISM, None),
             ("death", dates.DEATH, "death_ref_index"), ("burial", dates.BURIAL, None))
    vit: dict[str, dict] = {}
    unread = 0
    for h, p in T["people"].items():
        vit[h] = {}
        for name, types, pointer in specs:
            got, n = _pick(T["events"], p, types, pointer)
            unread += n
            if got:
                vit[h][name] = got
    return vit, unread


def _ev(kind_: str, pair) -> str:
    e, s = pair
    return f"{kind_} {e.get('gramps_id')} {s.text}"


def _firm(*spans) -> str:
    return "firm" if all(s.firm for s in spans) else "soft"


def _born(vit: dict, h: str):
    """(label, span) of a birth: the Birth event, else the baptism as its upper bound."""
    v = vit.get(h, {})
    if "birth" in v:
        return _ev("Birth", v["birth"]), v["birth"][1]
    if "baptism" in v:
        return _ev("Baptism", v["baptism"]), dates.birth_from_baptism(v["baptism"][1])
    return None


def _died(vit: dict, h: str):
    """(label, span) of a death: the Death event, else the burial, which bounds it from above."""
    v = vit.get(h, {})
    if "death" in v:
        return _ev("Death", v["death"]), v["death"][1]
    if "burial" in v:
        return _ev("Burial", v["burial"]), v["burial"][1]
    return None


def _event_order(out: Findings, pid: str, v: dict) -> None:
    order = (("birth", "baptism"), ("birth", "death"), ("baptism", "death"), ("death", "burial"))
    for first, then in order:
        if first in v and then in v and v[then][1].hi < v[first][1].lo:
            a, b = v[first], v[then]
            out.add("event-order", f"{pid} {a[0].get('gramps_id')} {b[0].get('gramps_id')}",
                    f"{_ev(then.capitalize(), b)} is before {_ev(first.capitalize(), a)} ({_firm(a[1], b[1])})")


def _lifespan(out: Findings, pid: str, v: dict) -> None:
    start = v.get("birth") or v.get("baptism")
    end = v.get("death") or v.get("burial")
    if start and end:
        least, _ = dates.years_between(start[1], end[1])
        if least > MAX_LIFESPAN:
            out.add("lifespan", pid, f"at least {least:.0f} years: {_ev('from', start)} to {_ev('to', end)} "
                                     f"({_firm(start[1], end[1])})")


def _own_event_checks(T: dict, out: Findings, vit: dict) -> None:
    """A person's own events."""
    for h, v in vit.items():
        pid = _gid(T, "people", h)
        _event_order(out, pid, v)
        _lifespan(out, pid, v)


def _parent_age(out: Findings, key: str, role: str, pb, cb) -> None:
    young, old = PARENT_AGE[role]
    least, most = dates.years_between(pb[1], cb[1])
    if most < young:
        out.add("parent-age", key, f"{role} at most {most:.1f} at the birth: {role} {pb[0]}, "
                                   f"child {cb[0]} ({_firm(cb[1], pb[1])})")
    elif least > old:
        out.add("parent-age", key, f"{role} at least {least:.1f} at the birth: {role} {pb[0]}, "
                                   f"child {cb[0]} ({_firm(cb[1], pb[1])})")


def _child_findings(out: Findings, key: str, role: str, pb, pd, cb) -> None:
    if pb and cb[1].hi < pb[1].lo:
        out.add("child-before-parent", key, f"child {cb[0]} is before {role} {pb[0]} ({_firm(cb[1], pb[1])})")
    elif pb:
        _parent_age(out, key, role, pb, cb)
    if pd and cb[1].lo > pd[1].hi + (POSTHUMOUS_DAYS if role == "father" else 0):
        out.add("child-after-parent-death", key,
                f"child {cb[0]} is after {role} {pd[0]} ({_firm(cb[1], pd[1])})")


def _parent_children(T: dict, out: Findings, vit: dict, fid: str, role: str, ph: str, f: dict) -> None:
    pb, pd = _born(vit, ph), _died(vit, ph)
    pg = _gid(T, "people", ph)
    for c in f.get("child_ref_list", []):
        ch = c.get("ref") if isinstance(c, dict) else None
        cb = _born(vit, ch) if ch in T["people"] else None
        if not cb or dates.kind(c.get("frel" if role == "father" else "mrel")) not in BIOLOGICAL:
            continue
        _child_findings(out, f"{fid} {pg} {_gid(T, 'people', ch)}", role, pb, pd, cb)


def _parent_child_checks(T: dict, out: Findings, vit: dict) -> None:
    """Parents and children."""
    for fh, f in T["families"].items():
        fid = f.get("gramps_id") or fh
        for role, ph in (("father", f.get("father_handle")), ("mother", f.get("mother_handle"))):
            if ph in T["people"]:
                _parent_children(T, out, vit, fid, role, ph, f)


def _marriage_findings(out: Findings, key: str, ml: str, role: str, mar, pb, pd, gender) -> None:
    if pd and mar.lo > pd[1].hi:
        out.add("marriage-after-death", key, f"{ml} is after {role} {pd[0]} ({_firm(mar, pd[1])})")
    if pb and mar.hi < pb[1].lo:
        out.add("marriage-before-birth", key, f"{ml} is before {role} {pb[0]} ({_firm(mar, pb[1])})")
    elif pb:
        least_age = MARRIAGE_AGE.get(gender, min(MARRIAGE_AGE.values()))
        _, most = dates.years_between(pb[1], mar)
        if most < least_age:
            out.add("marriage-age", key, f"{role} at most {most:.1f} at {ml}, {pb[0]} "
                                         f"({_firm(mar, pb[1])})")


def _marriage_spouses(T: dict, out: Findings, vit: dict, fid: str, e: dict, mar, f: dict) -> None:
    ml = _ev("Marriage", (e, mar))
    for role, ph in (("father", f.get("father_handle")), ("mother", f.get("mother_handle"))):
        if ph not in T["people"]:
            continue
        key = f"{fid} {e.get('gramps_id')} {_gid(T, 'people', ph)}"
        _marriage_findings(out, key, ml, role, mar, _born(vit, ph), _died(vit, ph),
                           T["people"][ph].get("gender"))


def _marriage_checks(T: dict, out: Findings, vit: dict) -> None:
    """Marriages against the spouses' births and deaths."""
    for fh, f in T["families"].items():
        fid = f.get("gramps_id") or fh
        for r in f.get("event_ref_list", []):
            e = T["events"].get(r.get("ref"))
            mar = e and dates.kind(e.get("type")) in dates.MARRIAGE and dates.span(e.get("date"))
            if mar:
                _marriage_spouses(T, out, vit, fid, e, mar, f)


def _exact_births(T: dict, vit: dict) -> dict:
    """{mother: {child: birth}}: Birth events with an exact day, by birth."""
    kids: dict[str, dict] = defaultdict(dict)
    for f in T["families"].values():
        mh = f.get("mother_handle")
        if mh not in T["people"]:
            continue
        for c in f.get("child_ref_list", []):
            ch = c.get("ref") if isinstance(c, dict) else None
            b = vit.get(ch, {}).get("birth")
            if b and b[1].firm and b[1].lo == b[1].hi and dates.kind(c.get("mrel")) in BIOLOGICAL:
                kids[mh][ch] = b
    return kids


def _spacing_pairs(T: dict, out: Findings, mh: str, got: dict) -> None:
    rows = sorted(got.items(), key=lambda x: x[1][1].lo)
    for i, (a, ba) in enumerate(rows):
        for b, bb in rows[i + 1:]:
            gap = bb[1].lo - ba[1].lo
            if gap >= MIN_SPACING:
                break
            if gap > TWIN_DAYS:
                pair = sorted((_gid(T, "people", a), _gid(T, "people", b)), key=id_order)
                out.add("sibling-spacing", f"{_gid(T, 'people', mh)} {','.join(pair)}",
                        f"{gap} days apart: {_ev('Birth', ba)}, {_ev('Birth', bb)}")


def _spacing_checks(T: dict, out: Findings, vit: dict) -> None:
    """Children of one mother too close together."""
    for mh, got in _exact_births(T, vit).items():
        _spacing_pairs(T, out, mh, got)


def _parent_map(T: dict) -> dict:
    parents: dict[str, list] = defaultdict(list)
    for f in T["families"].values():
        for c in f.get("child_ref_list", []):
            ch = c.get("ref") if isinstance(c, dict) else None
            for par in (f.get("father_handle"), f.get("mother_handle")):
                if ch in T["people"] and par in T["people"] and par not in parents[ch]:
                    parents[ch].append(par)
    return parents


def _walk_up(T: dict, out: Findings, parents: dict, state: dict, start: str) -> None:
    """Depth-first walk up the parents from `start`, without recursion."""
    path, pos, stack = [start], {start: 0}, [iter(sorted(parents[start]))]
    state[start] = 1
    while stack:
        nxt = next(stack[-1], None)
        if nxt is None:
            state[path[-1]] = 2
            del pos[path.pop()]
            stack.pop()
        elif state.get(nxt) == 1:
            cyc = [_gid(T, "people", x) for x in path[pos[nxt]:]]
            out.add("own-ancestor", ",".join(sorted(set(cyc), key=id_order)),
                    " -> ".join(cyc + [cyc[0]]) + " (each the child of the next)")
        elif nxt not in state:
            state[nxt] = 1
            pos[nxt] = len(path)
            path.append(nxt)
            stack.append(iter(sorted(parents.get(nxt, ()))))


def _ancestor_checks(T: dict, out: Findings) -> None:
    """A person who is their own ancestor."""
    parents = _parent_map(T)
    state: dict[str, int] = {}  # 1 on the current path, 2 done
    for start in sorted(parents):
        if start not in state:
            _walk_up(T, out, parents, state, start)
