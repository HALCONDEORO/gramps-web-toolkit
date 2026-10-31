import copy
import json

import pytest

from gwt_check import selftest
from gwt_check.citegaps import dates_in, propose_citations, write_ops
from gwt_check.tree import Tree


def _tree(page="Acta de nacimiento n°3, 5 de mayo de 1860", date=(5, 5, 1860), event_cited=False, year_ok=True):
    """The clean tree: E0001 has no citation, its owner I0001 is cited by C0001 with the given page."""
    d = copy.deepcopy(selftest.clean_tree())
    ev = selftest._get(d, "events", "E0001")
    ev["citation_list"] = ["hC0001"] if event_cited else []
    ev["date"] = {"dateval": [date[0], date[1], date[2], False]} if date else {}
    selftest._get(d, "citations", "C0001")["page"] = page
    selftest._get(d, "people", "I0001")["citation_list"] = ["hC0001"]
    return d


def _rep(d):
    return propose_citations(Tree(d))


def test_the_owners_citation_that_writes_the_event_date_is_proposed_with_its_quote():
    r = _rep(_tree())
    assert [(x["event"], x["citation"], x["date"]) for x in r["proposals"] if x["event"] == "E0001"] == [
        ("E0001", "C0001", "1860-05-05")]


def test_a_citation_that_writes_another_date_is_not_proposed():
    assert not [x for x in _rep(_tree(page="Acta de nacimiento, 6 de mayo de 1860"))["proposals"] if x["event"] == "E0001"]


def test_an_event_that_already_has_a_citation_is_left_alone():
    assert not [x for x in _rep(_tree(event_cited=True))["proposals"] if x["event"] == "E0001"]


def test_a_citation_of_another_act_is_held_back_not_proposed():
    r = _rep(_tree(page="Acta de matrimonio, 5 de mayo de 1860"))
    assert not [x for x in r["proposals"] if x["event"] == "E0001"]
    assert [x for x in r["held_back"] if x["event"] == "E0001"]


def test_nobody_born_or_dying_after_the_living_cutoff_is_proposed():
    assert not [x for x in _rep(_tree(page="Acta de nacimiento, 5/5/1960", date=(5, 5, 1960)))["proposals"]
                if x["event"] == "E0001"]


def test_an_event_with_only_a_year_is_counted_not_proposed():
    r = _rep(_tree(date=None))
    assert not [x for x in r["proposals"] if x["event"] == "E0001"] and r["undated"] >= 1


@pytest.mark.parametrize("text", ["5/5/1860", "05.05.1860", "1860-05-05", "5 de mayo de 1860", "5 mai 1860", "5 May 1860"])
def test_the_date_forms_it_reads(text):
    assert (1860, 5, 5) in dates_in("acta " + text + " folio")


def test_the_ops_file_has_an_empty_ruling_and_is_never_replaced(tmp_path):
    p = str(tmp_path / "ops.jsonl")
    n = write_ops(_rep(_tree()), p)
    row = json.loads(open(p).readline())
    assert n >= 1 and row["op"] == "attach_existing" and row["ruling"] == "" and row["object"] == {"citation": "C0001"} and row["attach"] == [{"event": "E0001"}]
    with pytest.raises(FileExistsError):
        write_ops(_rep(_tree()), p)


def test_1er_is_the_first_day():
    assert (1860, 5, 1) in dates_in("acte du 1er mai 1860")


def test_a_citation_that_records_a_negative_search_is_never_proposed():
    d = _tree(page="Acta de nacimiento, 5 de mayo de 1860 (negativo: sin fila)")
    r = _rep(d)
    assert not [x for x in r["proposals"] + r["held_back"] if x["event"] == "E0001"]
