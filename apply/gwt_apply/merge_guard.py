"""Read-only review hold for same-name, same-day birth/baptism merges.

A match requires review; it does not establish distinct identities.
Only corresponding primary events on exact Gregorian civil days qualify.
"""

from .dates import DateError, date_key, parse_date


def _name(person, name_key):
    name = (person or {}).get("primary_name") or {}
    return name_key(name.get("first_name", ""), " ".join(
        s.get("surname", "") for s in name.get("surname_list") or []))


def _civil_day(value):
    """Validate stored dates with the existing parser, without trusting sortval."""
    if not isinstance(value, dict) or value.get("calendar") != 0:
        return None
    if value.get("modifier", 0) != 0 or value.get("quality", 0) != 0 or value.get("newyear", 0) != 0:
        return None
    parts = value.get("dateval")
    if not isinstance(parts, (list, tuple)) or len(parts) != 4 or parts[3] is not False:
        return None
    if any(type(v) is not int or v <= 0 for v in parts[:3]):
        return None
    day, month, year = parts[:3]
    try:
        parsed = parse_date(f"{year:04d}-{month:02d}-{day:02d}")
    except DateError:
        return None
    return date_key(parsed)


def _events(client, person):
    """Collect primary birth/baptism event types and exact days."""
    groups = set()
    for ref in person.get("event_ref_list") or []:
        if ref.get("role", "Primary") != "Primary" or not ref.get("ref"):
            continue
        event = client.get("Event", ref["ref"]) or {}
        kind = str(event.get("type")).casefold()
        if kind not in ("birth", "baptism"):
            continue
        day = _civil_day(event.get("date"))
        if day is not None:
            groups.add((kind, day))
    return groups


def same_day_review_required(client, winner, loser, name_key):
    """Hold every normalized namesake with a corresponding exact event day.

    Contradictory evidence is not required, and no caller waiver is consulted.
    """
    name = _name(winner, name_key)
    if not any(name) or name != _name(loser, name_key):
        return False
    return bool(_events(client, winner) & _events(client, loser))
