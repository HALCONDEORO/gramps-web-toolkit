"""Offline, human-maintained surname search suggestions; never identity evidence."""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import sys

from .names import fold


def _fields(value, expected, label):
    if not isinstance(value, dict) or set(value) != set(expected):
        raise ValueError(f"{label}: expected fields {', '.join(expected)}")


def _text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label}: expected a nonblank string")
    if any("\ud800" <= char <= "\udfff" for char in value):
        raise ValueError(f"{label}: lone Unicode surrogate is not allowed")


def validate_config(doc):
    """Reject malformed/conflicting definitions; cross-cluster overlap is valid.

    IDs are literal, human-assigned and unique. Within a cluster, each folded
    alias occurs once. Across clusters, a folded alias can have many owners;
    resolving it must expose ambiguity rather than choose an owner.
    """
    _fields(doc, ("clusters",), "config")
    if not isinstance(doc["clusters"], list):
        raise ValueError("clusters: expected a list")
    ids = set()
    for cluster in doc["clusters"]:
        _fields(cluster, ("cluster_id", "aliases"), "cluster")
        cid = cluster["cluster_id"]
        _text(cid, "cluster_id")
        if cid != cid.strip():
            raise ValueError("cluster_id: surrounding whitespace is not allowed")
        if cid in ids:
            raise ValueError(f"duplicate cluster_id: {cid!r} (conflicting definitions are not merged)")
        ids.add(cid)
        aliases = cluster["aliases"]
        if not isinstance(aliases, list) or not aliases:
            raise ValueError(f"{cid}: aliases must be a nonempty list")
        seen = set()
        for alias in aliases:
            _fields(alias, ("spelling", "provenance"), "alias")
            spelling = alias["spelling"]
            _text(spelling, "spelling")
            if spelling != spelling.strip() or not fold(spelling).strip():
                raise ValueError("spelling: surrounding whitespace or empty folded alias")
            _text(alias["provenance"], "provenance")
            key = fold(spelling)
            if key in seen:
                raise ValueError(f"{cid}: duplicate normalized alias {key!r}; consolidate provenance explicitly")
            seen.add(key)


def suggest(doc, alias):
    """Return literal candidates and reviewable suggestions without changing doc.

    Ambiguous inputs retain every matching cluster but produce no flattened
    suggestions. No transitive closure, phonetics, person matching or writes.
    """
    validate_config(doc)  # Validate before any lookup.
    _text(alias, "input alias")
    key = fold(alias)
    if not key.strip():
        raise ValueError("input alias: empty folded alias")
    candidates = [copy.deepcopy(c) for c in doc["clusters"]
                  if any(fold(a["spelling"]) == key for a in c["aliases"])]
    status = "ambiguous" if len(candidates) > 1 else "matched" if candidates else "unmatched"
    suggestions = []
    if len(candidates) == 1:
        cluster = candidates[0]
        suggestions = [{"cluster_id": cluster["cluster_id"], **a} for a in cluster["aliases"]]
    return {"input": alias, "normalized_alias": key, "status": status,
            "requires_human_review": True, "candidates": candidates, "suggestions": suggestions}


def _unique_fields(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON field: {key!r}")
        result[key] = value
    return result


def _bad_constant(value):
    raise ValueError(f"invalid JSON constant: {value}")


def main(argv=None):
    """Read local configuration and print one JSON result."""
    parser = argparse.ArgumentParser(
        prog="gwt-surname-clusters",
        description="Offline search suggestions from human-maintained clusters; human review "
                    "required, never identity or merge evidence. Literal spelling and provenance stay intact.",
        epilog="Exit 0: matched or unmatched; 1: ambiguous (all candidates, no suggestions); "
               "2: invalid config/input. No phonetics or automatic clustering. Variants integration deferred.")
    parser.add_argument("config", type=Path, help="local UTF-8 JSON cluster configuration")
    parser.add_argument("alias", help="one alias; only existing names folding (case and accents)")
    args = parser.parse_args(argv)
    try:
        doc = json.loads(args.config.read_text(encoding="utf-8"),
                         object_pairs_hook=_unique_fields, parse_constant=_bad_constant)
        result = suggest(doc, args.alias)
    except (OSError, ValueError) as exc:
        print(f"gwt-surname-clusters: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 1 if result["status"] == "ambiguous" else 0


if __name__ == "__main__":
    sys.exit(main())
