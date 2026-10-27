import copy
import json

from gwt_check import selftest
from gwt_check.cli import main
from gwt_check.republican import republican_report
from gwt_check.tree import Tree


def _tree(*texts):
    d = copy.deepcopy(selftest.clean_tree())
    for i, t in enumerate(texts):
        d["notes"].append({**copy.deepcopy(d["notes"][0]), "handle": f"hN90{i}", "gramps_id": f"N90{i}",
                           "text": {"_class": "StyledText", "string": t, "tags": []}})
    return d


def test_right_conversions_are_counted_and_wrong_ones_listed():
    d = _tree("Acta n. 3, 26 Frimaire an V = 16/12/1796.",
              "née le 10 germinal an IX (conversion) = 30/03/1801",   # the day is 31/03/1801
              "1er vendémiaire an 2 = 22/09/1793")
    rep = republican_report(Tree(d))
    assert (len(rep["rows"]), rep["differs"]) == (3, 1)
    bad = rep["rows"][0]
    assert (bad["id"], bad["stated"], bad["computed"]) == ("N901", "30/03/1801", "31/03/1801")


def test_a_year_outside_the_calendar_is_unreadable_and_text_without_an_equals_sign_is_ignored():
    d = _tree("5 floréal an 15 = 24/04/1807", "14 germinal an 13, vue 157, le 4 avril 1805")
    rep = republican_report(Tree(d))
    assert [r["status"] for r in rep["rows"]] == ["unreadable"]


def test_the_command_exits_1_on_a_difference_and_0_otherwise(tmp_path, capsys):
    for texts, code in (((), 0), (("10 germinal an 9 = 30/03/1801",), 1)):
        p = tmp_path / f"t{code}.json"
        p.write_text(json.dumps({"objects": _tree(*texts)}))
        assert main(["republican", str(p)]) == code
    assert "recomputed 31/03/1801" in capsys.readouterr().out
