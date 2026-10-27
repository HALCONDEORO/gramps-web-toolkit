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
    # compared with the same tree where every surname differs, so a busy machine slows both runs alike
    import time

    def best_of_two(common):
        d = big_tree(6000, seed=7)
        for i, p in enumerate(d["people"]):
            for s in p["primary_name"]["surname_list"]:
                s["surname"] = "Comun" if common else f"Apellido{i}"
        best = None
        for _ in range(2):
            t = time.perf_counter()
            run_checks(Tree(d))
            best = min(best or 1e9, time.perf_counter() - t)
        return best

    assert best_of_two(True) < 8 * best_of_two(False) + 0.5


def test_the_real_dump_summary_shows_only_digits(monkeypatch, capsys, tmp_path):
    from gwt_check import scale
    monkeypatch.setattr(scale, "COMMANDS", (["check"],))
    monkeypatch.setattr(scale, "run", lambda cmd, tree: ("check", 0.1, 1, 0, "12 findings in Maria Garcia I0001"))
    assert scale.main(["--people", "50", "--real", str(tmp_path / "x.json")]) == 0
    real = [l for l in capsys.readouterr().out.splitlines() if l.startswith("real")]
    assert real and "Maria" not in real[0] and "I0001" not in real[0] and real[0].endswith("12 0001")


def test_findings_of_one_check_come_out_in_id_order():
    from gwt_check.checks import id_order
    d = big_tree(300, seed=5)
    for i, q in enumerate(d["people"]):
        q["primary_name"]["surname_list"][0]["surname"] = "Apellido" + str(i % 4)
    d["people"].reverse()   # the dump lists people backwards: only the sort can put the findings in order
    items = run_checks(Tree(d))[0]
    by_check = {}
    for f in items:
        by_check.setdefault(f["check"], []).append(f["key"])
    assert len(items) > 5
    for keys in by_check.values():
        assert keys == sorted(keys, key=id_order)
