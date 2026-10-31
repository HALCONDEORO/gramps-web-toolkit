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
full given name equal +1 more, year equal to a birth, baptism or death +3 (within 5, +2; within 15, +1). A row with no surname agreement is dropped.

Delimiter (tab, semicolon, comma or bar) and encoding (UTF-8, UTF-16, Windows-1252) are detected; --encoding forces one.

A row that fits several people equally lists all their IDs, comma separated (a sign of duplicates in the tree
or of a common name, never a pick).

Exit 0 when a row reached --min-score, 1 when none did, 2 when INDEX or the dump cannot be read.
"""
import argparse
import csv
import os
import difflib
import json
import sys
import unicodedata

HEADERS = {"surname": ("surname", "apellido", "apellidos", "nom", "lastname", "family"),
           "given": ("given", "nombre", "prenom", "prenoms", "firstname", "forename"),
           "year": ("year", "ano", "año", "annee", "année", "date", "fecha"),
           "place": ("place", "lugar", "lieu", "commune", "localidad"),
           "ref": ("ref", "cote", "id", "url", "link", "reference"),
           "spouse": ("spouse", "conyuge", "cónyuge", "conjoint")}


def norm(text):
    t = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode().lower()
    return " ".join("".join(c if c.isalnum() else " " for c in t).split())


def phon(text):
    """A rough Spanish/French spelling key: Vidal and Bidal, Gil and Jil, Pérez and Peres agree."""
    t = norm(text).replace("ll", "y").replace("qu", "k").replace("ph", "f")
    for a, b in (("v", "b"), ("w", "b"), ("z", "s"), ("c", "k"), ("g", "j"), ("h", ""), ("y", "i"), ("x", "ks")):
        t = t.replace(a, b)
    return t


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
            hn = norm(h)
            # exact name, or a name followed by a qualifier: "Apellidos_nacimiento", "Fecha_nacimiento"
            if field not in out and any(hn == norm(n) or hn.startswith(norm(n) + " ") for n in names):
                out[field] = i
    return out


def tree_people(dump, only_parentless=False, no_death=False):
    objs = dump.get("objects", dump)
    events = {e["handle"]: e for e in objs["events"]}
    places = {x["handle"]: ((x.get("name") or {}).get("value", "")) for x in objs.get("places", [])}
    out = []
    for p in objs["people"]:
        if only_parentless and p.get("parent_family_list"):
            continue
        if no_death and any((events.get(r.get("ref")) or {}).get("type") == "Death" for r in p.get("event_ref_list") or []):
            continue
        n = p.get("primary_name") or {}
        surs = [norm(s.get("surname")) for s in n.get("surname_list") or [] if s.get("surname")]
        if not surs:
            continue
        years, births, deaths, pl = [], [], [], set()
        for r in p.get("event_ref_list") or []:
            e = events.get(r.get("ref"))
            if not e:
                continue
            v = (e.get("date") or {}).get("dateval") or []
            if e.get("type") in ("Birth", "Baptism", "Death") and len(v) > 2 and v[2]:
                years.append(v[2])
                (deaths if e["type"] == "Death" else births).append(v[2])
            if e.get("place") and places.get(e["place"]):
                pl.add(norm(places[e["place"]]))
        out.append({"id": p.get("gramps_id"), "given": norm(n.get("first_name")), "surnames": surs, "years": years, "births": births, "deaths": deaths, "places": pl})
    return out


def score(row, person):
    s, why = 0, []
    sur = row["surname"]
    if sur in person["surnames"]:
        s += 4; why.append("apellido")
    elif any(sur and (set(sur.split()) <= set(x.split()) or set(x.split()) <= set(sur.split())) and len(sur) > 3
             for x in person["surnames"]):
        s += 3; why.append("apellido(partes)")  # "de la Fontaine" against "Fontaine"
    elif any(any(a == b for a in phon(sur).split() for b in phon(x).split()) for x in person["surnames"]):
        s += 3; why.append("apellido(fonetico)")
    elif any(close(sur, x) for x in person["surnames"]):
        s += 2; why.append("apellido~")
    else:
        return 0, []
    g, pg = row["given"], person["given"]
    if g and pg:
        if g.split()[0] == pg.split()[0]:
            s += 3; why.append("nombre")
            if g == pg:
                s += 1; why.append("nombre completo")
        elif set(g.split()) & set(pg.split()):
            s += 1; why.append("otro nombre")  # a shared second name (Marie, Jean) is weak on its own
        elif close(g.split()[0], pg.split()[0]):
            s += 1; why.append("nombre~")
        elif g[0] == pg[0]:
            s += 1; why.append("inicial")
    if row["place"] and any(row["place"] == x or (len(row["place"]) > 4 and row["place"] in x) for x in person["places"]):
        s += 2; why.append("lugar")
    pairs = [(row["year"], person["births"]), (row.get("year2"), person["deaths"])]
    gaps = [abs(ry - y) for ry, ys in pairs if ry for y in ys]
    if gaps:
        d = min(gaps)
        if d == 0:
            s += 3; why.append("ano exacto")
        elif d <= 5:
            s += 2; why.append("ano")
        elif d <= 15:
            s += 1; why.append("ano~")
    return s, why


def cross(rows, people, min_score):
    """Rows are only scored against people with a surname word that starts with the same sound letter (after the
    phonetic key: v/b, g/j, c/k share a letter): 20,000 rows against 5,000 people stays under a few seconds."""
    found = []
    bucket = {}
    for p in people:
        for x in p["surnames"]:
            for tok in phon(x).split() or [""]:
                bucket.setdefault(tok[:1], []).append(p)
    for r in rows:
        cand = {id(p): p for tok in (phon(r["surname"]).split() or [""]) for p in bucket.get(tok[:1], [])}.values()
        scored = [(score(r, p), p) for p in cand]
        top = max((x[0][0] for x in scored), default=0)
        if top >= min_score:
            tied = [(x, p) for x, p in scored if x[0] == top]  # equal scores: show every person, never pick one by order
            found.append({"score": top, "person": ",".join(p["id"] for _, p in tied), "why": "+".join(tied[0][0][1]),
                          "row": r["raw"], "ref": r["ref"], "ties": len(tied)})
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


def is_insee(first_line):
    """The INSEE file of deceased people: NAME*GIVEN NAMES/ padded to 80 characters, then fixed columns."""
    return len(first_line) >= 166 and "*" in first_line[:80] and "/" in first_line[:80] and first_line[81:89].isdigit()


def insee_rows(path, keep, label):
    """Stream the INSEE deceased file (data.gouv.fr, fixed columns, Latin-1 or UTF-8) and keep only the rows whose
    surname sounds like one in `keep` (a set of phonetic keys). Birth date, birth commune, death date and
    commune, act number: name*given/ (80), sex, birth YYYYMMDD, commune code, commune (30), country (30),
    death YYYYMMDD, commune code, act number."""
    rows = []
    with open(path, "rb") as fh:
        for raw in fh:
            try:
                line = raw.decode("utf-8")
            except UnicodeDecodeError:
                line = raw.decode("latin-1")
            name = line[:80]
            sur, _, given = name.partition("*")
            sur_n = norm(sur)
            if not sur_n or not any(t in keep for t in phon(sur_n).split()):
                continue
            by, dy = line[81:85], line[154:158]
            commune = line[94:124].strip()
            rows.append({"surname": sur_n, "given": norm(given.replace("/", " ").replace(",", " ")),
                         "year": int(by) if by.isdigit() and int(by) else None,
                         "year2": int(dy) if dy.isdigit() and int(dy) else None,
                         "place": norm(commune), "ref": "%s act %s, death %s %s" % (label, line[167:176].strip(), line[154:162], line[162:167]),
                         "raw": "%s | %s | n. %s %s | m. %s" % (sur.strip(), given.strip(" /"), line[81:89], commune, line[154:162])})
    return rows


def read_index(path, encoding=None, keep=None):
    with open(path, "rb") as fh:
        head = fh.readline().decode("latin-1")
    if is_insee(head):
        return insee_rows(path, keep if keep is not None else set(), os.path.basename(path))
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
    p.add_argument("index", nargs="+", help="one or more index files (CSV, TSV, or the INSEE deceased file)")
    p.add_argument("--dump", required=True)
    p.add_argument("--only-parentless", action="store_true", help="only the people with no parents in the tree")
    p.add_argument("--no-death", action="store_true", help="only people with no death event in the tree: who is the index's death?")
    p.add_argument("--min-score", type=int, default=6)
    p.add_argument("--encoding", help="force the index encoding (default: UTF-8, UTF-16 or Windows-1252 by what decodes)")
    p.add_argument("--limit", type=int, default=0, help="print at most this many rows (0 = all)")
    p.add_argument("--format", choices=("tsv", "json"), default="tsv")
    a = p.parse_args(argv)
    try:
        with open(a.dump, encoding="utf-8") as fh:
            people = tree_people(json.load(fh), a.only_parentless, a.no_death)
        keep = {t for p in people for x in p["surnames"] for t in phon(x).split()}
        rows = []
        for path in a.index:
            rows += read_index(path, a.encoding, keep)
    except (OSError, ValueError, KeyError, LookupError) as e:
        print("gwt-cross: cannot read the index or the dump: %s" % e, file=sys.stderr)
        return 2
    found = cross(rows, people, a.min_score)
    if a.limit > 0:
        found = found[:a.limit]
    print("gwt-cross: %d rows, %d people, %d leads" % (len(rows), len(people), len(found)), file=sys.stderr)
    if sys.stdout.isatty() is False:
        import signal
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)  # `| head` ends quietly
    if a.format == "json":
        print(json.dumps(found, ensure_ascii=False, indent=1))
    else:
        for f in found:
            print("%d\t%s\t%s\t%s\t%s" % (f["score"], f["person"], f["why"], f["ref"], f["row"]))
    return 0 if found else 1


if __name__ == "__main__":
    sys.exit(main())
