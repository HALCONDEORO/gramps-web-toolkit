"""One person's events in date order, as the server's own timeline lists them: a report that changes nothing.

Each row is the event's Gramps ID, type, role and date, and the person's Gramps ID. The description, place and
citations stay on the server."""

from __future__ import annotations

from .client import Client


def timeline(client: Client, handles: list[str], limit: int = 200) -> list[dict]:
    """The events of `handles` (people) in the order the server gives them, newest last."""
    params = {"handles": ",".join(handles), "pagesize": limit}
    _, rows = client.get("/timelines/people/", params)
    out = []
    for r in rows[:limit]:
        person = r.get("person") if isinstance(r.get("person"), dict) else {}
        out.append({"person": person.get("gramps_id"), "event": r.get("gramps_id"), "type": r.get("type"),
                    "role": r.get("role"), "date": r.get("date")})
    return out
