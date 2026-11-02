"""Integration tests: real gwt_fetch.surname_clusters.suggest() into build_queries().

Every cluster, person and register below is invented for these tests. No
network, no Gramps, no real data. These tests never edit surname_clusters or
the frozen B V2 module; they only wire the two real APIs together.
"""
import copy
import itertools
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)                                   # gwt_research/
sys.path.insert(0, os.path.join(_HERE, os.pardir, "fetch"))  # fetch/ for gwt_fetch

import pytest

from gwt_fetch import surname_clusters
import variant_queries as vq


def config():
    """Invented cluster configuration, in the exact shape suggest() validates."""
    return {"clusters": [
        {"cluster_id": "fict-zunexa", "aliases": [
            {"spelling": "Zunexa", "provenance": "registro inventado Aurelio, foja 12"},
            {"spelling": "Zuneixa", "provenance": "registro inventado Aurelio, foja 88"}]},
        {"cluster_id": "fict-molarca-a", "aliases": [
            {"spelling": "Molarca", "provenance": "libro inventado B, asiento 3"},
            {"spelling": "Molarqua", "provenance": "libro inventado B, asiento 19"}]},
        {"cluster_id": "fict-molarca-b", "aliases": [
            {"spelling": "Mólarca", "provenance": "indice inventado C, entrada 41"}]},
    ]}


def person(surnames="de Zunexa Fictis", target="Zunexa", year=1905):
    return {"given": "Prudencia", "surnames": surnames,
            "event_year": year, "surname_target": target}


def test_matched_real_suggest_flows_verbatim_into_plan():
    result = surname_clusters.suggest(config(), "zunexa")
    assert result["status"] == "matched"
    plan = vq.build_queries(result, person(), "familysearch")
    assert plan["status"] == "matched"
    assert plan["requires_human_review"] is True
    rows = plan["query_groups"][0]["queries"]
    # both cluster spellings carried, each in both query orders
    spellings = sorted({r["spelling"] for r in rows})
    assert spellings == ["Zuneixa", "Zunexa"]
    # provenance strings preserved literally, never rewritten
    provenances = {r["provenance"] for r in rows}
    assert provenances == {"registro inventado Aurelio, foja 12",
                           "registro inventado Aurelio, foja 88"}
    assert all(r["cluster_id"] == "fict-zunexa" for r in rows)


def test_matched_with_accents_and_particle_keeps_literal_frame():
    result = surname_clusters.suggest(config(), "ZÚNEXA")  # folds to zunexa
    assert result["status"] == "matched"
    assert result["normalized_alias"] == "zunexa"
    plan = vq.build_queries(result, person(), "familysearch")
    queries = {r["query"] for r in plan["query_groups"][0]["queries"]}
    # the leading particle "de" is never rewritten; only the target segment is
    assert '"Prudencia de Zunexa Fictis"' in queries
    assert '"de Zunexa Fictis, Prudencia"' in queries
    assert '"Prudencia de Zuneixa Fictis"' in queries
    assert '"de Zuneixa Fictis, Prudencia"' in queries
    assert not any("Zunexa de Prudencia" in q for q in queries)


def test_ambiguous_real_suggest_keeps_every_candidate_grouped():
    result = surname_clusters.suggest(config(), "MOLARCA")
    assert result["status"] == "ambiguous"
    assert result["suggestions"] == []  # real suggest() never flattens ambiguity
    p = person(surnames="Molarca Fictis", target="Molarca")
    plan = vq.build_queries(result, p, "familysearch")
    assert plan["status"] == "ambiguous"
    groups = plan["query_groups"]
    assert [g["cluster_id"] for g in groups] == ["fict-molarca-a", "fict-molarca-b"]
    assert all(g["ambiguous"] for g in groups)
    # no winner: every candidate's aliases appear in their own group
    by_id = {g["cluster_id"]: sorted({r["spelling"] for r in g["queries"]})
             for g in groups}
    assert by_id["fict-molarca-a"] == ["Molarca", "Molarqua"]
    assert by_id["fict-molarca-b"] == ["Mólarca"]
    assert plan["suggestions"] == []


def test_unmatched_real_suggest_builds_no_queries():
    result = surname_clusters.suggest(config(), "Nadiez")
    assert result["status"] == "unmatched"
    plan = vq.build_queries(result, person(surnames="de Nadiez Fictis",
                                           target="Nadiez"), "familysearch")
    assert plan["status"] == "unmatched"
    assert plan["query_groups"] == []
    assert plan["query_count"] == 0


def test_plan_is_deterministic_under_config_permutation():
    base = config()
    plans = []
    for cluster_order in itertools.permutations(range(3)):
        for alias_flip in (False, True):
            doc = {"clusters": [copy.deepcopy(base["clusters"][i])
                                for i in cluster_order]}
            if alias_flip:
                for c in doc["clusters"]:
                    c["aliases"] = list(reversed(c["aliases"]))
            result = surname_clusters.suggest(doc, "zunexa")
            plan = vq.build_queries(result, person(), "familysearch")
            plans.append((plan["query_groups"], plan["query_count"]))
    first = plans[0]
    for other in plans[1:]:
        assert other == first  # row order never collapses or reshuffles


def test_no_input_is_mutated_anywhere_in_the_chain():
    doc = config()
    doc_before = copy.deepcopy(doc)
    result = surname_clusters.suggest(doc, "zunexa")
    result_before = copy.deepcopy(result)
    p = person()
    p_before = copy.deepcopy(p)
    plan = vq.build_queries(result, p, "familysearch")
    assert doc == doc_before
    assert result == result_before
    assert p == p_before
    # mutating the plan afterwards cannot reach back into suggest()'s output
    plan["candidates"][0]["aliases"][0]["spelling"] = "CORROMPIDO"
    plan["suggestions"][0]["spelling"] = "CORROMPIDO"
    assert result["candidates"][0]["aliases"][0]["spelling"] == "Zunexa"
    assert result["suggestions"][0]["spelling"] == "Zunexa"


def test_expansions_are_opt_in_and_declared():
    result = surname_clusters.suggest(config(), "zunexa")
    p = person()
    default_plan = vq.build_queries(result, copy.deepcopy(p), "familysearch")
    rows = default_plan["query_groups"][0]["queries"]
    assert all(r["origin"] == "suggestion" and r["expansion"] is None for r in rows)

    widened = vq.build_queries(result, copy.deepcopy(p), "familysearch",
                               expand_year=True, expand_letters=True)
    wrows = widened["query_groups"][0]["queries"]
    literal = [r for r in wrows if r["origin"] == "suggestion"]
    expanded = [r for r in wrows if r["origin"] == "expansion"]
    assert len(literal) == len(rows)  # expansions only ADD rows, never collapse
    kinds = {(r["expansion"]["kind"],) for r in expanded}
    assert kinds == {("year-window",), ("letter-equivalence",)}
    year_rows = [r for r in expanded if r["expansion"]["kind"] == "year-window"]
    assert all(r["year_from"] == 1904 and r["year_to"] == 1906 for r in year_rows)
    assert all("heuristic" in r["expansion"]["reason"] for r in expanded)
    letter_spellings = {r["spelling"] for r in expanded
                        if r["expansion"]["kind"] == "letter-equivalence"}
    assert letter_spellings == {"sunexa", "suneixa", "Zuneyxa"}  # z->s at 0; i->y in Zuneixa


def test_expansion_flags_stay_strict_booleans_with_real_result():
    result = surname_clusters.suggest(config(), "zunexa")
    for bad in (1, "yes", None):
        with pytest.raises(ValueError, match="strict boolean"):
            vq.build_queries(result, person(), "familysearch", expand_letters=bad)
        with pytest.raises(ValueError, match="strict boolean"):
            vq.build_queries(result, person(), "familysearch", expand_year=bad)
