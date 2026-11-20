"""Synthetic, read-only regressions for parent keys from an earlier apply."""

from copy import deepcopy

import pytest

from gwt_apply.dupes import preflight
from gwt_apply.ops import Applier, stable_handle, validate


def person(handle, ref, first, surname, **fields):
    return {"handle": handle, "gramps_id": ref, "primary_name": {
        "first_name": first, "surname_list": [{"surname": surname}]}, **fields}


class MemoryTree:
    """Only the three existing list reads are available to preflight."""

    def __init__(self, parents=1, resumed=False):
        self.rows = {
            "Person": [
                person(stable_handle("prior-parent"), "I9101", "Luma", "Testvale"),
                person("synthetic-child", "I9102", "Neri", "Testvale",
                       parent_family_list=["synthetic-family"]),
                person(stable_handle("other-parent"), "I9103", "Sola", "Other"),
                person(stable_handle("unrelated-parent"), "I9104", "Neri", "Testvale Distant"),
            ],
            "Event": [],
            "Family": [{"handle": "synthetic-family", "gramps_id": "F9101"}],
        }
        if parents:
            self.rows["Family"][0]["father_handle"] = stable_handle("prior-parent")
        if parents == 2:
            self.rows["Family"][0]["mother_handle"] = stable_handle("other-parent")
        if resumed:
            self.rows["Person"].append(person(
                stable_handle("new-child"), "I9105", "Neri", "Testvale Other"))
        self.reads = []

    def list_all(self, cls, fields):
        self.reads.append((cls, fields))
        return self.rows[cls]


def changes(father="key:prior-parent", mother=None, prior_op=False):
    ops = [
        {"op": "add_person", "key": "new-child", "ruling": "synthetic",
         "first_name": "Neri", "surname": "Testvale Other"},
        {"op": "add_family", "key": "new-family", "ruling": "synthetic",
         "father": father, "children": ["key:new-child"]},
    ]
    if mother:
        ops[1]["mother"] = mother
    if prior_op:
        ops.insert(0, {"op": "add_person", "key": "prior-parent", "ruling": "synthetic",
                       "first_name": "Luma", "surname": "Testvale"})
    return ops


def warnings(ops, **tree_options):
    client = MemoryTree(**tree_options)
    before_rows, before_ops = deepcopy(client.rows), deepcopy(ops)
    assert validate(ops) == []
    result = preflight(ops, client)
    assert client.reads == [
        ("Person", ["primary_name", "gramps_id", "event_ref_list", "parent_family_list", "family_list"]),
        ("Event", ["type", "date"]),
        ("Family", ["father_handle", "mother_handle", "gramps_id"]),
    ]
    assert client.rows == before_rows
    assert ops == before_ops
    return result


@pytest.mark.parametrize("prior_op", [False, True])
@pytest.mark.parametrize("resumed", [False, True])
@pytest.mark.parametrize("parents", [1, 2])
def test_known_parent_key_matches_id_before_comparison(parents, resumed, prior_op):
    options = dict(parents=parents, resumed=resumed)
    expected = warnings(changes("I9101", "I9103" if parents == 2 else None, prior_op), **options)
    actual = warnings(changes("key:prior-parent", "key:other-parent" if parents == 2 else None,
                              prior_op), **options)
    assert len(expected) == 1
    assert "I9102" in expected[0] and "a parent in common" in expected[0]
    assert actual == expected


def test_parent_key_resolves_to_existing_handle():
    # Resolution itself was already correct; the gap was in the duplicate pass.
    class ResolverTree(MemoryTree):
        def exists(self, cls, handle):
            return any(row["handle"] == handle for row in self.rows[cls])

    assert Applier(ResolverTree()).resolve("Person", "key:prior-parent") == stable_handle("prior-parent")


@pytest.mark.parametrize("mother", ["I9103", "key:other-parent"])
def test_mother_alone_can_supply_the_common_parent(mother):
    expected = warnings(changes("I9104", "I9103"), parents=2)
    actual = warnings(changes("key:unrelated-parent", mother), parents=2)
    assert len(expected) == 1 and "a parent in common" in expected[0]
    assert actual == expected


@pytest.mark.parametrize("ref", ["key:unknown-parent", "key:unrelated-parent", "I9104"])
@pytest.mark.parametrize("parents", [0, 1, 2])
def test_unknown_or_unrelated_parent_does_not_match(ref, parents):
    assert warnings(changes(ref), parents=parents) == []


def test_unknown_keys_are_not_collapsed_to_an_empty_parent():
    ops = changes("key:unknown-parent")
    ops += [
        {"op": "add_person", "key": "another-child", "ruling": "synthetic",
         "first_name": "Neri Second", "surname": "Testvale Third"},
        {"op": "add_family", "key": "another-family", "ruling": "synthetic",
         "mother": "key:another-unknown", "children": ["key:another-child"]},
    ]
    assert warnings(ops, parents=0) == []


@pytest.mark.parametrize("ref", ["I9101", "key:prior-parent"])
@pytest.mark.parametrize("waived", ["I9102", "I9104"])
def test_namesake_waiver_still_names_the_specific_candidate(ref, waived):
    ops = changes(ref)
    ops[0].update(allow_namesake=True, namesake_reason="Synthetic distinct person", namesake_of=[waived])
    result = warnings(ops)
    if waived == "I9102":
        assert result == []
    else:
        assert len(result) == 1 and "I9102" in result[0] and "a parent in common" in result[0]


def test_link_child_to_new_family_uses_the_same_parent_identity():
    ops = changes()
    del ops[1]["children"]
    ops.append({"op": "link_child", "key": "link-child", "ruling": "synthetic",
                "child": "key:new-child", "family": "key:new-family"})
    result = warnings(ops)
    assert len(result) == 1 and "I9102" in result[0] and "a parent in common" in result[0]
