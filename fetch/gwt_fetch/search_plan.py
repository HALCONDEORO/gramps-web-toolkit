"""Bounded, explicit search planning; no I/O, identity inference or execution."""
from __future__ import annotations

import unicodedata

from . import names, variants

MAX_SELECTED = 8
MAX_ENTRIES = 36
SOURCES = ("gazeta", "boe")


def _text(value, *, provenance=False):
    limit = 512 if provenance else variants.MAX_LENGTH
    if (not isinstance(value, str) or not value.strip() or len(value) > limit
            or any(unicodedata.category(c).startswith("C") or c in "\u2028\u2029" for c in value)):
        raise ValueError("expected bounded, nonblank single-line text")
    if not provenance:
        variants.validate(value)
        if value != " ".join(value.split()) or '"' in value or not names.fold(value).strip():
            raise ValueError("query terms need canonical spaces, no double quotes and nonempty folded text")
    return value


def _selection(value):
    if not isinstance(value, dict) or set(value) != {"spelling", "provenance"}:
        raise ValueError("selection needs exactly spelling and provenance")
    return (_text(value["spelling"]), _text(value["provenance"], provenance=True))


def build(given: str, original: dict, selected: list, sources: list) -> dict:
    """Plan two name orders for explicitly selected *complete* surname strings.

    Each selection is {spelling, provenance}. The caller must resolve ambiguity
    and explicitly select alternatives first; cluster/configuration documents
    are not accepted. No rules, reverse edges or transitive expansion are used.
    At most eight alternatives plus the original and two sources are accepted.
    Folding merges planned requests, retaining every exact contributing query.
    """
    given = _text(given)
    first = _selection(original)
    if not isinstance(selected, list) or len(selected) > MAX_SELECTED:
        raise ValueError("selected must be a list of at most eight explicit selections")
    selections = [first, *sorted({_selection(row) for row in selected} - {first})]
    if (not isinstance(sources, list) or not 1 <= len(sources) <= len(SOURCES)
            or any(not isinstance(s, str) or s not in SOURCES for s in sources)
            or len(set(sources)) != len(sources)):
        raise ValueError("sources must be distinct gazeta/boe selections")

    # Reuse explicit suggestion semantics with rules disabled. Full surnames are
    # passed without spellings to names.queries: it must not split a compound
    # surname and replace only its first word.
    spellings = variants.suggest(first[0], {first[0]: [s for s, _ in selections]}, rules=False)
    query_sets = {s: names.queries(given, s) for s in spellings}
    entries = []
    for source in SOURCES:
        if source not in sources:
            continue
        grouped = {}
        for spelling, provenance in selections:
            for query in query_sets[spelling]:
                key = names.fold(query)
                row = grouped.setdefault(key, {
                    "source": source, "query": query, "normalized_query": key,
                    "status": "planned", "coverage_status": "unknown", "contributions": []})
                row["contributions"].append({"spelling": spelling, "provenance": provenance,
                                              "query": query})
        entries.extend(grouped.values())
    if len(entries) > MAX_ENTRIES:
        raise ValueError("plan exceeds 36 logical source/query entries")
    return {"schema_version": 1, "status": "planned", "executed": False,
            "given": given, "entries": entries}
