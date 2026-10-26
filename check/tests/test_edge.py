import copy

from gwt_check import selftest
from gwt_check.checks import run_checks
from gwt_check.synth import big_tree
from gwt_check.tree import Tree


def _checks(d):
    return {f["check"] for f in run_checks(Tree(d))[0]}


def test_an_empty_tree_runs():
    assert run_checks(Tree({t: [] for t in selftest.clean_tree()}))[0] == []


def test_people_with_no_name_no_events_or_missing_lists_do_not_crash():
    d = copy.deepcopy(selftest.clean_tree())
    d["people"].append(selftest._base("I0900", gender=2))
    d["people"].append(selftest._base("I0901", gender=1, primary_name={}))
    _checks(d)


def test_accents_and_case_do_not_hide_a_namesake():
    d = big_tree(60, seed=2)
    a, b = d["people"][10], d["people"][11]
    for p, first in ((a, "José"), (b, "JOSE")):
        p["primary_name"]["first_name"] = first
        p["primary_name"]["surname_list"][0]["surname"] = "Peña"
    ev = {e["handle"]: e for e in d["events"]}
    for p in (a, b):
        ev[p["event_ref_list"][0]["ref"]]["date"]["dateval"] = [5, 5, 1750, False]
    assert "dup-person-birth" in _checks(d)


def test_odd_dates_are_read_without_crashing():
    d = copy.deepcopy(selftest.clean_tree())
    for e, val in zip(d["events"], ([31, 2, 1800, False], [0, 13, 1800, False], [0, 0, 0, False],
                                    [1, 1, -50, False], [99, 99, 99999, False])):
        e["date"]["dateval"] = val
    d["events"][-1]["date"] = {"_class": "Date", "text": "hacia 1800", "modifier": 6, "dateval": [0, 0, 0, False]}
    _checks(d)


def test_a_parent_cycle_is_found():
    d = copy.deepcopy(selftest.clean_tree())
    # make I0001's own mother's mother's ... child be the same person: I0001 is a child of a family he heads
    fam = d["families"][0]
    first = next(p for p in d["people"] if p["gramps_id"] == "I0001")
    fam["father_handle"] = first["handle"]
    first["family_list"].append(fam["handle"])
    assert "own-ancestor" in _checks(d)


def test_references_to_things_that_are_not_there_are_reported_not_fatal():
    d = copy.deepcopy(selftest.clean_tree())
    d["people"][0]["event_ref_list"].append(selftest._ref("hE_gone", role="Primary"))
    d["families"][0]["father_handle"] = "hI_gone"
    d["citations"][0]["source_handle"] = "hS_gone"
    assert "dangling-ref" in _checks(d)


def test_the_report_of_one_tree_is_the_same_twice():
    d = big_tree(200, seed=9)
    assert run_checks(Tree(copy.deepcopy(d)))[0] == run_checks(Tree(copy.deepcopy(d)))[0]
