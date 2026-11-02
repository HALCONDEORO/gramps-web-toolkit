"""Read-only review hold for same-name, same-day birth/baptism merges.

This deliberately narrow check neither establishes identity nor authorizes a merge.
Only exact Gregorian civil days and explicit biological relationships supply evidence.
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
    """Group all corresponding primary events by type and exact day."""
    groups = {}
    for ref in person.get("event_ref_list") or []:
        if ref.get("role", "Primary") != "Primary" or not ref.get("ref"):
            continue
        event = client.get("Event", ref["ref"]) or {}
        kind = str(event.get("type")).casefold()
        if kind not in ("birth", "baptism"):
            continue
        day = _civil_day(event.get("date"))
        if day is not None:
            groups.setdefault((kind, day), []).append(event)
    return groups


def _parents(client, person, name_key):
    names = {"father": set(), "mother": set()}
    for handle in person.get("parent_family_list") or []:
        family = client.get("Family", handle) or {}
        for ref in family.get("child_ref_list") or []:
            if ref.get("ref") != person["handle"]:
                continue
            for role, relation in (("father", "frel"), ("mother", "mrel")):
                parent = family.get(role + "_handle")
                if ref.get(relation) != "Birth" or not parent:
                    continue
                name = _name(client.get("Person", parent), name_key)
                if any(name):
                    names[role].add(name)
    return names


def _pages(client, events):
    pages = {}
    for event in events:
        for handle in event.get("citation_list") or []:
            citation = client.get("Citation", handle) or {}
            source, page = citation.get("source_handle"), citation.get("page")
            if source and isinstance(page, str) and page.strip():
                pages.setdefault(source, set()).add(page.strip())
    return pages


def same_day_review_required(client, winner, loser, name_key):
    """True only for a namesake/day match with positive contradictory evidence.

    Parent names are unioned over every biological family, separately per role.
    Act pages are unioned over corresponding events: a shared page defeats page
    disjointness within that source, even if either side also has other pages.
    Missing information supplies no contradiction. No caller waiver is consulted.
    """
    name = _name(winner, name_key)
    if not any(name) or name != _name(loser, name_key):
        return False
    left, right = _events(client, winner), _events(client, loser)
    common = left.keys() & right.keys()
    if not common:
        return False
    wp, lp = _parents(client, winner, name_key), _parents(client, loser, name_key)
    if any(wp[role] and lp[role] and wp[role].isdisjoint(lp[role]) for role in wp):
        return True
    for key in common:
        wc, lc = _pages(client, left[key]), _pages(client, right[key])
        if any(wc[source].isdisjoint(lc[source]) for source in wc.keys() & lc.keys()):
            return True
    return False
