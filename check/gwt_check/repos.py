"""`gwt-check repos`: find repositories by name or type, and list what is not linked. Read-only."""

from __future__ import annotations

import json

from .checks import norm
from .tree import Tree


def repository_report(tree: Tree, *, name: str | None = None, rtype: str | None = None) -> dict:
    """Repositories matching `name` (a part of the name, compared like people's names) and `rtype`, each with the
    sources that list it; plus the sources that list no repository and the repositories no source lists."""
    repos, sources = tree.objs["repositories"], tree.objs["sources"]
    used: dict[str, list[dict]] = {h: [] for h in repos}
    bare = []
    for s in sources.values():
        refs = s.get("reporef_list") or []
        if not refs:
            bare.append({"id": s.get("gramps_id"), "title": s.get("title", "")})
        for r in refs:
            if r.get("ref") in used:
                used[r["ref"]].append({"id": s.get("gramps_id"), "title": s.get("title", ""),
                                       "call_number": r.get("call_number", ""), "media_type": r.get("media_type", "")})
    found = []
    for h, o in repos.items():
        kind = o.get("type") if isinstance(o.get("type"), str) else (o.get("type") or {}).get("string", "")
        if name and norm(name) not in norm(o.get("name") or ""):
            continue
        if rtype and norm(rtype) != norm(kind):
            continue
        found.append({"id": o.get("gramps_id"), "name": o.get("name") or "", "type": kind,
                      "urls": [u.get("path") for u in o.get("urls") or []], "sources": used[h]})
    found.sort(key=lambda r: r["id"] or "")
    return {
        "repositories": found,
        "sources_without_repository": sorted(bare, key=lambda r: r["id"] or ""),
        "repositories_without_sources": [{"id": o.get("gramps_id"), "name": o.get("name", "")}
                                         for h, o in sorted(repos.items(), key=lambda x: x[1].get("gramps_id") or "")
                                         if not used[h]],
    }


def print_repository_report(rep: dict, *, unlinked: bool, limit: int) -> None:
    for r in rep["repositories"]:
        print(f"{r['id']}  {r['name']}  [{r['type']}]  {len(r['sources'])} sources")
    if not rep["repositories"]:
        print("no repository matches")
    if unlinked:
        print(f"\nrepositories no source lists: {len(rep['repositories_without_sources'])}")
        for r in rep["repositories_without_sources"][:limit]:
            print(f"  {r['id']}  {r['name']}")
        bare = rep["sources_without_repository"]
        print(f"sources that list no repository: {len(bare)}")
        for s in bare[:limit]:
            print(f"  {s['id']}  {s['title']}")
        if len(bare) > limit:
            print(f"  ... {len(bare) - limit} more (--limit, or --json)")


def _starts_with_name(pub: str, name: str) -> bool:
    """The name opens the text as whole words: "archive, vol 2" names "Archive", "archivex" does not."""
    return pub.startswith(name) and not (len(pub) > len(name) and pub[len(name)].isalnum())


def suggest_links(tree: Tree) -> dict:
    """For each source with no repository, the repository whose name its own publication info starts with, when
    exactly one does. A source that names no repository, or two, is left out: proposals only, never a guess."""
    repos = {h: (o.get("gramps_id"), o.get("name") or "") for h, o in tree.objs["repositories"].items()}
    proposals, ambiguous = [], []
    for s in tree.objs["sources"].values():
        if s.get("reporef_list"):
            continue
        pub = norm(s.get("pubinfo") or "")
        hit = [(rid, name) for rid, name in repos.values() if name and _starts_with_name(pub, norm(name))]
        if len(hit) == 1:
            proposals.append({"source": s.get("gramps_id"), "title": s.get("title", ""), "pubinfo": s.get("pubinfo", ""),
                              "repository": hit[0][0], "name": hit[0][1]})
        elif hit:
            ambiguous.append({"source": s.get("gramps_id"), "repositories": [r for r, _ in hit]})
    key = lambda x: x["source"] or ""
    return {"proposals": sorted(proposals, key=key), "ambiguous": sorted(ambiguous, key=key)}


def print_suggestions(rep: dict, limit: int) -> None:
    for x in rep["proposals"][:limit]:
        print(f"  {x['source']} -> {x['repository']} {x['name']}   (publication info: {x['pubinfo']})")
    if len(rep["proposals"]) > limit:
        print(f"  ... {len(rep['proposals']) - limit} more (--limit, or --json)")
    print(f"proposed links: {len(rep['proposals'])}; sources naming more than one repository: {len(rep['ambiguous'])}")
    print("proposals only: read each source before applying one")


def write_link_ops(rep: dict, path: str) -> int:
    """One gwt-apply link_repository op per proposal, with an empty ruling, so gwt-apply refuses the file until
    someone has read each source and written the ruling. Refuses to replace a file that exists."""
    with open(path, "x", encoding="utf-8") as fh:
        for x in rep["proposals"]:
            fh.write(json.dumps({"op": "link_repository", "key": f"link-repo-{x['source']}", "ruling": "",
                                 "source": x["source"], "repository": x["repository"]}, ensure_ascii=False) + "\n")
    return len(rep["proposals"])
