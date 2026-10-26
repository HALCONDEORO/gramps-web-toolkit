"""Load test of gramps-web-toolkit against the disposable "LoadTest" tree. Invented data only.

Runs inside the container. Refuses to run unless the server is the loadtest container and the tree
is named LoadTest. Writes /out/result.json and prints a table.
"""
import base64
import json
import os
import re
import resource
import subprocess
import sys
import time
import urllib.request

URL = os.environ["LT_URL"].rstrip("/")            # http://gramps-loadtest-grampsweb-1:5000
EXPECT_HOST = os.environ["LT_WEB"]                # the loadtest web container, exactly
EXPECT_HOST = os.environ["LT_WEB"]                # the loadtest web container, exactly
USER = os.environ.get("LT_USER", "loadadmin")
PASS = os.environ["LT_PASS"]
OUT = "/out"
res = {"steps": []}


def api(path, token=None, data=None, method=None):
    h = {"Content-Type": "application/json"}
    if token:
        h["Authorization"] = "Bearer " + token
    req = urllib.request.Request(URL + "/api" + path, data=data, headers=h, method=method)
    return urllib.request.urlopen(req, timeout=120)


def fatal(msg):
    """Stop at the first failure and keep what was measured so far."""
    res["aborted"] = msg
    os.makedirs(OUT, exist_ok=True)
    json.dump(res, open(f"{OUT}/result.json", "w"), indent=1)
    sys.exit("ABORT: " + msg)


def token_tree(tok):
    """The tree a token is bound to: Gramps Web puts it in the token, so every call made with it hits that tree."""
    body = tok.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4))).get("tree")


def fatal(msg):
    """Stop at the first failure and keep what was measured so far."""
    res["aborted"] = msg
    os.makedirs(OUT, exist_ok=True)
    json.dump(res, open(f"{OUT}/result.json", "w"), indent=1)
    sys.exit("ABORT: " + msg)


def token_tree(tok):
    """The tree a token is bound to: Gramps Web puts it in the token, so every call made with it hits that tree."""
    body = tok.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4))).get("tree")


def login():
    r = api("/token/", data=json.dumps({"username": USER, "password": PASS}).encode())
    return json.load(r)["access_token"]


def step(name, cmd, env=None, timeout=3600):
    t = time.time()
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env)
    try:
        out, _ = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        p.kill()
        out = "TIMEOUT"
    secs = time.time() - t
    ru = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss // 1024
    row = {"step": name, "seconds": round(secs, 1), "exit": p.returncode, "peak_mb_so_far": ru,
           "last": [x for x in out.strip().splitlines() if x.strip()][-3:]}
    res["steps"].append(row)
    print(f"{name:34} {secs:8.1f}s exit {p.returncode} peak {ru} MB  | {row['last'][-1][:90] if row['last'] else ''}", flush=True)
    if p.returncode != 0:
        fatal(f"{name} exited {p.returncode} (137 means the memory cap was hit: that is the result)")
    return p.returncode, out


tok = login()
# guard: only the disposable tree, checked before anything is read or written
from urllib.parse import urlparse  # noqa: E402
if urlparse(URL).hostname != EXPECT_HOST or urlparse(URL).scheme != "http":
    sys.exit(f"refusing: the host must be exactly {EXPECT_HOST!r} over plain http inside the loadtest network")
trees = json.load(api("/trees/", tok))
print("trees:", [t.get("name") for t in trees])
loadtest = [t for t in trees if t.get("name") == "LoadTest"]
if len(loadtest) != 1:
    sys.exit("refusing: there must be exactly one tree named LoadTest")
TREE = loadtest[0]["id"]
if token_tree(tok) != TREE:
    sys.exit("refusing: the login is bound to a tree other than LoadTest")
res["tree"] = {"name": "LoadTest"}


def assert_tree():
    """Called before every phase: a fresh login must still land in the LoadTest tree."""
    if token_tree(login()) != TREE:
        fatal("a login no longer lands in the LoadTest tree")


assert_tree()
# 1. page latency per type, three pages each
for t in ("people", "families", "events", "places", "citations", "sources", "notes", "media"):
    ts, size, total = [], 0, None
    for page in (1, 2, 3):
        t0 = time.time()
        r = api(f"/{t}/?page={page}&pagesize=1000", tok)
        b = r.read()
        ts.append(time.time() - t0)
        size += len(b)
        total = r.headers.get("X-Total-Count")
    row = {"type": t, "total": total, "sec_per_1000": round(sum(ts) / 3, 2), "kb_per_1000": size // 3 // 1024}
    res.setdefault("pages", []).append(row)
    print(f"page {t:10} total {total} {row['sec_per_1000']} s / 1000 objects, {row['kb_per_1000']} KB", flush=True)

cfg = "/tmp/lt.cfg"
with open(cfg, "w") as f:
    f.write(f"GWT_URL={URL}\nGWT_USERNAME={USER}\nGWT_PASSWORD={PASS}\nGWT_TREE={TREE}\n")
os.chmod(cfg, 0o600)

# ids for the batch
people = json.load(api("/people/?page=1&pagesize=2000&keys=gramps_id", tok))
ids = [p["gramps_id"] for p in people]


def ops(n, tag, prefix):
    with open(f"/tmp/{prefix}.jsonl", "w") as f:
        for i in ids[:n]:
            f.write(json.dumps({"op": "set_attribute", "key": f"{prefix}-{i}", "ruling": "load test, disposable tree",
                                "target": {"person": i}, "type": "LoadTest", "value": tag}) + "\n")
    return f"/tmp/{prefix}.jsonl"


# 2. full dump (may hit the container memory limit: exit 137 is the finding)
rc, _ = step("gwt-check dump (4 workers)", ["gwt-check", "dump", "/tmp/full.json.gz", "--config", cfg])
have_dump = os.path.exists("/tmp/full.json.gz")
if have_dump:
    step("gwt-check check on the dump", ["gwt-check", "check", "/tmp/full.json.gz", "--limit", "0"])
    step("gwt-apply dry-run 2000 ops (snapshot)", ["gwt-apply", "--snapshot", "/tmp/full.json.gz", ops(2000, "LT-snap", "snap")])

assert_tree()
# 3. batch write: 300 set_attribute on the live tree, verify, repeat (idempotence), request count from the log
f300 = ops(300, "LT-batch", "b300")
step("gwt-apply dry-run 300 (live reads)", ["gwt-apply", "--config", cfg, f300])
step("gwt-apply --apply 300", ["gwt-apply", "--config", cfg, "--apply", "--log", "/tmp/apply1.log", f300])
rc, out = step("gwt-apply --verify 300", ["gwt-apply", "--config", cfg, "--verify", f300])
rc, out = step("gwt-apply --apply 300 again", ["gwt-apply", "--config", cfg, "--apply", "--log", "/tmp/apply2.log", f300])
summary = [x for x in out.splitlines() if re.search(r"\b\d+ (done|skip)", x)][-1:]
res["idempotent_second_run_summary"] = summary
m = re.search(r"(\d+) done", summary[0]) if summary else None
if not summary or (m and int(m.group(1)) != 0):
    fatal(f"the second run was not idempotent: {summary}")
for n in ("apply1", "apply2"):
    try:
        import glob
        p = glob.glob(f"/tmp/{n}*.log")
        res[f"{n}_log_lines"] = sum(1 for _ in open(p[0])) if p else None
    except OSError:
        pass
print("api log lines (first apply, second apply):", res.get("apply1_log_lines"), res.get("apply2_log_lines"))
os.makedirs(OUT, exist_ok=True)
json.dump(res, open(f"{OUT}/result.json", "w"), indent=1)
print("done; result in /out/result.json")
