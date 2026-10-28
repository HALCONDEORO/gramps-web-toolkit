import json

import pytest

from gwt_check.cli import main
from gwt_check.insight import discrepancies_report, citations_report, insight, parse_citation, witnesses_report
from gwt_check.tree import Tree


def date(y, m=0, d=0):
    return {"dateval": [d, m, y, False], "modifier": 0, "quality": 0, "calendar": 0, "newyear": 0, "text": ""}


def note(h, gid, text):
    return {"handle": h, "gramps_id": gid, "text": {"string": text}}


def base():
    return {
        "people": [], "families": [], "events": [], "citations": [], "sources": [{"handle": "s1", "gramps_id": "S1"}],
        "media": [], "notes": [], "repositories": [], "tags": [],
        "places": [{"handle": "pl1", "gramps_id": "L1", "name": {"value": "Lowtown"}},
                   {"handle": "pl2", "gramps_id": "L2", "name": {"value": "Highfield"}}],
    }


def cit(h, gid, page, notes=(), source="s1"):
    return {"handle": h, "gramps_id": gid, "page": page, "note_list": list(notes), "source_handle": source}


def event(h, gid, typ, dt=None, place="", cites=()):
    return {"handle": h, "gramps_id": gid, "type": typ, "date": dt or {}, "place": place, "citation_list": list(cites), "note_list": []}


def person(h, gid, events=(), parents=(), cites=(), notes=(), refs=()):
    return {"handle": h, "gramps_id": gid, "event_ref_list": [{"ref": e} for e in events], "parent_family_list": list(parents),
            "citation_list": list(cites), "note_list": list(notes), "person_ref_list": list(refs), "birth_ref_index": 0 if events else -1}


def test_parse_citation_reads_what_the_text_states_and_nothing_else():
    p = parse_citation("Acte n°12/1851, vue 84-85, registry https://example.org/a/1.")
    assert p == {"act": 12, "year": 1851, "act_year": True, "image": "84-85", "url": "https://example.org/a/1"}
    assert parse_citation("page 3, register 1880-1890") == {"image": "3", "year": 1880}
    assert parse_citation("a loose remark") == {}


def test_citations_count_structure_and_find_acts_behind_several_claims():
    d = base()
    d["citations"] = [cit("c1", "C1", "Acte n°7/1850, vue 10 | Nacimiento"), cit("c2", "C2", "acta n°7/1850, imagen 10"),
                      cit("c3", "C3", "free text")]
    r = citations_report(Tree(d))
    assert (r["total"], r["with_act_number"], r["with_image"], r["with_own_date"]) == (3, 2, 2, 0)
    g = r["same_act_in_several_citations"]
    assert [{k: v for k, v in x.items() if k != "quotes"} for x in g] == [{"citations": ["C1", "C2"], "act": 7, "year": 1850, "source": "S1"}]


def test_year_outside_event_only_compares_the_same_kind_of_act():
    d = base()
    d["citations"] = [cit("c1", "C1", "Acte n°7/1907 | Nacimiento"), cit("c2", "C2", "Acte n°8/1907 | Matrimonio"),
                      cit("c3", "C3", "Margen del acta de nacimiento n°9/1907 | Nacimiento"), cit("c4", "C4", "Acte n°10/1871 | Nacimiento")]
    d["events"] = [event("e1", "E1", "Birth", date(1871), cites=["c1", "c2", "c3", "c4"])]
    odd = citations_report(Tree(d))["year_outside_event"]
    assert [(o["citation"], o["text_year"]) for o in odd] == [("C1", 1907)]  # the matrimony and the margin note are not birth acts


def test_discrepancies_list_the_owner_and_the_citations_and_guess_no_status():
    d = base()
    d["notes"] = [note("n1", "N1", "DISCREPANCIA: DNI 1923, libro 1922 (C000999)"), note("n2", "N2", "Conflict was resolved"), note("n3", "N3", "plain"), note("n4", "N4", "date differs by a year; se conserva la fecha civil")]
    d["citations"] = [cit("c1", "C1", "p", notes=["n1", "n2", "n3", "n4"])]
    r = discrepancies_report(Tree(d))
    assert r["notes_with_a_conflict_marker"] == 3
    first = r["items"][0]
    assert first["note"] == "N1" and first["on"] == ["C1"] and first["citations_named"] == ["C000999"]
    assert all("status" not in i for i in r["items"])  # whether a conflict is still open is never read from its words


def test_witnesses_report_recurring_names_without_calling_them_relatives():
    d = base()
    d["notes"] = [note("n1", "N1", "Valor afirmado: boda; testigos Marlow Pierre (56, boulanger) y Tanner Luc (44, maréchal). Oficial Quill Remy."),
                  note("n2", "N2", "Valor afirmado: nacimiento; témoins Marlow Pierre (60 ans, tisserand)")]
    d["citations"] = [cit("c1", "C1", "p", notes=["n1"]), cit("c2", "C2", "p", notes=["n2"])]
    r = witnesses_report(Tree(d))
    assert r["citations_with_witness_names"] == 2
    assert r["names_in_two_or_more_citations"] == [{"name": "marlow pierre", "citations": ["C1", "C2"], "association_recorded": False}]
    assert r["recurring_without_recorded_association"] == 1
    assert "witness is not a relative" in r["note"]


def test_the_command_reads_a_dump_and_writes_nothing(tmp_path, capsys):
    d = base()
    d["citations"] = [cit("c1", "C1", "Acte n°7/1850")]
    path = tmp_path / "dump.json"
    path.write_text(json.dumps({"objects": d}))
    assert main(["insight", str(path), "--only", "citations", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert list(out) == ["citations"] and out["citations"]["total"] == 1
    assert main(["insight", str(path), "--only", "nope"]) == 2
    assert main(["insight", str(path)]) == 0
    assert "== witnesses" in capsys.readouterr().out
    assert set(insight(Tree(d))) == {"citations", "discrepancies", "witnesses"}


def test_a_childs_act_backing_a_parents_event_is_only_a_possible_mismatch():
    d = base()
    d["citations"] = [cit("c1", "C1", "Acte de naissance n°24/1852 | Naissance")]
    d["events"] = [event("e1", "E1", "Birth", date(1807), cites=["c1"])]  # the 1852 act is the child's; it names the parent
    r = citations_report(Tree(d))
    assert [o["citation"] for o in r["year_outside_event"]] == ["C1"]
    assert "possible mismatches to review" in r["note"] and "not errors" in r["note"]


def named(p, first, surname):
    p["primary_name"] = {"first_name": first, "surname_list": [{"surname": surname}]}
    return p


def test_a_recurring_witness_with_a_recorded_association_is_marked_and_the_other_listed_first(capsys):
    d = base()
    d["notes"] = [note("n1", "N1", "testigos Marlow Pierre (56 ans) y Tanner Luc (44 ans)"),
                  note("n2", "N2", "témoins Marlow Pierre (60 ans) y Tanner Luc (45 ans)")]
    d["citations"] = [cit("c1", "C1", "p", notes=["n1"]), cit("c2", "C2", "p", notes=["n2"])]
    witness = named(person("p1", "P1"), "Pierre", "Marlow")
    holder = named(person("p2", "P2", refs=[{"ref": "p1", "rel": "Witness"}]), "Anne", "Other")
    d["people"] = [witness, holder]
    r = witnesses_report(Tree(d))
    by = {x["name"]: x["association_recorded"] for x in r["names_in_two_or_more_citations"]}
    assert by == {"marlow pierre": True, "tanner luc": False} and r["recurring_without_recorded_association"] == 1
    from gwt_check.insight import print_insight
    print_insight({"witnesses": r})
    out = capsys.readouterr().out.splitlines()
    assert any("1 of the 2 names that recur have no association recorded" in x for x in out)
    assert out.index([x for x in out if "tanner luc" in x][0]) < out.index([x for x in out if "marlow pierre" in x][0])


def test_same_act_groups_carry_each_citations_own_text():
    d = base()
    d["citations"] = [cit("c1", "C1", "Acte n°7/1850 vue 3"), cit("c2", "C2", "Acte n°7/1850 | copy B")]
    g = citations_report(Tree(d))["same_act_in_several_citations"]
    assert len(g) == 1 and g[0]["quotes"]["C1"].startswith("Acte n°7/1850") and "copy B" in g[0]["quotes"]["C2"]


def test_mismatches_print_before_duplicate_groups(capsys):
    from gwt_check.insight import print_insight
    d = base()
    d["citations"] = [cit("c1", "C1", "Acte de naissance n°24/1852 | Naissance"), cit("c2", "C2", "Acte n°9/1800"), cit("c3", "C3", "Acte n°9/1800")]
    d["events"] = [event("e1", "E1", "Birth", date(1807), cites=["c1"])]
    print_insight({"citations": citations_report(Tree(d))})
    out = capsys.readouterr().out
    assert out.index("possible mismatch") < out.index("same act 9/1800")


@pytest.mark.parametrize("text,found", [
    ("foto-acta-vs-copia.png", False), ("Edad no coincide con el bautismo", True),
    ("Bautismo vs. censo: la edad difiere", True), ("Conflicto: resuelto, sin dudas", True), ("la nota no lo contradice", False),
])
def test_discrepancies_find_a_conflict_word_not_a_file_name_or_a_negated_phrase(text, found):
    d = base(); d["notes"] = [note("n", "N1", text)]
    assert bool(discrepancies_report(Tree(d))["items"]) is found
