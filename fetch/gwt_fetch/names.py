"""Look one person up in the Gaceta de Madrid, the BOE up to 1959 and the BOE from 1960, in one command.

A person is written as given name, surname(s) and the spellings to try for the first surname.
Each spelling is searched in both orders the gazette prints names in: `Given Sur1 Sur2` and
`Sur1 Sur2, Given`. Every hit is downloaded and the words around the surname are printed, because
a hit says only that the words occur in the item, and a same-name hit is not kinship.
"""
from __future__ import annotations

import re
import sys
import unicodedata

from . import boe, gazeta
from .store import Store

COLUMNS = ["person", "ref", "date", "source", "query", "context"]
SOURCES = {"gazeta": gazeta, "boe": boe}


def fold(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s.lower()) if not unicodedata.combining(c))


def parse_person(spec: str) -> tuple[str, str]:
    """`Given/Surname1 Surname2` -> (given, surnames)."""
    if "/" not in spec or not all(p.strip() for p in spec.split("/", 1)):
        raise SystemExit(f"gwt-fetch: person must be Given/Surname1 Surname2, got {spec!r}")
    given, surnames = (p.strip() for p in spec.split("/", 1))
    return given, surnames


def queries(given: str, surnames: str, spellings=()) -> list[str]:
    parts = surnames.split()
    seen, out = set(), []
    for first in [parts[0], *spellings]:
        sur = " ".join([first, *parts[1:]])
        for q in (f'"{given} {sur}"', f'"{sur}, {given}"'):
            if fold(q) not in seen:  # the search ignores accents, so Garcia and García are one query
                seen.add(fold(q))
                out.append(q)
    return out


def fold_chars(s: str) -> str:
    """Fold accents and case one character at a time, so positions in the result match the original."""
    out = []
    for c in s:
        f = "".join(x for x in unicodedata.normalize("NFKD", c.lower()) if not unicodedata.combining(x))
        out.append(f if len(f) == 1 else c.lower())
    return "".join(out)


def context(text: str, surnames, given: str, width: int = 100) -> str:
    """The words around the first place a full surname stands next to the whole given name.

    `surnames` are complete spellings such as `Ejemplo Uno`. Both orders the gazettes print are
    accepted, `Given Sur1 Sur2` and `Sur1 Sur2, Given`, with whole words only: `Juanita` is not
    `Juan` and `Ejemplote` is not `Ejemplo`. When the full surname is there but not next to the
    given name, the first such place comes back marked.
    """
    flat = re.sub(r"\s+", " ", text)
    low = fold_chars(flat)
    giv = re.escape(fold_chars(given))
    sur = "|".join(re.escape(fold_chars(x)).replace(r"\ ", r"\s+") for x in surnames)
    edge, gap = r"(?<!\w)", r"[\s.,;:-]{1,4}"
    for pat in (rf"{edge}{giv}{gap}(?:{sur})(?!\w)", rf"{edge}(?:{sur}){gap}{giv}(?!\w)"):
        m = re.search(pat, low)
        if m:
            return flat[max(0, m.start() - width): m.end() + width]
    m = re.search(rf"{edge}(?:{sur})(?!\w)", low)
    return ("(given name not adjacent) " + flat[max(0, m.start() - width): m.end() + width]) if m else ""


def hits(client, query, sources, date_from, date_to, limit):
    for name in sources:
        for r in SOURCES[name].search(client, text=query, date_from=date_from, date_to=date_to, limit=limit):
            yield name, r


def run(args, client) -> int:
    store, rows, failed = Store(args.out), [], 0
    for spec in args.person:
        given, surnames = parse_person(spec)
        found = {}
        for q in queries(given, surnames, args.spell):
            for name, r in hits(client, q, args.source, args.date_from, args.date_to, args.limit):
                found.setdefault(r["ref"], (name, r, q))
        for ref, (name, r, q) in sorted(found.items(), key=lambda kv: kv[1][1]["date"]):
            failed += gazeta.fetch(client, ref, store) != 0
            path = store.find(ref + "_text")
            full = [" ".join([first, *surnames.split()[1:]]) for first in [surnames.split()[0], *args.spell]]
            ctx = context(path.read_text(errors="ignore"), full, given) if path else ""
            rows.append({"person": spec, "ref": ref, "date": r["date"], "source": name, "query": q,
                         "context": ctx or ("(surname not in the OCR text: open the PDF)" if path else "(no text: open the PDF)")})
    print("\t".join(COLUMNS))
    for r in rows:
        print("\t".join(r[c].replace("\t", " ") for c in COLUMNS))
    print(f"gwt-fetch: {len(rows)} item(s). A hit is the same name, not proof of kinship.", file=sys.stderr)
    return 1 if failed else 0
