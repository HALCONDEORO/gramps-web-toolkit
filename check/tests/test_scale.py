import json
import os
import subprocess
import sys

from gwt_check.checks import run_checks
from gwt_check.synth import big_tree
from gwt_check.tree import Tree


def test_the_invented_tree_has_the_size_asked_and_no_broken_links():
    d = big_tree(400, seed=3)
    assert len(d["people"]) == 400
    kinds = {f["check"] for f in run_checks(Tree(d))[0]}
    assert not kinds & {"orphan-note", "orphan-event", "missing-ref", "dangling-reference"}


def test_the_report_order_does_not_depend_on_the_hash_seed(tmp_path):
    p = tmp_path / "t.json"
    d = big_tree(300, seed=5)
    for i, q in enumerate(d["people"]):  # few names, so there are findings to put in order
        q["primary_name"]["surname_list"][0]["surname"] = "Apellido" + str(i % 4)
    p.write_text(json.dumps(d))
    outs = []
    for seed in ("1", "2"):
        r = subprocess.run([sys.executable, "-m", "gwt_check", "check", str(p), "--json", "-", "--limit", "0"],
                           capture_output=True, text=True, env={**os.environ, "PYTHONHASHSEED": seed})
        outs.append(r.stdout)
    assert outs[0] == outs[1]


def test_common_surnames_do_not_make_the_duplicate_search_quadratic():
    d = big_tree(6000, seed=7)
    for p in d["people"]:
        for s in p["primary_name"]["surname_list"]:
            s["surname"] = "Comun"
    import time
    t = time.time()
    run_checks(Tree(d))
    assert time.time() - t < 8
