"""Questions the Gramps Web server can answer itself, asked read-only: who changed what, how two people are
related, whether the server thinks a person is alive, a full-text search. Nothing here writes to the tree.

The server's own answers are a second opinion. They never replace a rule of this toolkit: `living` below shows
where the server and `possibly_living` disagree, so the owner can see it before trusting either."""

from __future__ import annotations

import datetime

from .checks import possibly_living
from .client import Client


def history(client: Client, limit: int = 20) -> list[dict]:
    """The newest transactions: id, time, user and how many objects each one changed, never their contents.
    The server answers the whole list oldest first and ignores `pagesize` and `sort` here, so this sorts it."""
    _, rows = client.get("/transactions/history/", {})
    rows = sorted(rows, key=lambda r: r.get("timestamp") or 0, reverse=True)[:limit]
    out = []
    for r in rows:
        conn = r.get("connection")
        out.append({"id": r.get("id"), "timestamp": r.get("timestamp"), "description": r.get("description", ""),
                    "user": conn.get("user") if isinstance(conn, dict) else None,
                    "changes": len(r.get("changes") or [])})
    return out


def can_undo(client: Client, transaction_id) -> dict:
    """Ask whether the server could undo one transaction without a conflict. Asks only; it never undoes."""
    _, res = client.get(f"/transactions/history/{transaction_id}/undo", {})
    return res if isinstance(res, dict) else {"result": res}


def relation(client: Client, handle1: str, handle2: str, all_paths: bool = False) -> list[str] | str:
    """How `handle1` is related to `handle2`, in the server's words."""
    _, res = client.get(f"/relations/{handle1}/{handle2}" + ("/all" if all_paths else ""), {})
    if all_paths:  # an unrelated pair comes back as [{}]
        return [r["relationship_string"] for r in (res if isinstance(res, list) else []) if r.get("relationship_string")]
    return res.get("relationship_string", "") if isinstance(res, dict) else ""


def search(client: Client, query: str, limit: int = 10, types: str | None = None) -> list[dict]:
    """Full-text hits as object type, handle and score: the text itself stays on the server."""
    params = {"query": query, "pagesize": limit}
    if types:
        params["type"] = types
    _, rows = client.get("/search/", params)
    return [{"type": r.get("object_type"), "handle": r.get("object", {}).get("handle"),
             "gramps_id": r.get("object", {}).get("gramps_id"), "score": r.get("score")} for r in rows[:limit]]


def living_diff(client: Client, people: list[dict], events: dict, *, year: int | None = None,
                workers: int = 8) -> dict:
    """Compare the server's `living` answer with `possibly_living` for each person of a dump.

    `server_only` are the ones the server calls alive and this toolkit does not, even counting undated people as
    possibly alive: the cases to look at first. Gramps IDs only."""
    from concurrent.futures import ThreadPoolExecutor
    year = year or datetime.date.today().year

    def ask(p):
        try:
            _, res = client.get(f"/living/{p['handle']}", {})
            return p["gramps_id"], bool(res.get("living")) if isinstance(res, dict) else None
        except Exception:  # one failed person must not hide the rest
            return p["gramps_id"], None

    with ThreadPoolExecutor(workers) as ex:
        srv = dict(ex.map(ask, people))
    out = {"server_only": [], "tool_only": [], "agree": 0, "unknown": []}
    for p in people:
        s = srv[p["gramps_id"]]
        if s is None:
            out["unknown"].append(p["gramps_id"])
            continue
        t = possibly_living(p, events, year, undated=True)
        if s and not t:
            out["server_only"].append(p["gramps_id"])
        elif t and not s:
            out["tool_only"].append(p["gramps_id"])
        else:
            out["agree"] += 1
    return out
