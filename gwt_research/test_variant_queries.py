"""Contract tests for gwt-variant-queries; every fixture is invented."""
import copy
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pytest

import variant_queries as vq

MODULE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "variant_queries.py")


def suggest(status="matched"):
    doc = {"input": "Álfa", "normalized_alias": "alfa", "status": status,
           "requires_human_review": True, "candidates": [], "suggestions": []}
    if status == "matched":
        doc["candidates"] = [{"cluster_id": "demo-alfa", "aliases": [
            {"spelling": "Alfa", "provenance": "invented register A, view 1"},
            {"spelling": "Alpha", "provenance": "invented register A, view 2"}]}]
        doc["suggestions"] = [{"cluster_id": "demo-alfa", **a}
                              for a in doc["candidates"][0]["aliases"]]
    elif status == "ambiguous":
        doc["candidates"] = [
            {"cluster_id": "demo-alfa", "aliases": [
                {"spelling": "Alfa", "provenance": "invented register A"}]},
            {"cluster_id": "demo-beta", "aliases": [
                {"spelling": "Alfa", "provenance": "invented register B, view 1"},
                {"spelling": "Alfha", "provenance": "invented register B, view 2"}]}]
    return doc


def person(year=1905, surnames="Alfa Inventado", target="Alfa"):
    return {"given": "Demo", "surnames": surnames, "event_year": year,
            "surname_target": target}


def test_matched_plan_is_literal_and_complete():
    plan = vq.build_queries(suggest(), person(), "invented-source")
    assert plan["status"] == "matched"
    assert plan["requires_human_review"] is True
    assert len(plan["query_groups"]) == 1
    group = plan["query_groups"][0]
    assert group["cluster_id"] == "demo-alfa" and group["ambiguous"] is False
    assert [r["query"] for r in group["queries"]] == [
        '"Alfa Inventado, Demo"', '"Demo Alfa Inventado"',
        '"Alpha Inventado, Demo"', '"Demo Alpha Inventado"']
    assert [r["origin"] for r in group["queries"]] == ["suggestion"] * 4
    assert all(r["expansion"] is None for r in group["queries"])
    assert all(r["year_from"] == r["year_to"] == 1905 for r in group["queries"])
    assert group["queries"][0]["provenance"] == "invented register A, view 1"
    assert plan["query_count"] == 4


def test_input_is_never_mutated_and_plan_is_independent():
    doc, p = suggest(), person()
    before_doc, before_p = copy.deepcopy(doc), copy.deepcopy(p)
    plan = vq.build_queries(doc, p, "invented-source")
    assert doc == before_doc and p == before_p
    plan["candidates"][0]["aliases"][0]["spelling"] = "Changed"
    plan["suggestions"][0]["provenance"] = "Changed"
    plan["person"]["given"] = "Changed"
    assert doc == before_doc and p == before_p


def test_ambiguous_keeps_every_candidate_flagged_and_no_winner():
    plan = vq.build_queries(suggest("ambiguous"), person(), "invented-source")
    assert plan["status"] == "ambiguous"
    assert [g["cluster_id"] for g in plan["query_groups"]] == ["demo-alfa", "demo-beta"]
    assert all(g["ambiguous"] for g in plan["query_groups"])
    beta = plan["query_groups"][1]["queries"]
    assert {r["spelling"] for r in beta} == {"Alfa", "Alfha"}
    assert {r["provenance"] for r in beta} == {
        "invented register B, view 1", "invented register B, view 2"}
    assert plan["candidates"] == suggest("ambiguous")["candidates"]
    assert plan["suggestions"] == []
    # demo-alfa has one alias, demo-beta two: 2*1 + 2*2 queries.
    assert plan["query_count"] == 2 * 1 + 2 * 2


def test_unmatched_builds_no_queries():
    plan = vq.build_queries(suggest("unmatched"), person(), "invented-source")
    assert plan["query_groups"] == [] and plan["query_count"] == 0
    assert plan["requires_human_review"] is True


def test_unknown_status_refused():
    doc = suggest()
    doc["status"] = "guessed"
    with pytest.raises(ValueError, match="status"):
        vq.build_queries(doc, person(), "invented-source")


def test_inconsistent_status_shapes_refused():
    doc = suggest("matched")
    doc["suggestions"] = []
    with pytest.raises(ValueError, match="matched"):
        vq.build_queries(doc, person(), "invented-source")
    doc = suggest("ambiguous")
    doc["suggestions"] = [{"cluster_id": "demo-alfa", "spelling": "Alfa",
                           "provenance": "invented register A"}]
    with pytest.raises(ValueError, match="ambiguous"):
        vq.build_queries(doc, person(), "invented-source")
    doc = suggest("unmatched")
    doc["candidates"] = suggest("matched")["candidates"]
    with pytest.raises(ValueError, match="unmatched"):
        vq.build_queries(doc, person(), "invented-source")


def test_requires_human_review_false_refused():
    doc = suggest()
    doc["requires_human_review"] = False
    with pytest.raises(ValueError, match="requires_human_review"):
        vq.build_queries(doc, person(), "invented-source")


def test_expansions_are_off_by_default():
    doc = suggest()
    doc["candidates"][0]["aliases"].append({"spelling": "Alsa",
                                            "provenance": "invented register A, view 3"})
    doc["suggestions"].append({"cluster_id": "demo-alfa", "spelling": "Alsa",
                               "provenance": "invented register A, view 3"})
    plan = vq.build_queries(doc, person(), "invented-source")
    assert all(r["origin"] == "suggestion" for r in plan["query_groups"][0]["queries"])
    assert not any("year" in str(r["expansion"]) for g in plan["query_groups"]
                   for r in g["queries"])


def test_letter_expansion_opt_in_adds_rows_without_collapsing():
    doc = suggest()
    doc["candidates"][0]["aliases"].append({"spelling": "Alsa",
                                            "provenance": "invented register A, view 3"})
    doc["suggestions"].append({"cluster_id": "demo-alfa", "spelling": "Alsa",
                               "provenance": "invented register A, view 3"})
    plan = vq.build_queries(doc, person(), "invented-source", expand_letters=True)
    rows = plan["query_groups"][0]["queries"]
    literal = [r for r in rows if r["origin"] == "suggestion"]
    expanded = [r for r in rows if r["origin"] == "expansion"]
    assert len(literal) == 6  # the three literal spellings always stay
    # Bidirectional pairs: Alsa -> Alza/Alca; Alfa <-> Alpha also pair up.
    assert {r["spelling"] for r in expanded} == {"Alca", "Alfa", "Alpha", "Alza"}
    assert all(r["expansion"]["kind"] == "letter-equivalence" for r in expanded)
    assert all("not an identity alias" in r["expansion"]["reason"] for r in expanded)
    # Each expansion inherits the provenance of the literal spelling it widens.
    alsa_driven = [r for r in expanded if r["spelling"] in ("Alza", "Alca")]
    assert alsa_driven and all(r["provenance"] == "invented register A, view 3"
                               for r in alsa_driven)


def test_year_expansion_opt_in_and_bounded():
    plan = vq.build_queries(suggest(), person(1905), "invented-source", expand_year=True)
    rows = plan["query_groups"][0]["queries"]
    windows = [r for r in rows if r["origin"] == "expansion"]
    assert len(windows) == 4  # one window row per literal query string
    assert all(r["year_from"] == 1904 and r["year_to"] == 1906 for r in windows)
    assert all(r["query"].endswith(" 1904-1906") for r in windows)
    assert all(r["expansion"]["kind"] == "year-window" for r in windows)
    base = [r for r in rows if r["origin"] == "suggestion"]
    assert all(r["year_from"] == r["year_to"] == 1905 for r in base)
    no_year = vq.build_queries(suggest(), person(None), "invented-source", expand_year=True)
    assert all(r["origin"] == "suggestion" for r in no_year["query_groups"][0]["queries"])


def test_no_collapse_when_expansion_meets_a_literal_spelling():
    doc = suggest()
    doc["candidates"][0]["aliases"] += [
        {"spelling": "Alza", "provenance": "invented register A, view 4"},
        {"spelling": "Alsa", "provenance": "invented register A, view 3"}]
    doc["suggestions"].append({"cluster_id": "demo-alfa", "spelling": "Alza",
                               "provenance": "invented register A, view 4"})
    doc["suggestions"].append({"cluster_id": "demo-alfa", "spelling": "Alsa",
                               "provenance": "invented register A, view 3"})
    plan = vq.build_queries(doc, person(), "invented-source", expand_letters=True)
    rows = [r for r in plan["query_groups"][0]["queries"] if r["spelling"] == "Alza"]
    origins = sorted(r["origin"] for r in rows)
    assert origins == ["expansion", "expansion", "suggestion", "suggestion"]


def test_determinism_same_input_and_permuted_clusters():
    doc = suggest("ambiguous")
    plan1 = vq.build_queries(doc, person(), "invented-source")
    plan2 = vq.build_queries(copy.deepcopy(doc), person(), "invented-source")
    assert plan1 == plan2
    shuffled = copy.deepcopy(doc)
    shuffled["candidates"] = list(reversed(shuffled["candidates"]))
    plan3 = vq.build_queries(shuffled, person(), "invented-source")
    assert [g["cluster_id"] for g in plan3["query_groups"]] == \
           [g["cluster_id"] for g in plan1["query_groups"]]
    for g1, g3 in zip(plan1["query_groups"], plan3["query_groups"]):
        assert g1["queries"] == g3["queries"]


def test_non_ascii_spelling_and_provenance_survive_verbatim():
    doc = suggest()
    doc["suggestions"][0]["provenance"] = " invented note\nsecond line "
    doc["candidates"][0]["aliases"][0]["provenance"] = " invented note\nsecond line "
    plan = vq.build_queries(doc, {"given": "Démo", "surnames": "Álfa Inventado",
                                  "event_year": 1905, "surname_target": "Álfa"},
                            "invented-source")
    rows = plan["query_groups"][0]["queries"]
    assert rows[0]["provenance"] == " invented note\nsecond line "
    assert {r["query"] for r in rows} == {'"Alfa Inventado, Démo"', '"Démo Alfa Inventado"',
                                          '"Alpha Inventado, Démo"', '"Démo Alpha Inventado"'}


def test_ledger_keys_use_ledger_slug_semantics():
    plan = vq.build_queries(suggest(), person(), "Invented Source")
    assert plan["person"]["person_key"] == "demo-alfa-inventado"
    assert plan["source"]["source_key"] == "invented-source"
    keys = {r["query"]: r["query_key"] for r in plan["query_groups"][0]["queries"]}
    assert keys['"Alfa Inventado, Demo"'] == "alfa-inventado-demo"
    assert keys['"Demo Alfa Inventado"'] == "demo-alfa-inventado"


@pytest.mark.parametrize("bad", [
    {"given": " ", "surnames": "Alfa", "event_year": 1905, "surname_target": "Alfa"},
    {"given": "Demo", "surnames": "  ", "event_year": 1905, "surname_target": "Alfa"},
    {"given": "Demo", "surnames": "Alfa", "event_year": True, "surname_target": "Alfa"},
    {"given": "Demo", "surnames": "Alfa", "event_year": 0, "surname_target": "Alfa"},
    {"given": "Demo", "surnames": "Alfa", "event_year": 2101, "surname_target": "Alfa"},
    {"given": "Demo", "surnames": "Alfa", "event_year": "1905", "surname_target": "Alfa"},
])
def test_bad_person_refused(bad):
    with pytest.raises(ValueError, match="person"):
        vq.build_queries(suggest(), bad, "invented-source")


def test_lone_surrogate_refused():
    doc = suggest()
    doc["suggestions"][0]["provenance"] = "invented \ud800 note"
    with pytest.raises(ValueError, match="surrogate"):
        vq.build_queries(doc, person(), "invented-source")


def test_blank_source_refused():
    with pytest.raises(ValueError, match="source"):
        vq.build_queries(suggest(), person(), "   ")


def _cli(tmp_path, doc, p, *extra):
    sj = tmp_path / "suggest.json"
    pj = tmp_path / "person.json"
    sj.write_text(json.dumps(doc), encoding="utf-8")
    pj.write_text(json.dumps(p), encoding="utf-8")
    return subprocess.run([sys.executable, MODULE, str(sj), str(pj),
                           "invented-source", *extra],
                          capture_output=True, text=True, check=False)


def test_cli_exit_codes_and_output(tmp_path):
    run = _cli(tmp_path, suggest(), person())
    assert run.returncode == 0, run.stderr
    plan = json.loads(run.stdout)
    assert plan["query_count"] == 4
    run = _cli(tmp_path, suggest("ambiguous"), person())
    assert run.returncode == 1
    assert json.loads(run.stdout)["status"] == "ambiguous"
    run = _cli(tmp_path, suggest("unmatched"), person())
    assert run.returncode == 0
    assert json.loads(run.stdout)["query_count"] == 0


def test_cli_invalid_input_exits_2(tmp_path):
    doc = suggest()
    doc["status"] = "guessed"
    run = _cli(tmp_path, doc, person())
    assert run.returncode == 2 and "status" in run.stderr
    sj = tmp_path / "suggest.json"
    sj.write_text('{"a": 1, "a": 2}', encoding="utf-8")
    pj = tmp_path / "person.json"
    pj.write_text(json.dumps(person()), encoding="utf-8")
    run = subprocess.run([sys.executable, MODULE, str(sj), str(pj), "invented-source"],
                         capture_output=True, text=True, check=False)
    assert run.returncode == 2 and "duplicate" in run.stderr


def test_cli_expansion_flags(tmp_path):
    run = _cli(tmp_path, suggest(), person(1905), "--expand-year")
    assert run.returncode == 0
    rows = json.loads(run.stdout)["query_groups"][0]["queries"]
    assert any(r["origin"] == "expansion" for r in rows)


# ---- V2 (Servidor review) ----

# 1) matched: suggestions must mirror the candidate's aliases exactly.

def test_matched_rejects_trimmed_suggestions():
    doc = suggest()
    doc["suggestions"] = doc["suggestions"][:1]  # one alias dropped
    with pytest.raises(ValueError, match="suggestions"):
        vq.build_queries(doc, person(), "invented-source")


def test_matched_rejects_foreign_cluster_id_in_suggestions():
    doc = suggest()
    doc["suggestions"][0]["cluster_id"] = "demo-other"
    with pytest.raises(ValueError, match="suggestions"):
        vq.build_queries(doc, person(), "invented-source")


def test_matched_rejects_altered_provenance_in_suggestions():
    doc = suggest()
    doc["suggestions"][1]["provenance"] = "invented register A, view 9"
    with pytest.raises(ValueError, match="suggestions"):
        vq.build_queries(doc, person(), "invented-source")


def test_matched_accepts_reordered_suggestions():
    doc = suggest()
    doc["suggestions"] = list(reversed(doc["suggestions"]))
    plan = vq.build_queries(doc, person(), "invented-source")
    assert plan["query_count"] == 4


# 2) year window clamps to the valid range.

def test_year_window_clamps_at_bounds():
    low = vq.build_queries(suggest(), person(1), "invented-source", expand_year=True)
    wins = [r for r in low["query_groups"][0]["queries"] if r["origin"] == "expansion"]
    assert all(r["year_from"] == 1 and r["year_to"] == 2 for r in wins)
    assert all(r["query"].endswith(" 1-2") for r in wins)
    high = vq.build_queries(suggest(), person(2100), "invented-source", expand_year=True)
    wins = [r for r in high["query_groups"][0]["queries"] if r["origin"] == "expansion"]
    assert all(r["year_from"] == 2099 and r["year_to"] == 2100 for r in wins)
    assert all(r["query"].endswith(" 2099-2100") for r in wins)


# 3) explicit matching target segment; particles are never rewritten blindly.

def test_particle_first_surname_substitutes_the_target_not_the_first_word():
    plan = vq.build_queries(suggest(), person(surnames="de Alfa Inventado", target="Alfa"),
                            "invented-source")
    rows = plan["query_groups"][0]["queries"]
    queries = {r["query"] for r in rows}
    assert '"Demo de Alfa Inventado"' in queries
    assert '"Demo de Alpha Inventado"' in queries
    assert not any("Alfa Alfa" in q or "Alpha Alfa" in q for q in queries)
    assert all(r["segment"] == "Alfa" and r["position"] == 1 for r in rows)


def test_target_must_be_a_single_matching_segment():
    with pytest.raises(ValueError, match="surname_target"):
        vq.build_queries(suggest(), person(target="Inventado"), "invented-source")
    with pytest.raises(ValueError, match="surname_target"):
        vq.build_queries(suggest(), person(target="Alfa Inventado"), "invented-source")
    with pytest.raises(ValueError, match="surname_target"):
        vq.build_queries(suggest(), person(surnames="Alfa de Alfa", target="Alfa"),
                         "invented-source")


def test_multi_word_target_refused_even_when_it_folds():
    doc = suggest()
    doc["input"] = "Alfa Inventado"
    doc["normalized_alias"] = "alfa inventado"
    with pytest.raises(ValueError, match="surname_target"):
        vq.build_queries(doc, person(target="Alfa Inventado"), "invented-source")


def test_target_matching_folds_accents_and_case():
    plan = vq.build_queries(suggest(), person(surnames="ÁLFA Inventado", target="Álfa"),
                            "invented-source")
    rows = plan["query_groups"][0]["queries"]
    assert {r["query"] for r in rows} >= {'"Demo Alfa Inventado"', '"Demo Alpha Inventado"'}
    assert all(r["position"] == 0 and r["segment"] == "Álfa" for r in rows)


# 4) total stable order including provenance under permuted input.

def test_total_order_stable_under_permutation():
    doc = suggest()
    doc["candidates"][0]["aliases"] = list(reversed(doc["candidates"][0]["aliases"]))
    doc["suggestions"] = list(reversed(doc["suggestions"]))
    plan = vq.build_queries(doc, person(), "invented-source")
    base = vq.build_queries(suggest(), person(), "invented-source")
    assert plan["query_groups"] == base["query_groups"]
    order = [(r["cluster_id"], r["spelling"], r["provenance"],
              r["origin"], "" if r["expansion"] is None else r["expansion"]["kind"],
              r["query"]) for r in base["query_groups"][0]["queries"]]
    assert order == sorted(order)


# 5) strict booleans for the expansion flags.

@pytest.mark.parametrize("flag", ["expand_letters", "expand_year"])
@pytest.mark.parametrize("bad", [1, "yes", None, 0.5])
def test_expansion_flags_require_bool(flag, bad):
    with pytest.raises(ValueError, match=flag):
        vq.build_queries(suggest(), person(), "invented-source", **{flag: bad})
