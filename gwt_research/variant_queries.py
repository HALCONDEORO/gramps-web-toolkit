#!/usr/bin/env python3
"""gwt-variant-queries: turn one suggest() result (fork PR #117) into deterministic record-search queries.

Contract
========
- The only cluster input is the exact dict returned by
  ``gwt_fetch.surname_clusters.suggest()`` (``status``, ``candidates``,
  ``suggestions``, ``requires_human_review``). This adapter never recomputes or
  re-filters clusters and never edits ``surname_clusters``.
- Every alias spelling, provenance string and ambiguous candidate is carried
  verbatim. No winner is chosen among ambiguous clusters: each candidate
  cluster becomes its own query group marked ``"ambiguous": true`` and the
  ``candidates`` list is preserved untouched.
- Rows are never collapsed. Literal suggestion rows always stay; opt-in
  heuristic expansions only ADD rows, each with its expansion reason. A letter
  root/equivalence or a +/-1 year window widens a search; it is an explicitly
  heuristic query expansion, never an identity alias, and it is off unless the
  caller asks for it.
- Deterministic: no clock, no randomness, no network, no filesystem writes.
  Groups and rows sort by a total order, so the same input always produces the
  same plan.
- Ledger keys follow the slug semantics of ``gwt-ledger`` (NFKD casefold,
  accents and punctuation stripped, words joined with '-') so a parallel agent
  can match a plan row against the shared ledger without importing this module.
- Research aid only: ``requires_human_review`` is always true. Nothing here is
  identity, kinship or merge evidence, and nothing is written anywhere.

CLI: ``gwt-variant-queries SUGGEST.json PERSON.json SOURCE [--expand-letters] [--expand-year]``
prints the plan as JSON. Exit 0 prints a matched or unmatched plan; exit 1 an
ambiguous one (every group flagged, all candidates kept); exit 2 invalid input.
"""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import re
import sys
import unicodedata

MAX_TEXT = 1024
MAX_YEAR = 2100

# Historic one-letter/one-pair equivalences, one substitution at one position,
# never fed back into themselves. Heuristic search wideners only.
PAIRS = (("s", "z"), ("z", "s"), ("c", "s"), ("s", "c"),
         ("ph", "f"), ("f", "ph"), ("y", "i"), ("i", "y"))
LETTER_REASON = ("heuristic letter equivalence: one historic substitution "
                 "(s/z, c/s, ph/f, y/i) at one position; widens the search, "
                 "not an identity alias")
YEAR_REASON = ("heuristic +/-1 year window around the recorded event year; "
               "registers lag and memory drifts; not an identity alias")

_STATUSES = ("matched", "ambiguous", "unmatched")
_SUGGEST_FIELDS = ("input", "normalized_alias", "status",
                   "requires_human_review", "candidates", "suggestions")
_PERSON_FIELDS = ("given", "surnames", "event_year", "surname_target")


def _text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label}: expected a nonblank string")
    if len(value) > MAX_TEXT:
        raise ValueError(f"{label}: over {MAX_TEXT} characters")
    if any("\ud800" <= char <= "\udfff" for char in value):
        raise ValueError(f"{label}: lone Unicode surrogate is not allowed")


def _normalize(text):
    """Same slug base as gwt-ledger: casefold, strip accents/punct, collapse spaces."""
    text = unicodedata.normalize("NFKD", (text or "").casefold())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return " ".join(re.findall(r"[^\W_]+", text))


def _key(text):
    """Ledger-comparable slug: 'Jose Perez', never the mixed form."""
    return _normalize(text).replace(" ", "-")


def _validate_cluster(cluster, label):
    if not isinstance(cluster, dict) or set(cluster) != {"cluster_id", "aliases"}:
        raise ValueError(f"{label}: expected fields cluster_id, aliases")
    _text(cluster["cluster_id"], f"{label}.cluster_id")
    aliases = cluster["aliases"]
    if not isinstance(aliases, list) or not aliases:
        raise ValueError(f"{label}: aliases must be a nonempty list")
    for alias in aliases:
        if not isinstance(alias, dict) or set(alias) != {"spelling", "provenance"}:
            raise ValueError(f"{label}: alias needs fields spelling, provenance")
        _text(alias["spelling"], f"{label}.spelling")
        _text(alias["provenance"], f"{label}.provenance")


def _validate_suggestion(i, sug):
    if not isinstance(sug, dict) or set(sug) != {"cluster_id", "spelling", "provenance"}:
        raise ValueError(f"suggestions[{i}]: expected fields cluster_id, spelling, provenance")
    _text(sug["cluster_id"], f"suggestions[{i}].cluster_id")
    _text(sug["spelling"], f"suggestions[{i}].spelling")
    _text(sug["provenance"], f"suggestions[{i}].provenance")


def _check_matched(candidates, suggestions):
    if len(candidates) != 1 or not suggestions:
        raise ValueError("matched: exactly one candidate and nonempty suggestions expected")
    cluster = candidates[0]
    expected = sorted((a["spelling"], a["provenance"]) for a in cluster["aliases"])
    got = sorted((s["spelling"], s["provenance"]) for s in suggestions)
    if got != expected or any(s["cluster_id"] != cluster["cluster_id"] for s in suggestions):
        raise ValueError("matched: suggestions must mirror the candidate's aliases "
                         "and cluster_id exactly; a trimmed or foreign list is refused")


def _check_ambiguous(candidates, suggestions):
    if len(candidates) < 2 or suggestions:
        raise ValueError("ambiguous: several candidates and no flattened suggestions expected")


def _check_unmatched(candidates, suggestions):
    if candidates or suggestions:
        raise ValueError("unmatched: no candidates or suggestions expected")


_STATUS_SHAPES = {"matched": _check_matched,
                  "ambiguous": _check_ambiguous,
                  "unmatched": _check_unmatched}


def _validate_suggest(doc):
    if not isinstance(doc, dict) or set(doc) != set(_SUGGEST_FIELDS):
        raise ValueError(f"suggest result: expected fields {', '.join(_SUGGEST_FIELDS)}")
    _text(doc["input"], "input")
    _text(doc["normalized_alias"], "normalized_alias")
    status = doc["status"]
    if status not in _STATUSES:
        raise ValueError(f"status: expected one of {', '.join(_STATUSES)}")
    if doc["requires_human_review"] is not True:
        raise ValueError("requires_human_review must be true; plans are never final")
    candidates, suggestions = doc["candidates"], doc["suggestions"]
    if not isinstance(candidates, list) or not isinstance(suggestions, list):
        raise ValueError("candidates and suggestions must be lists")
    for i, cluster in enumerate(candidates):
        _validate_cluster(cluster, f"candidates[{i}]")
    for i, sug in enumerate(suggestions):
        _validate_suggestion(i, sug)
    _STATUS_SHAPES[status](candidates, suggestions)
    return status


def _fold(text):
    """Case/accent folding, same rule as gwt-fetch names."""
    return "".join(c for c in unicodedata.normalize("NFKD", text.lower())
                   if not unicodedata.combining(c))


def _validate_person(person, normalized_alias):
    if not isinstance(person, dict) or set(person) != set(_PERSON_FIELDS):
        raise ValueError(f"person: expected fields {', '.join(_PERSON_FIELDS)}")
    _text(person["given"], "person.given")
    _text(person["surnames"], "person.surnames")
    _text(person["surname_target"], "person.surname_target")
    target = person["surname_target"].strip()
    if _fold(target) != normalized_alias:
        raise ValueError("person.surname_target: must fold to the suggest() normalized_alias")
    year = person["event_year"]
    if year is not None:
        if isinstance(year, bool) or not isinstance(year, int):
            raise ValueError("person.event_year: expected an integer year or null")
        if not 1 <= year <= MAX_YEAR:
            raise ValueError(f"person.event_year: outside 1..{MAX_YEAR}")
    words = person["surnames"].split()
    hits = [i for i, w in enumerate(words) if _fold(w) == _fold(target)]
    if len(hits) != 1:
        raise ValueError("person.surname_target: exactly one matching whole-word "
                         "segment in surnames expected (a multi-word target can never be one)")
    return year, hits[0], target


def _orders(spelling, given, words, position):
    swapped = list(words)
    swapped[position] = spelling
    sur = " ".join(swapped)
    return (f'"{given} {sur}"', f'"{sur}, {given}"')


def _letter_variants(spelling):
    """One pair substitution at one position; the literal spelling is never produced."""
    out = set()
    low = spelling.lower()
    for i in range(len(spelling)):
        for source, target in PAIRS:
            if low[i:i + len(source)] == source:
                out.add(spelling[:i] + target + spelling[i + len(source):])
    return sorted(out)


def _row(query, spelling, cluster_id, provenance, origin, expansion,
         year_from, year_to, segment, position):
    return {"query": query, "query_key": _key(query), "query_normalized": _normalize(query),
            "spelling": spelling, "cluster_id": cluster_id, "provenance": provenance,
            "origin": origin, "expansion": expansion,
            "year_from": year_from, "year_to": year_to,
            "segment": segment, "position": position}


def _row_order(row):
    return (row["cluster_id"], row["spelling"], row["provenance"],
            0 if row["origin"] == "suggestion" else 1,
            "" if row["expansion"] is None else row["expansion"]["kind"],
            row["query"], row["position"])


def _grouped_spellings(status, suggest_result):
    """Spellings to query per candidate cluster; ambiguity keeps every owner."""
    if status == "matched":
        return [(suggest_result["candidates"][0]["cluster_id"], False,
                 [(sug["spelling"], sug["provenance"])
                  for sug in suggest_result["suggestions"]])]
    if status == "ambiguous":
        return [(c["cluster_id"], True,
                 [(a["spelling"], a["provenance"]) for a in c["aliases"]])
                for c in suggest_result["candidates"]]
    return []


def _suggestion_rows(spelling, provenance, cluster_id, segment, position,
                     given, words, year, expand_year):
    rows = []
    for query in _orders(spelling, given, words, position):
        rows.append(_row(query, spelling, cluster_id, provenance,
                         "suggestion", None, year, year, segment, position))
        if expand_year and year is not None:
            year_from = max(1, year - 1)
            year_to = min(MAX_YEAR, year + 1)
            rows.append(_row(f"{query} {year_from}-{year_to}", spelling,
                             cluster_id, provenance, "expansion",
                             {"kind": "year-window", "reason": YEAR_REASON},
                             year_from, year_to, segment, position))
    return rows


def _cluster_rows(cluster_id, spellings, given, words, position, segment,
                  year, expand_letters, expand_year):
    rows = []
    for spelling, provenance in spellings:
        rows.extend(_suggestion_rows(spelling, provenance, cluster_id, segment,
                                     position, given, words, year, expand_year))
        if expand_letters:
            for variant in _letter_variants(spelling):
                for query in _orders(variant, given, words, position):
                    rows.append(_row(query, variant, cluster_id, provenance,
                                     "expansion",
                                     {"kind": "letter-equivalence", "reason": LETTER_REASON},
                                     year, year, segment, position))
    rows.sort(key=_row_order)
    return rows


def build_queries(suggest_result, person, source, *, expand_letters=False, expand_year=False):
    """Build the deterministic query plan for one suggest() result.

    ``person`` is ``{"given", "surnames", "event_year", "surname_target"}``;
    ``surname_target`` is the single surname segment the alias spellings replace.
    It must fold to the suggest() normalized alias and occur exactly once as a
    whole word in ``surnames``, so a leading particle (de, del, van) is never
    rewritten by mistake. ``source`` is the ledger source label (for example
    "familysearch"). Expansions are opt-in and never collapse rows.
    """
    _text(source, "source")
    if not _key(source):
        raise ValueError("source: no letters or digits to key")
    for flag, value in (("expand_letters", expand_letters), ("expand_year", expand_year)):
        if not isinstance(value, bool):
            raise ValueError(f"{flag}: expected a strict boolean")
    status = _validate_suggest(suggest_result)
    year, position, segment = _validate_person(person, suggest_result["normalized_alias"])
    given = person["given"].strip()
    words = person["surnames"].split()

    groups = []
    for cluster_id, ambiguous, spellings in _grouped_spellings(status, suggest_result):
        groups.append({"cluster_id": cluster_id, "ambiguous": ambiguous,
                       "queries": _cluster_rows(cluster_id, spellings, given, words,
                                                position, segment, year,
                                                expand_letters, expand_year)})
    groups.sort(key=lambda g: g["cluster_id"])

    return {"tool": "gwt-variant-queries", "status": status,
            "requires_human_review": True,
            "input": suggest_result["input"],
            "normalized_alias": suggest_result["normalized_alias"],
            "person": {"given": person["given"], "surnames": person["surnames"],
                       "event_year": year,
                       "person_key": _key(f"{person['given']} {person['surnames']}")},
            "source": {"label": source, "source_key": _key(source)},
            "candidates": copy.deepcopy(suggest_result["candidates"]),
            "suggestions": copy.deepcopy(suggest_result["suggestions"]),
            "query_groups": groups,
            "query_count": sum(len(g["queries"]) for g in groups)}


def _unique_fields(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON field: {key!r}")
        result[key] = value
    return result


def _bad_constant(value):
    raise ValueError(f"invalid JSON constant: {value}")


def _read_json(path, label):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"),
                          object_pairs_hook=_unique_fields, parse_constant=_bad_constant)
    except OSError as exc:
        raise ValueError(f"{label}: {exc}") from exc


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="gwt-variant-queries",
        description="Deterministic record-search queries from one suggest() result; human review "
                    "required, never identity or merge evidence. Expansions are opt-in heuristics.",
        epilog="Exit 0: matched or unmatched plan; 1: ambiguous (all candidates kept, every group "
               "flagged); 2: invalid input. No network, no writes.")
    parser.add_argument("suggest_json", help="UTF-8 JSON with one suggest() result")
    parser.add_argument("person_json", help='UTF-8 JSON {"given", "surnames", "event_year"}')
    parser.add_argument("source", help="ledger source label, for example familysearch")
    parser.add_argument("--expand-letters", action="store_true",
                        help="add heuristic letter-equivalence rows (s/z, c/s, ph/f, y/i)")
    parser.add_argument("--expand-year", action="store_true",
                        help="add heuristic +/-1 year window rows when event_year is set")
    args = parser.parse_args(argv)
    try:
        plan = build_queries(_read_json(args.suggest_json, "suggest_json"),
                             _read_json(args.person_json, "person_json"),
                             args.source,
                             expand_letters=args.expand_letters,
                             expand_year=args.expand_year)
    except ValueError as exc:
        print(f"gwt-variant-queries: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(plan, ensure_ascii=False))
    return 1 if plan["status"] == "ambiguous" else 0


if __name__ == "__main__":
    sys.exit(main())
