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


def test_the_scale_script_runs_every_command_on_an_invented_tree_and_on_a_dump(tmp_path):
    from gwt_check import synth
    real = tmp_path / "real.json.gz"
    assert synth.main(["50", str(real), "--seed", "3"]) == 0
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)}
    out = subprocess.run([sys.executable, "-m", "gwt_check.scale", "--people", "50", "--real", str(real)],
                         capture_output=True, text=True, env=env, timeout=120)
    assert out.returncode == 0, out.stderr
    rows = [l.split()[:2] for l in out.stdout.splitlines()[1:]]
    assert {r[0] for r in rows} == {"invented", "real"}
    assert {r[1] for r in rows} == {"check", "repos", "fill-places", "links", "cites"}
