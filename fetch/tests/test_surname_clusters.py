"""Offline contract for human-maintained search clusters; all fixtures invented."""
import contextlib
import copy
import io
import json
import tempfile
from pathlib import Path
from unittest import mock

import pytest

from gwt_fetch import surname_clusters as sc
from gwt_fetch.names import fold


def config():
    return {"clusters": [
        {"cluster_id": "demo-alfa", "aliases": [
            {"spelling": s, "provenance": "invented note A"}
            for s in ("Alfa", "Alpha", "Alfha")]},
        {"cluster_id": "demo-beta", "aliases": [
            {"spelling": s, "provenance": "invented note B"}
            for s in ("Beta", "Bheta")]},
    ]}


def test_explicit_membership_and_literal_provenance():
    doc = config()
    before = copy.deepcopy(doc)
    result = sc.suggest(doc, "ÁLPHA")
    assert result == {
        "input": "ÁLPHA", "normalized_alias": fold("ÁLPHA"),
        "status": "matched", "requires_human_review": True,
        "candidates": [{"cluster_id": "demo-alfa", "aliases": doc["clusters"][0]["aliases"]}],
        "suggestions": [{"cluster_id": "demo-alfa", **a} for a in doc["clusters"][0]["aliases"]],
    }
    assert doc == before
    result["candidates"][0]["aliases"][0]["spelling"] = "Changed"
    assert doc == before


@pytest.mark.parametrize("alias", ["Alffaa", "Al", "Alfa Beta", " Alfa ", "Al-fa"])
def test_unknown_no_phonetics_substrings_whitespace_or_punctuation_rewrite(alias):
    result = sc.suggest(config(), alias)
    assert result["status"] == "unmatched"
    assert result["candidates"] == result["suggestions"] == []
    assert result["requires_human_review"] is True


def test_beta_cluster_is_independent():
    result = sc.suggest(config(), "bheta")
    assert [a["spelling"] for a in result["suggestions"]] == ["Beta", "Bheta"]
    assert {a["cluster_id"] for a in result["suggestions"]} == {"demo-beta"}


def test_non_ascii_literal_spelling_and_provenance_survive():
    doc = config()
    doc["clusters"][0]["aliases"][0] = {"spelling": "Álfa", "provenance": " invented note A\nsecond reading "}
    result = sc.suggest(doc, "ALFA")
    assert result["suggestions"][0] == {
        "cluster_id": "demo-alfa", "spelling": "Álfa", "provenance": " invented note A\nsecond reading "}


@pytest.mark.parametrize("reverse", [False, True])
def test_overlap_is_ambiguous_never_first_wins_or_transitive(reverse):
    doc = config()
    doc["clusters"][1]["aliases"].append({"spelling": "Álfa", "provenance": "invented overlap"})
    if reverse:
        doc["clusters"].reverse()
    sc.validate_config(doc)  # Cross-cluster overlap is valid, never silently discarded.
    result = sc.suggest(doc, "alfa")
    assert result["status"] == "ambiguous"
    assert result["suggestions"] == []
    assert {c["cluster_id"] for c in result["candidates"]} == {"demo-alfa", "demo-beta"}
    assert result["requires_human_review"] is True
    # Overlap does not join the two clusters or propagate Beta to Alpha.
    assert [a["spelling"] for a in sc.suggest(doc, "Alpha")["suggestions"]] == ["Alfa", "Alpha", "Alfha"]


@pytest.mark.parametrize("change, message", [
    (lambda d: d["clusters"].append(copy.deepcopy(d["clusters"][0])), "duplicate cluster_id"),
    (lambda d: d["clusters"][1].update(cluster_id="demo-alfa"), "duplicate cluster_id"),
    (lambda d: d["clusters"][0]["aliases"].append({"spelling": "ÁLFA", "provenance": "invented conflict"}), "duplicate normalized alias"),
    (lambda d: d["clusters"][0]["aliases"].append(copy.deepcopy(d["clusters"][0]["aliases"][0])), "duplicate normalized alias"),
    (lambda d: d["clusters"][0].update(cluster_id=" "), "cluster_id"),
    (lambda d: d["clusters"][0].update(cluster_id=7), "cluster_id"),
    (lambda d: d["clusters"][0].update(cluster_id=" demo-alfa "), "cluster_id"),
    (lambda d: d["clusters"][0].update(aliases=[]), "aliases"),
    (lambda d: d["clusters"][0].update(aliases="Alfa"), "aliases"),
    (lambda d: d["clusters"][0]["aliases"][0].update(spelling=""), "spelling"),
    (lambda d: d["clusters"][0]["aliases"][0].update(spelling=" Alfa "), "spelling"),
    (lambda d: d["clusters"][0]["aliases"][0].update(spelling="\u0301"), "spelling"),
    (lambda d: d["clusters"][0]["aliases"][0].update(provenance=" "), "provenance"),
    (lambda d: d["clusters"][0]["aliases"][0].pop("provenance"), "fields"),
    (lambda d: d["clusters"][0].update(phonetic=True), "fields"),
    (lambda d: d.update(merge=True), "fields"),
])
def test_reject_invalid_config_even_for_unmatched_query(change, message):
    doc = config()
    change(doc)
    with pytest.raises(ValueError, match=message):
        sc.suggest(doc, "Unknown")


@pytest.mark.parametrize("doc", [None, [], {"clusters": None}, {"clusters": [None]},
                                    {"clusters": [{"cluster_id": "demo", "aliases": [None]}]}])
def test_bad_shapes_are_validation_errors(doc):
    with pytest.raises(ValueError):
        sc.validate_config(doc)


@pytest.mark.parametrize("alias", [None, 3, "", " ", "\u0301"])
def test_invalid_input(alias):
    with pytest.raises(ValueError):
        sc.suggest(config(), alias)


def test_empty_config_matches_nothing():
    assert sc.suggest({"clusters": []}, "Alfa")["status"] == "unmatched"


def invoke(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = sc.main(argv)
        except SystemExit as e:
            code = e.code
    return code, out.getvalue(), err.getvalue()


def test_cli_json_and_exit_codes_without_network():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "invented.json"
        path.write_text(json.dumps(config()), encoding="utf-8")
        with mock.patch("socket.socket", side_effect=AssertionError("network forbidden")):
            code, out, err = invoke([str(path), "Alpha"])
        assert code == 0 and not err
        assert json.loads(out) == sc.suggest(config(), "Alpha")
        code, out, _ = invoke([str(path), "Unknown"])
        assert code == 0 and json.loads(out)["status"] == "unmatched"
        doc = config()
        doc["clusters"][1]["aliases"].append({"spelling": "Alfa", "provenance": "invented overlap"})
        path.write_text(json.dumps(doc), encoding="utf-8")
        code, out, _ = invoke([str(path), "Alfa"])
        assert code == 1 and json.loads(out)["status"] == "ambiguous"
        for raw in ('{', '{"clusters": [], "clusters": []}', '{"clusters": [NaN]}',
                    json.dumps({"clusters": [{"cluster_id": "demo", "aliases": []}]})):
            path.write_text(raw, encoding="utf-8")
            code, out, err = invoke([str(path), "Alfa"])
            assert code == 2 and not out and "gwt-surname-clusters:" in err
        code, out, err = invoke([str(path.with_name("missing.json")), "Alfa"])
        assert code == 2 and not out and err
        path.write_bytes(b"\xff")
        assert invoke([str(path), "Alfa"])[0] == 2


def test_help_states_scope_and_review_requirement():
    code, out, err = invoke(["--help"])
    assert code == 0 and not err
    for phrase in ("human review", "offline", "identity", "ambiguous", "provenance"):
        assert phrase in out.lower()


@pytest.mark.parametrize("field", ["cluster_id", "spelling", "provenance", "input alias"])
@pytest.mark.parametrize("surrogate", ["\ud800", "\udfff"])
def test_cli_rejects_lone_surrogates_before_utf8_output(tmp_path, field, surrogate):
    doc = config()
    alias = "Alpha"
    if field == "input alias":
        alias += surrogate
    elif field == "cluster_id":
        doc["clusters"][0][field] += surrogate
    else:
        doc["clusters"][0]["aliases"][0][field] += surrogate
    path = tmp_path / "invented.json"
    # ASCII JSON escapes can carry lone surrogates even in a UTF-8 file.
    path.write_text(json.dumps(doc), encoding="utf-8")
    raw = io.BytesIO()
    with io.TextIOWrapper(raw, encoding="utf-8") as out:
        err = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = sc.main([str(path), alias])
        out.flush()
        assert code == 2
        assert raw.getvalue() == b""
        assert f"{field}:" in err.getvalue()


def test_supplementary_unicode_remains_literal():
    doc = config()
    doc["clusters"][0]["aliases"][0].update(spelling="Alfa\U00010428", provenance="invented note \U0001f4d6")
    result = sc.suggest(doc, "Alfa\U00010428")
    assert result["status"] == "matched"
    assert result["suggestions"][0]["spelling"] == "Alfa\U00010428"
    assert result["suggestions"][0]["provenance"] == "invented note \U0001f4d6"
