#!/usr/bin/env python3
"""gwt-cross: cross a downloaded index (a table of names, years and places) against the people of the tree.

Big archives publish name indexes as CSV or TSV. Reading ten thousand rows by eye is the giant search. This
scores every row against every person in a gwt-check dump and prints the rows that agree, best first, with the
Gramps ID they point to. Everything is local: nothing is sent anywhere, and a score is a lead for the reader,
never an identification.

  gwt-cross INDEX.tsv --dump dump.json [--only-parentless] [--min-score 6] [--format tsv|json]

INDEX needs a header. Recognised columns (any of the names): surname (apellido, nom, surname), given (nombre,
prenom, given), year (ano, annee, year), place (lugar, lieu, place, commune), ref (ref, cote, id, url), spouse.
Score: surname equal +4 (close spelling +2), given name equal +3 (close +1, initial +1), place equal +2,
year within 5 of a birth, baptism or death +2 (within 15, +1). A row with no surname agreement is dropped.

Delimiter (tab, semicolon, comma or bar) and encoding (UTF-8, UTF-16, Windows-1252) are detected; --encoding forces one.

Exit 0 when a row reached --min-score, 1 when none did, 2 when INDEX or the dump cannot be read.
"""
import argparse
import csv
import difflib
import json
import sys
import unicodedata

HEADERS = {"surname": ("surname", "apellido", "apellidos", "nom", "lastname", "family"),
           "given": ("given", "nombre", "prenom", "prenoms", "firstname", "forename"),
           "year": ("year", "ano", "año", "annee", "année", "date"),
           "place": ("place", "lugar", "lieu", "commune", "localidad"),
           "ref": ("ref", "cote", "id", "url", "link", "reference"),
           "spouse": ("spouse", "conyuge", "cónyuge", "conjoint")}


def norm(text):
    t = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode().lower()
    return " ".join("".join(c if c.isalnum() else " " for c in t).split())


def close(a, b):
    return bool(a and b) and difflib.SequenceMatcher(None, a, b).ratio() >= 0.85


def year_of(text):
    import re
    m = re.search(r"\b(1[0-9]{3}|20[0-2][0-9])\b", str(text or ""))
    return int(m.group(1)) if m else None


def columns(header):
    out = {}
    for i, h in enumerate(header):
        for field, names in HEADERS.items():
            if field not in out and norm(h) in [norm(n) for n in names]:
                out[field] = i
    return out


def tree_people(dump, only_parentless=False):
    objs = dump.get("objects", dump)
    events = {e["handle"]: e for e in objs["events"]}
    places = {x["handle"]: ((x.get("name") or {}).get("value", "")) for x in objs.get("places", [])}
    out = []
    for p in objs["people"]:
        if only_parentless and p.get("parent_family_list"):
            continue
        n = p.get("primary_name") or {}
        surs = [norm(s.get("surname")) for s in n.get("surname_list") or [] if s.get("surname")]
        if not surs:
            continue
        years, pl = [], set()
        for r in p.get("event_ref_list") or []:
            e = events.get(r.get("ref"))
            if not e:
                continue
            v = (e.get("date") or {}).get("dateval") or []
            if e.get("type") in ("Birth", "Baptism", "Death") and len(v) > 2 and v[2]:
                years.append(v[2])
            if e.get("place") and places.get(e["place"]):
                pl.add(norm(places[e["place"]]))
        out.append({"id": p.get("gramps_id"), "given": norm(n.get("first_name")), "surnames": surs, "years": years, "places": pl})
    return out


def score(row, person):
    s, why = 0, []
    sur = row["surname"]
    if sur in person["surnames"]:
        s += 4; why.append("apellido")
    elif any(sur and (set(sur.split()) <= set(x.split()) or set(x.split()) <= set(sur.split())) and len(sur) > 3
             for x in person["surnames"]):
        s += 3; why.append("apellido(partes)")  # "de la Fontaine" against "Fontaine"
    elif any(close(sur, x) for x in person["surnames"]):
        s += 2; why.append("apellido~")
    else:
        return 0, []
    g, pg = row["given"], person["given"]
    if g and pg:
        if g.split()[0] == pg.split()[0] or set(g.split()) & set(pg.split()):
            s += 3; why.append("nombre")
        elif close(g.split()[0], pg.split()[0]):
            s += 1; why.append("nombre~")
        elif g[0] == pg[0]:
            s += 1; why.append("inicial")
    if row["place"] and any(row["place"] == x or (len(row["place"]) > 4 and row["place"] in x) for x in person["places"]):
        s += 2; why.append("lugar")
    if row["year"] and person["years"]:
        d = min(abs(row["year"] - y) for y in person["years"])
        if d <= 5:
            s += 2; why.append("ano")
        elif d <= 15:
            s += 1; why.append("ano~")
    return s, why


def cross(rows, people, min_score):
    """Rows are only scored against people whose surname starts with the same letter (a close spelling can
    differ elsewhere, rarely in the first letter): 20,000 rows against 5,000 people stays under a few seconds."""
    found = []
    bucket = {}
    for p in people:
        for x in p["surnames"]:
            bucket.setdefault(x[:1], []).append(p)
    for r in rows:
        cand = bucket.get(r["surname"][:1], [])
        best = max(((score(r, p), p) for p in cand), key=lambda x: x[0][0], default=((0, []), None))
        (s, why), p = best
        if p and s >= min_score:
            found.append({"score": s, "person": p["id"], "why": "+".join(why), "row": r["raw"], "ref": r["ref"]})
    return sorted(found, key=lambda f: -f["score"])


def decode(raw, encoding=None):
    """Text of an index file. Archives export UTF-8, UTF-8 with a BOM, UTF-16 (Excel) or Windows-1252/Latin-1."""
    if encoding:
        return raw.decode(encoding)
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16")
    for enc in ("utf-8-sig", "cp1252"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            pass
    return raw.decode("latin-1")


def delimiter(header):
    counts = {d: header.count(d) for d in ("\t", ";", ",", "|")}
    best = max(counts, key=counts.get)
    return best if counts[best] else ","


def read_index(path, encoding=None):
    with open(path, "rb") as fh:
        text = decode(fh.read(), encoding)
    lines = [l for l in text.splitlines() if l.strip()]
    if not lines:
        raise ValueError("%s is empty" % path)
    data = list(csv.reader(lines, delimiter=delimiter(lines[0])))
    cols = columns(data[0])
    if "surname" not in cols:
        raise ValueError("no surname column in the header: %s" % ", ".join(data[0]))
    rows = []
    for line in data[1:]:
        def g(f):
            return line[cols[f]] if f in cols and cols[f] < len(line) else ""
        rows.append({"surname": norm(g("surname")), "given": norm(g("given")), "year": year_of(g("year")),
                     "place": norm(g("place")), "ref": g("ref"), "raw": " | ".join(line)})
    return rows


def main(argv=None):
    p = argparse.ArgumentParser(prog="gwt-cross", description=__doc__.split("\n\n")[0])
    p.add_argument("index")
    p.add_argument("--dump", required=True)
    p.add_argument("--only-parentless", action="store_true", help="only the people with no parents in the tree")
    p.add_argument("--min-score", type=int, default=6)
    p.add_argument("--encoding", help="force the index encoding (default: UTF-8, UTF-16 or Windows-1252 by what decodes)")
    p.add_argument("--limit", type=int, default=0, help="print at most this many rows (0 = all)")
    p.add_argument("--format", choices=("tsv", "json"), default="tsv")
    a = p.parse_args(argv)
    try:
        rows = read_index(a.index, a.encoding)
        with open(a.dump, encoding="utf-8") as fh:
            people = tree_people(json.load(fh), a.only_parentless)
    except (OSError, ValueError, KeyError, LookupError) as e:
        print("gwt-cross: cannot read the index or the dump: %s" % e, file=sys.stderr)
        return 2
    found = cross(rows, people, a.min_score)
    if a.limit > 0:
        found = found[:a.limit]
    print("gwt-cross: %d rows, %d people, %d leads" % (len(rows), len(people), len(found)), file=sys.stderr)
    if a.format == "json":
        print(json.dumps(found, ensure_ascii=False, indent=1))
    else:
        for f in found:
            print("%d\t%s\t%s\t%s\t%s" % (f["score"], f["person"], f["why"], f["ref"], f["row"]))
    return 0 if found else 1


if __name__ == "__main__":
    sys.exit(main())
