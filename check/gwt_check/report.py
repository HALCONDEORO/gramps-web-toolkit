"""Build read-only check reports independently of command-line dispatch."""

from __future__ import annotations

from . import compare
from .checks import CHECKS, INFO_CHECKS, run_checks
from .compare import ahnentafel, compare_ahnentafel, diff
from .tree import BadInput, Tree


def check_names(names: set) -> None:
    unknown = set(names) - set(CHECKS)
    if unknown:
        raise BadInput(f"--ignore-check: no such check {sorted(unknown)}; the checks are {sorted(CHECKS)}")


def _scope(tree, since, tag):
    if tree.partial:
        raise BadInput(f"{tree.source}: a .gramps backup can be the earlier tree (--since), not the one checked")
    tagged = None
    if tag:
        if not tree.has_tags:
            raise BadInput(f"--tag needs a dump with tags; {tree.source or 'this tree'} has none")
        tagged = tree.tagged(tag)
    scope = None
    if since is not None or tag:
        scope = set(tagged or ())
        if since is not None:
            scope |= {(t, h) for t, objs in tree.objs.items() for h in objs if h not in since.objs[t]}
    return scope, tagged


def _comparison(tree, since, tagged, root, findings, report):
    if since is not None:
        f, report["diff"] = diff(since, tree, tagged)
        findings += f
        if root:
            f, report["ahnentafel"] = compare_ahnentafel(since, tree, root)
            findings += f
    elif root:
        a = ahnentafel(tree, root)
        if not a:
            raise BadInput(f"--root {root}: no such person")
        report["ahnentafel"] = {"numbers": len(a), "people": len(set(a.values()))}
        if a.complete_depth is not None:
            report["ahnentafel"]["truncated"] = f"over {compare.MAX_POSITIONS} positions; walked up to generation {a.complete_depth}"
            findings.append({"check": "ahnentafel-truncated", "key": f"root {root}",
                             "detail": report["ahnentafel"]["truncated"]})


def _classify(findings, report, known, ignore):
    known = dict.fromkeys(known, "") if isinstance(known, set) else (known or {})
    ignore = ignore or set()
    check_names(ignore)
    for f in findings:
        f["ignored"] = f["check"] in ignore
        f["known"] = f["ignored"] or (f["check"], f["key"]) in known
        if known.get((f["check"], f["key"])):
            f["reason"] = known[(f["check"], f["key"])]
    report["findings"] = findings
    for f in findings:
        f["info"] = f["check"] in INFO_CHECKS
    report["new"] = [f for f in findings if not f["known"] and not f["info"]]
    report["ignored"] = {c: sum(1 for f in findings if f["check"] == c) for c in sorted(ignore)}


def check(tree: Tree, *, since: Tree | None = None, tag: str | None = None, root: str | None = None,
          known: dict | set | None = None, ignore: set | None = None) -> dict:
    """Every check on one tree. Returns the report; report["new"] are the findings not in `known`."""
    scope, tagged = _scope(tree, since, tag)
    findings, info = run_checks(tree, scope, root)
    report = {"counts": tree.counts(), "scope": None if scope is None else len(scope), "info": info}
    _comparison(tree, since, tagged, root, findings, report)
    _classify(findings, report, known, ignore)
    return report
