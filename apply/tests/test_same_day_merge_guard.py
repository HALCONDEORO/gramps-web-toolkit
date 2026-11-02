"""Synthetic, in-memory merge regressions; no API, credentials or tree writes."""

from copy import deepcopy

import pytest

from gwt_apply.dates import parse_date
from gwt_apply.ops import Applier, Conflict, stable_handle


class Memory:
    """Return live objects deliberately: even adding _class must be detected."""

    def __init__(self):
        self.data = {kind: {} for kind in ("Person", "Family", "Event", "Citation")}
        self.calls = []

    def get(self, kind, handle):
        return self.data[kind].get(handle)

    def handle_for_gramps_id(self, kind, gid):
        return next((h for h, obj in self.data[kind].items() if obj.get("gramps_id") == gid), None)

    def merge(self, kind, winner, loser, *args):
        self.calls.append("merge")
        del self.data[kind][loser]


class Probe(Applier):
    def _merge_note(self, op, *args):
        self.c.calls.append("note")
        self.obj("Person", op["winner"]).setdefault("note_list", []).append(stable_handle(op["key"] + "#merge"))

    def save(self, before, after):
        self.c.calls.append("save")
        self.c.data["Person"][after["handle"]] = after


def person(memory, handle, first="Néral", surname="Cloudfern"):
    obj = dict(handle=handle, gramps_id="I" + handle, primary_name={
        "first_name": first, "surname_list": [{"surname": surname}]})
    memory.data["Person"][handle] = obj
    return obj


def event(memory, child, handle, kind="Birth", date="1888-04-12", pages=()):
    citations = []
    for i, (source, page) in enumerate(pages):
        ch = handle + "c" + str(i)
        memory.data["Citation"][ch] = dict(handle=ch, source_handle=source, page=page)
        citations.append(ch)
    memory.data["Event"][handle] = dict(handle=handle, type=kind, date=parse_date(date), citation_list=citations)
    child.setdefault("event_ref_list", []).append(dict(ref=handle, role="Primary"))
    child["birth_ref_index"] = 0


def family(memory, child, handle, father=None, mother=None, frel="Birth", mrel="Birth"):
    ref = dict(ref=child["handle"])
    if frel is not None:
        ref["frel"] = frel
    if mrel is not None:
        ref["mrel"] = mrel
    memory.data["Family"][handle] = dict(handle=handle, father_handle=father, mother_handle=mother,
                                        child_ref_list=[ref])
    child.setdefault("parent_family_list", []).append(handle)


def fixture(kind="Birth", parents=False, acts=False):
    memory = Memory()
    a, b = person(memory, "1"), person(memory, "2", " neral ", "CLOUDFERN")
    for i, child in enumerate((a, b)):
        event(memory, child, "e" + str(i), kind, pages=[("source", "act " + str(i))] if acts else [])
        if parents:
            dad = person(memory, str(3 + i), "Daven" + str(i), "Mistbranch")
            family(memory, child, "f" + str(i), father=dad["handle"])
    op = dict(op="merge_person", key="synthetic", ruling="synthetic", winner="I1", loser="I2")
    return memory, a, b, op


def blocked(memory, op):
    before = deepcopy(memory.data)
    applier = Probe(memory)
    with pytest.raises(Conflict) as exc:
        applier.merge_person(op)
    assert "I1" in str(exc.value) and "I2" in str(exc.value)
    assert "review" in str(exc.value).lower()
    assert not any(private in str(exc.value) for private in ("Néral", "Cloudfern", "act 0", "Daven"))
    assert memory.data == before
    assert memory.calls == []  # no note, save or merge
    assert not applier.wrote and applier.actions == []


@pytest.mark.parametrize("kind", ["Birth", "Baptism"])
@pytest.mark.parametrize("parents,acts", [(False, False), (True, False), (False, True), (True, True)])
@pytest.mark.parametrize("reverse", [False, True])
def test_four_cases(kind, parents, acts, reverse):
    memory, a, b, op = fixture(kind, parents, acts)
    if reverse:
        op["winner"], op["loser"] = op["loser"], op["winner"]
    if parents or acts:
        blocked(memory, op)
    else:
        Probe(memory).merge_person(op)
        assert memory.calls == ["note", "merge"]


def test_reason_vitals_and_direct_namesake_waiver_cannot_bypass():
    memory, a, b, op = fixture(parents=True)
    op.update(reason="same person", use_loser=["birth"], allow_namesake=True,
              namesake_reason="same person", namesake_of=["I1"])
    blocked(memory, op)


def test_partial_mother_conflict_with_shared_father():
    memory, a, b, op = fixture()
    person(memory, "3", "Shared father")
    person(memory, "4", "Mother A")
    person(memory, "5", "Mother B")
    family(memory, a, "fa", father="3", mother="4")
    family(memory, b, "fb", father="3", mother="5")
    blocked(memory, op)


@pytest.mark.parametrize("relation", [None, "Adopted", "Foster", "Unknown"])
def test_only_explicit_birth_relation_counts(relation):
    memory, a, b, op = fixture(parents=True)
    for fam in memory.data["Family"].values():
        ref = fam["child_ref_list"][0]
        ref.pop("frel")
        if relation is not None:
            ref["frel"] = relation
    Probe(memory).merge_person(op)
    assert memory.calls == ["note", "merge"]


@pytest.mark.parametrize("reverse", [False, True])
def test_all_alternate_families_and_parent_names(reverse):
    memory, a, b, op = fixture(parents=True)
    # A common alternative biological father removes disjointness, regardless of order.
    family(memory, b, "alternate", father="3")
    if reverse:
        b["parent_family_list"].reverse()
    Probe(memory).merge_person(op)
    assert memory.calls == ["note", "merge"]
    memory, a, b, op = fixture(parents=True)
    memory.data["Person"]["4"]["primary_name"] = deepcopy(memory.data["Person"]["3"]["primary_name"])
    Probe(memory).merge_person(op)
    assert memory.calls == ["note", "merge"]


def test_missing_parent_names_do_not_supply_conflict():
    memory, a, b, op = fixture(parents=True)
    memory.data["Person"]["3"].pop("primary_name")
    Probe(memory).merge_person(op)
    assert memory.calls == ["note", "merge"]


def test_conflict_in_alternate_family_and_explicit_child_ref_required():
    memory, a, b, op = fixture()
    person(memory, "3", "Dad A")
    person(memory, "4", "Dad B")
    family(memory, a, "empty")
    family(memory, b, "empty2")
    family(memory, a, "alternate", father="3")
    family(memory, b, "alternate2", father="4")
    blocked(memory, op)
    memory.data["Family"]["alternate"]["child_ref_list"] = []
    Probe(memory).merge_person(op)
    assert memory.calls == ["note", "merge"]


@pytest.mark.parametrize("date", ["abt 1888-04-12", "est 1888-04-12", "calc 1888-04-12",
                                "bet 1888-04-12 and 1888-04-12", "from 1888-04-12 to 1888-04-13",
                                "1888", "1888-04", "1900-04-12"])
def test_uncertain_partial_or_distant_dates_are_outside_guard(date):
    memory, a, b, op = fixture(parents=True)
    memory.data["Event"]["e1"]["date"] = parse_date(date)
    Probe(memory).merge_person(op)
    assert memory.calls == ["note", "merge"]


@pytest.mark.parametrize("patch", [dict(dateval=[31, 2, 1888, False]), dict(calendar=99),
                                  dict(calendar=None), dict(dateval=[12, 4, 1888, True]),
                                  dict(newyear=1), dict(dateval=[12, 4, 1888, False, 13, 4, 1888, False])])
def test_invalid_unknown_or_non_civil_dates(patch):
    memory, a, b, op = fixture(parents=True)
    for ev in memory.data["Event"].values():
        ev["date"].update(patch)
    Probe(memory).merge_person(op)
    assert memory.calls == ["note", "merge"]


@pytest.mark.parametrize("change", ["different_name", "different_type", "witness", "missing_date", "missing_name"])
def test_exact_name_and_corresponding_primary_event_required(change):
    memory, a, b, op = fixture(parents=True)
    if change == "different_name":
        b["primary_name"]["first_name"] = "Nera"
    elif change == "different_type":
        memory.data["Event"]["e1"]["type"] = "Baptism"
    elif change == "witness":
        b["event_ref_list"][0]["role"] = "Witness"
    elif change == "missing_date":
        memory.data["Event"]["e1"].pop("date")
    else:
        a.pop("primary_name")
        b.pop("primary_name")
    Probe(memory).merge_person(op)
    assert memory.calls == ["note", "merge"]


@pytest.mark.parametrize("left,right", [
    ([("source", "same")], [("source", "same"), ("source", "extra")]),
    ([("source", "one")], [("other", "two")]),
    ([("source", "")], [("source", "two")]),
    ([(None, "one")], [(None, "two")]),
    ([], [("source", "two")]),
])
def test_shared_act_additional_pages_and_missing_evidence(left, right):
    memory, a, b, op = fixture()
    event(memory, a, "left", pages=left)
    event(memory, b, "right", pages=right)
    Probe(memory).merge_person(op)
    assert memory.calls == ["note", "merge"]


def test_other_event_type_citations_do_not_conflict():
    memory, a, b, op = fixture()
    event(memory, a, "death1", "Death", pages=[("source", "one")])
    event(memory, b, "death2", "Death", pages=[("source", "two")])
    Probe(memory).merge_person(op)
    assert memory.calls == ["note", "merge"]


def test_other_days_do_not_supply_act_conflicts():
    memory, a, b, op = fixture()
    event(memory, a, "later1", date="1900-04-12", pages=[("source", "one")])
    event(memory, b, "later2", date="1910-04-12", pages=[("source", "two")])
    Probe(memory).merge_person(op)
    assert memory.calls == ["note", "merge"]


def test_shared_act_across_multiple_same_day_events():
    memory, a, b, op = fixture(acts=True)
    event(memory, a, "extra1", pages=[("source", "shared")])
    event(memory, b, "extra2", pages=[("source", "shared")])
    Probe(memory).merge_person(op)
    assert memory.calls == ["note", "merge"]


def test_common_source_disjointness_even_with_other_sources():
    memory, a, b, op = fixture()
    event(memory, a, "left", pages=[("source", "one"), ("other", "shared")])
    event(memory, b, "right", pages=[("source", "two"), ("other", "shared")])
    blocked(memory, op)


def test_completed_merge_rerun_is_first_and_unchanged():
    memory, a, b, op = fixture()
    applier = Probe(memory)
    applier.merge_person(op)
    # After completion even conflicting new evidence must not change the rerun path.
    a["event_ref_list"] = [{"ref": "unreadable", "role": "Primary"}]
    before = deepcopy(memory.data)
    applier.merge_person(op)
    assert memory.data == before and memory.calls == ["note", "merge"]
    assert applier.actions[-1].startswith("skip:")


def test_completed_merge_does_not_even_read_event_evidence():
    memory, a, b, op = fixture(parents=True, acts=True)
    del memory.data["Person"]["2"]
    a["note_list"] = [stable_handle(op["key"] + "#merge")]
    # Evidence reads fail if the guard accidentally moves before _merge_done.
    del memory.data["Event"]
    before = deepcopy(memory.data)
    Probe(memory).merge_person(op)
    assert memory.data == before and memory.calls == []
