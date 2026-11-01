"""Invented, offline controls for the before/after note rule."""
import copy
import json

import pytest

from gwt_check import checks, selftest
from gwt_check.cli import main
from gwt_check.tree import Tree


def fixture(mod=1, text="after1700", citation=False):
    data = selftest.clean_tree()
    event = selftest._get(data, "events", "E0001")
    event["date"] = selftest._date(1700, modifier=mod)
    note = selftest._base("N0800", text={"string": text})
    data["notes"].append(note)
    owner = selftest._get(data, "citations", "C0001") if citation else event
    owner["note_list"] = [note["handle"]]
    if citation:
        event["citation_list"] = [owner["handle"]]
    return data


def findings(data, scope=None):
    return [f for f in checks.run_checks(Tree(data), scope=scope)[0]
            if f["check"] == "date-modifier-note"]


@pytest.mark.parametrize("mod,text", [(1, "after1700"), (2, "before1700"),
                                       (1, "AFTER 1700."), (1, "después de 1700"),
                                       (2, "avant 1700"), (1, "après 1700")])
@pytest.mark.parametrize("citation", [False, True])
def test_opposite_boundaries_are_findings(mod, text, citation):
    data = fixture(mod, text, citation)
    original = copy.deepcopy(data)
    [row] = findings(data)
    assert row["key"] == "E0001"
    assert "N0800" in row["detail"]
    assert ("citation C0001" if citation else "event E0001") in row["detail"]
    assert "before 1700" in row["detail"] and "after 1700" in row["detail"]
    assert data == original


@pytest.mark.parametrize("mod,text", [(1, "before1700"), (2, "after1700"),
    (1, "not after1700"), (1, "no después de 1700"), (1, "pas après 1700"),
    (1, "possibly after1700"), (1, "after1700?"), (1, "after1700 or before1700"),
    (1, "not before1700; after1700"), (1, "another person: after1700"),
    (1, 'quoted "after1700"'), (1, "after17000"), (1, "after 0000"),
    (1, "after 1700-02-30"), (1, "after 1700-00"), (1, "after 1700-00-00"), (1, "after 1701"), (0, "after1700"), (3, "after1700")])
def test_matching_or_unasserted_text_stays_quiet(mod, text):
    assert findings(fixture(mod, text)) == []


def test_event_and_citation_notes_have_no_precedence():
    data = fixture(text="before1700")
    note = selftest._base("N0801", text="after1700")
    data["notes"].append(note)
    selftest._get(data, "citations", "C0001")["note_list"] = [note["handle"]]
    event = selftest._get(data, "events", "E0001")
    event["citation_list"] = ["hC0001"]
    event["date"] = selftest._date()  # disagreement is visible even without a usable event date
    [row] = findings(data)
    assert all(t in row["detail"] for t in ("event E0001", "citation C0001", "N0800", "N0801"))
    event["citation_list"].reverse()
    assert findings(data) == [row]


@pytest.mark.parametrize("scope", [set(), {("people", "hI0001")}, {("notes", "hN0001")}])
def test_unrelated_scope_stays_quiet(scope):
    assert findings(fixture(), scope) == []


@pytest.mark.parametrize("scope", [{("events", "hE0001")}, {("notes", "hN0800")},
                                    {("citations", "hC0001")}])
def test_changed_evidence_is_in_scope(scope):
    assert len(findings(fixture(citation=True), scope)) == 1


@pytest.mark.parametrize("field,value", [("calendar", 1), ("newyear", 1), ("quality", 1),
                                         ("quality", 2), ("modifier", 7)])
def test_unsupported_or_qualified_event_dates_stay_quiet(field, value):
    data = fixture()
    selftest._get(data, "events", "E0001")["date"][field] = value
    assert findings(data) == []


def test_cli_fails_for_contradiction_without_proposing_a_correction(tmp_path, capsys):
    tree = tmp_path / "invented.json"
    report = tmp_path / "report.json"
    tree.write_text(json.dumps(fixture()))
    assert main(["check", str(tree), "--json", str(report)]) == 1
    output = json.loads(report.read_text())
    assert any(f["check"] == "date-modifier-note" and not f["info"] for f in output["new"])
    assert "proposals" not in output
    assert "FAIL date-modifier-note" in capsys.readouterr().out


def test_help_explains_read_only_rule(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["check", "--help"])
    assert exc.value.code == 0
    text = capsys.readouterr().out
    assert "date-modifier-note" in text and "corrections" in text


def test_partial_date_precision_is_preserved():
    data = fixture(text="after 1700-02-03")
    event = selftest._get(data, "events", "E0001")
    event["date"] = selftest._date(1700, 2, 3, modifier=1)
    assert len(findings(data)) == 1
    event["date"] = selftest._date(1700, 2, modifier=1)
    assert findings(data) == []  # different boundaries are deliberately outside this rule


@pytest.mark.parametrize("dv", [[0, 0, 1700, True], [3, 0, 1700, False], [30, 2, 1700, False]])
def test_malformed_or_dual_event_boundaries_are_not_compared(dv):
    data = fixture()
    selftest._get(data, "events", "E0001")["date"]["dateval"] = dv
    assert findings(data) == []


def test_conflicting_notes_remain_visible_when_event_date_agrees_with_one():
    data = fixture(text="before1700")
    data["notes"].append(selftest._base("N0801", text="after1700"))
    citation = selftest._get(data, "citations", "C0001")
    citation["note_list"] = ["hN0801"]
    selftest._get(data, "events", "E0001")["citation_list"] = ["hC0001"]
    [row] = findings(data)
    assert all(t in row["detail"] for t in ("event E0001 date", "event E0001 note N0800", "citation C0001 note N0801"))


def test_baseline_and_ignore_keep_finding_visible(tmp_path):
    tree = tmp_path / "invented.json"
    tree.write_text(json.dumps(fixture()))
    report = tmp_path / "report.json"
    assert main(["check", str(tree), "--baseline", str(tree), "--json", str(report)]) == 0
    [row] = [f for f in json.loads(report.read_text())["findings"] if f["check"] == "date-modifier-note"]
    assert row["known"]
    # The invented year also trips existing chronology checks; accept those separately.
    known = tmp_path / "known.json"
    known.write_text(json.dumps([f for f in json.loads(report.read_text())["findings"]
                                 if f["check"] != "date-modifier-note"]))
    assert main(["check", str(tree), "--known", str(known), "--ignore-check",
                 "date-modifier-note", "--json", str(report)]) == 0
    assert any(f["check"] == "date-modifier-note" for f in json.loads(report.read_text())["findings"])
