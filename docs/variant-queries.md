# Variant queries

`python3 gwt_research/variant_queries.py SUGGEST.json PERSON.json SOURCE [--expand-letters] [--expand-year]` turns one
[`gwt surname-clusters`](surname-clusters.md) `suggest()` result into a deterministic list of
record-search queries, ready to run and to log in the ledger. It reads two local UTF-8 JSON files
and prints one JSON plan. It never fetches records, never changes the tree, the ledger or the
cluster configuration, and never edits `surname_clusters`.

```json
{"given": "Demo", "surnames": "de Alfa Inventado", "event_year": 1905, "surname_target": "Alfa"}
```

```sh
python3 gwt_research/variant_queries.py suggest.json person.json familysearch
python3 gwt_research/variant_queries.py suggest.json person.json familysearch --expand-letters --expand-year
```

`surname_target` names the one surname segment the alias spellings replace. It must fold (case and
accents) to the `suggest()` normalized alias and match exactly one whole word of `surnames`, so a
leading particle (`de`, `del`, `van`) is never rewritten by mistake: `"de Alfa Inventado"` yields
`"de Alpha Inventado"`, never `"Alfa Alfa Inventado"`. Every row carries the declared `segment`
and its word `position`. In a `matched` result the suggestions must mirror the candidate's aliases
and cluster id exactly; a trimmed or foreign list is refused.

For every suggested alias spelling the plan carries the literal spelling, its provenance and its
cluster id verbatim, with two query strings (`"Given Sur1 Sur2"` and `"Sur1 Sur2, Given"`), a
`query_key` in the ledger's slug form and the person's `person_key`, so a parallel agent can log
or match the search without importing this tool. Rows sort by a total order (cluster, spelling,
provenance, origin, expansion kind, query, position): the same input always prints the same plan,
even when the input candidates or aliases arrive permuted, with no clock, randomness, network or
writes involved.

An ambiguous result keeps every candidate cluster as its own group marked `"ambiguous": true`,
with the full `candidates` list preserved and no winner chosen; the CLI exits 1. A matched result
exits 0, an unmatched one exits 0 with an empty plan, and invalid input exits 2.

Expansions are opt-in heuristics, never identity aliases, and the flags accept only true booleans:

- `--expand-letters` adds rows with one historic letter substitution (s/z, c/s, ph/f, y/i) at one
  position, each row marked `"origin": "expansion"` with its reason and the provenance of the
  literal spelling it widens.
- `--expand-year` adds, when `event_year` is set, one row per literal query with a `Y-1-Y+1`
  window clamped to 1..2100 (year 1 gives `1-2`, year 2100 gives `2099-2100`), marked the same way.

The literal rows always stay: an expansion that reproduces a suggested spelling adds its own rows
instead of collapsing them. Every plan carries `"requires_human_review": true`; a query plan is a
search aid, not identity, kinship or merge evidence. Fixtures and examples are invented.

This tool is currently source-only. No installed CLI wrapper is added by this change.

## Integration tests against the real suggest() (piece B3)

`gwt_research/test_variant_queries_integration.py` builds invented cluster
configs and feeds the real `gwt_fetch.surname_clusters.suggest()` output into
`build_queries()` — no stubbed suggest dicts. Covered: matched, ambiguous
(every candidate grouped, no winner, empty suggestions) and unmatched; an
accent-folded input (`ZÚNEXA`) with a leading particle (`de`) that is never
rewritten; verbatim provenance strings in every row; identical `query_groups`
and `query_count` under every permutation of the config's cluster and alias
order; zero mutation of the config, the suggest() result, the person, or
backwards from the returned plan; and opt-in expansions that only add rows
(`year-window`, `letter-equivalence`), each with its heuristic reason, with
strict boolean flags. Run: `python3 .github/scripts/run_tests.py --only gwt_research`.
