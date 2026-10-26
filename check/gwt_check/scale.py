"""Run every offline command on an invented tree and, when given, on a real dump, side by side.

    python -m gwt_check.scale --people 20000 [--real dump.json.gz]

Prints seconds, peak memory, exit code and the command's last summary line for each. A real dump is only
read: its path is not stored and nothing about it is written anywhere, so the output is counts and times.
"""

from __future__ import annotations

import argparse
import os
import resource
import subprocess
import sys
import tempfile
import time

COMMANDS = (["check", "--limit", "0"], ["repos"], ["fill-places"], ["links"], ["cites"], ["republican"])


def run(cmd: list[str], tree: str) -> tuple[str, float, int, int, str]:
    """One command in a child process: its own peak memory comes from a wrapper, not from the family."""
    code = ("import resource,runpy,sys;sys.argv=['gwt_check']+sys.argv[1:];"
            "import atexit;atexit.register(lambda: sys.stderr.write('PEAK_KB=%d\\n'%resource.getrusage(resource.RUSAGE_SELF).ru_maxrss));"
            "runpy.run_module('gwt_check',run_name='__main__')")
    start = time.time()
    r = subprocess.run([sys.executable, "-c", code, cmd[0], tree, *cmd[1:]], capture_output=True, text=True)
    secs = time.time() - start
    peak = 0
    for line in r.stderr.splitlines():
        if line.startswith("PEAK_KB="):
            peak = int(line[8:]) // 1024
    lines = [x for x in r.stdout.strip().splitlines() if x.strip()]
    return " ".join(cmd[:1]), secs, peak, r.returncode, (lines[-1][:90] if lines else r.stderr.strip()[-90:])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--people", type=int, default=20000)
    ap.add_argument("--real", help="a real dump, read only; same commands, results beside the invented tree")
    a = ap.parse_args(argv)
    with tempfile.TemporaryDirectory() as tmp:
        synth = os.path.join(tmp, "synth.json.gz")
        # written by a child, so this process stays small: a child inherits its parent's peak memory
        subprocess.run([sys.executable, "-m", "gwt_check.synth", str(a.people), synth], check=True,
                       capture_output=True)
        trees = [("invented", synth)] + ([("real", a.real)] if a.real else [])
        print(f"{'tree':9} {'command':12} {'sec':>6} {'MB':>5} exit  last line")
        bad = 0
        for cmd in COMMANDS:
            for label, path in trees:
                name, secs, peak, rc, last = run(cmd, path)
                bad += rc > 1  # 1 is "findings"; more is a crash
                print(f"{label:9} {name:12} {secs:6.1f} {peak:5d} {rc:4d}  {last}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
