"""Offline pagination manifest auditor (stdlib only, no network).

Input: a JSON manifest describing pages that were ALREADY fetched elsewhere.
This tool only reads the manifest file; it never fetches anything.

Manifest shape::

    {"expected_page_size": 3, "declared_total": 5,
     "pages": [{"page_id": "p1", "index": 1, "source_url": "...",
                "declared_total": 5, "ids": ["a", "b", "c"],
                "expected_page_size": 3, "terminal": false,
                "wall": false, "truncated": false, "next_page_id": "p2"}]}

Verdicts: ``complete`` or ``STOP``. Incomplete data is never reported as
"none found": an empty result is only ``complete`` when a known terminal page
and a declared total of 0 agree. Repeated ids always STOP.
"""
import json
import sys
import unicodedata

EXIT_COMPLETE, EXIT_BAD_INPUT, EXIT_STOP = 0, 1, 2


def _stop(code, detail, page=None):
    page = page if isinstance(page, str) and len(page) <= 200 else None
    return {"code": code, "detail": detail, "page": page}


def _pid(page):
    return page.get("page_id")


def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _is_name(v):
    """Non-empty, trimmed string with no control/format/odd-space chars.

    Whitespace-only, padded, zero-width or other format-only values are not
    usable ids (they could never be verified against a source).
    """
    if not isinstance(v, str) or v == "":
        return False
    n = unicodedata.normalize("NFKC", v)
    if n == "" or n != n.strip() or v != v.strip():
        return False
    return not any(unicodedata.category(c)[0] in "CZ" and c != " " for c in v)


def _short(v):
    """Bounded, non-recursive description of untrusted values for output."""
    if isinstance(v, str):
        return repr(v[:40]) + ("..." if len(v) > 40 else "")
    if _is_int(v) and abs(v) < 10 ** 18:
        return repr(v)
    return f"<{type(v).__name__}>"


def _flag(v):
    return v if isinstance(v, bool) else None


def _flag_findings(page):
    """wall/truncated/terminal must be real bools; True wall/truncated stops."""
    out, pid = [], _pid(page)
    for key, code, why in (("wall", "WALL", "page content untrusted"),
                           ("truncated", "TRUNCATED", "truncation flag set")):
        val = page.get(key)
        if val is True:
            out.append(_stop(code, f"{key} flag set; {why}", pid))
        elif not isinstance(val, bool):
            out.append(_stop("BAD_FLAG", f"{key} missing or not a bool", pid))
    if not isinstance(page.get("terminal"), bool):
        out.append(_stop("UNKNOWN_TERMINAL", "terminal flag not a bool", pid))
    return out


def _ids_findings(ids, pid):
    out = []
    if not all(_is_name(i) for i in ids):
        out.append(_stop("BAD_IDS", "ids must be non-empty, trimmed, "
                         "printable strings", pid))
    elif len(set(ids)) != len(ids):
        out.append(_stop("REPEATED_ID", "id repeated within page", pid))
    return out


def _size_findings(page, ids, default_size):
    pid = _pid(page)
    out = []
    if "expected_page_size" in page:
        ps = page["expected_page_size"]
        if not _is_int(ps) or ps <= 0:
            out.append(_stop("NO_PAGE_SIZE", "page size invalid", pid))
        elif ps != default_size:  # never let a page override mask a check
            out.append(_stop("SIZE_CONFLICT", f"page size {_short(ps)} differs "
                             f"from manifest size {_short(default_size)}", pid))
    size = default_size  # only the valid manifest default is authoritative
    if not _is_int(size) or size <= 0:
        return out + [_stop("NO_PAGE_SIZE", "manifest expected_page_size "
                            "missing or invalid", pid)]
    if len(ids) > size:
        out.append(_stop("OVERSIZE", f"{len(ids)} ids > size {size}", pid))
    elif len(ids) < size and page.get("terminal") is not True:
        out.append(_stop("SHORT_PAGE", f"{len(ids)} ids < size {size} "
                         "on non-terminal page", pid))
    return out


def _page_findings(page, default_size):
    """Per-page evidence and stop reasons."""
    pid, ids = _pid(page), page.get("ids")
    out = []
    if not _is_name(pid):
        out.append(_stop("BAD_PAGE_ID", "page_id must be a non-empty string"))
        pid = None
    if not _is_name(page.get("source_url")):
        out.append(_stop("MISSING_SOURCE_URL",
                         "source_url must be a non-empty string", pid))
    if not isinstance(ids, list):
        return out + [_stop("BAD_IDS", "ids is not a list", pid)]
    out += _flag_findings(page) + _ids_findings(ids, pid)
    return out + _size_findings(page, ids, default_size)


def _index_findings(pages):
    """Bad/duplicate indices and gaps. Bools are not ints."""
    out, seen_ids, seen_idx = [], set(), set()
    for p in pages:
        pid, idx = _pid(p), p.get("index")
        if not _is_int(idx):
            out.append(_stop("BAD_INDEX", "page index not an integer", pid))
            continue
        if idx in seen_idx:
            out.append(_stop("DUPLICATE_PAGE", f"index {idx} repeated", pid))
        seen_idx.add(idx)
    for p in pages:
        pid = _pid(p)
        if _is_name(pid) and pid in seen_ids:
            out.append(_stop("DUPLICATE_PAGE", "page_id repeated", pid))
        if _is_name(pid):
            seen_ids.add(pid)
    return out + _gap_findings(seen_idx)


def _gap_findings(seen_idx):
    out = []
    if seen_idx:
        lo, hi = min(seen_idx), max(seen_idx)
        if lo not in (0, 1):
            out.append(_stop("MISSING_PAGE", f"first index {lo}, not 0/1"))
        cap, missing = len(seen_idx), 0  # work bounded by page count
        for i in range(lo, hi + 1):
            if i not in seen_idx:
                missing += 1
                out.append(_stop("MISSING_PAGE", f"no page at index {i}"))
                if missing >= cap:
                    break
        total = hi - lo + 1 - len(seen_idx)
        if total > missing:
            out.append(_stop("MISSING_PAGE", f"{total} page indices missing "
                             f"between {lo} and {hi} (listing truncated)"))
    return out


def _start_page(pages):
    ok = [p for p in pages if _is_int(p.get("index"))]
    return min(ok, key=lambda p: p["index"]) if ok else None


def _cycle_findings(pages):
    """Require an explicit chain from the first index to the terminal page.

    Every non-terminal page must name its successor (MISSING_LINK), links
    must exist (MISSING_PAGE), be acyclic (CYCLE), go to index+1
    (OUT_OF_ORDER) and reach every page. No order is ever inferred.
    """
    by_id = {_pid(p): p for p in pages if _is_name(_pid(p))}
    out, visited, cur = [], set(), _start_page(pages)
    while cur is not None:
        pid = _pid(cur)
        pid = pid if _is_name(pid) else None
        if id(cur) in visited:  # page_id may be unhashable (list/dict)
            return out + [_stop("CYCLE", "next_page_id revisits page", pid)]
        visited.add(id(cur))
        if cur.get("terminal") is True:
            back = cur.get("next_page_id")
            if (_is_name(back) and back in by_id
                    and id(by_id[back]) in visited):
                out.append(_stop("CYCLE", "terminal page links back to a "
                                 "visited page", pid))
            break
        found, nxt_page = _resolve_next(cur, by_id, pid)
        out += found
        if nxt_page is None:  # broken link: chain is not proven, stop here
            return out
        cur = nxt_page
    if len(visited) != len(pages):
        out.append(_stop("MISSING_LINK", "pages unreachable via next_page_id"))
    return out


def _resolve_next(cur, by_id, pid):
    """Return (findings, next page); next page is None when the link is broken."""
    nxt = cur.get("next_page_id")
    if nxt is None:
        return [_stop("MISSING_LINK", "non-terminal page has no "
                      "next_page_id; chain is not proven", pid)], None
    if not _is_name(nxt) or nxt not in by_id:
        return [_stop("MISSING_PAGE", f"next_page_id {_short(nxt)} "
                      "not in manifest", pid)], None
    nxt_page = by_id[nxt]
    if (_is_int(nxt_page.get("index")) and _is_int(cur.get("index"))
            and nxt_page["index"] != cur["index"] + 1):
        return [_stop("OUT_OF_ORDER", "next_page_id is not the next "
                      "index", pid)], nxt_page
    return [], nxt_page


def _terminal_findings(pages):
    terms = [p for p in pages if p.get("terminal") is True]
    if not terms:
        return [_stop("UNKNOWN_TERMINAL", "no page marked terminal")]
    idxs = [p["index"] for p in pages if _is_int(p.get("index"))]
    last = max(idxs) if idxs else None
    out = []
    for t in terms:
        if t.get("index") != last or not _is_int(t.get("index")):
            out.append(_stop("EARLY_TERMINAL",
                             "terminal page is not the last index", _pid(t)))
        if t.get("next_page_id") is not None:
            out.append(_stop("EARLY_TERMINAL",
                             "terminal page has next_page_id", _pid(t)))
    return out


def _safe_pid(page):
    return _pid(page) if _is_name(_pid(page)) else None


def _dedupe(pages):
    """Unique ids in first-seen order plus an explicit repeat report."""
    first, repeats = {}, {}
    for p in pages:
        ids = p.get("ids")
        for i in ids if isinstance(ids, list) else []:
            if not _is_name(i):
                continue
            if i in first:
                repeats.setdefault(i, [first[i]]).append(_safe_pid(p))
            else:
                first[i] = _safe_pid(p)
    return list(first), repeats


def _norm_key(i):
    return unicodedata.normalize("NFKC", i).strip().casefold()


def _ambiguous_findings(unique):
    """Distinct ids that normalize alike are ambiguous: STOP, never merge."""
    groups = {}
    for i in unique:
        groups.setdefault(_norm_key(i), []).append(i)
    return [_stop("AMBIGUOUS_ID", "near-identical ids "
                  f"{[_short(x) for x in g[:5]]}; not merged")
            for g in groups.values() if len(g) > 1]


def _repeat_findings(repeats):
    return [_stop("REPEATED_ID", f"id {_short(i)} seen on "
                  f"{len(pgs)} pages") for i, pgs in repeats.items()]


def _totals(manifest, pages):
    vals = [manifest.get("declared_total")]
    vals += [p.get("declared_total") for p in pages]
    return [v for v in vals if v is not None]


def _total_findings(declared, unique_count, returned_count):
    if any(not _is_int(v) or v < 0 for v in declared):
        return [_stop("BAD_TOTAL", "invalid declared total(s): "
                      f"{[_short(v) for v in declared[:5]]}")]
    if not declared:
        return [_stop("NO_DECLARED_TOTAL", "declared total unknown")]
    if len(set(declared)) > 1:
        return [_stop("TOTAL_CONFLICT",
                      f"declared totals disagree: {sorted(set(declared))}")]
    if declared[0] != unique_count or declared[0] != returned_count:
        return [_stop("TOTAL_MISMATCH", f"declared {declared[0]} but "
                      f"{unique_count} unique / {returned_count} returned")]
    return []


NOTE = "offline audit of manifest only; no live validation"


def _bad(code, detail):
    """STOP report with the same schema as a normal report."""
    return {"verdict": "STOP", "stops": [_stop(code, detail)],
            "coverage": None, "declared_total": None, "unique_ids": 0,
            "returned_ids": 0, "verified_ids": 0, "repeats": {}, "pages": [],
            "note": NOTE}


def _verified(pages):
    """Ids from pages explicitly not walled and not truncated."""
    seen = set()
    for p in pages:
        if (p.get("wall") is False and p.get("truncated") is False
                and isinstance(p.get("ids"), list)):
            seen.update(i for i in p["ids"] if _is_name(i))
    return seen


def _returned(pages):
    return sum(len(p["ids"]) for p in pages if isinstance(p.get("ids"), list))


def audit(manifest):
    """Audit a manifest dict; return a report dict (never raises on content)."""
    pages = manifest.get("pages") if isinstance(manifest, dict) else None
    if not isinstance(pages, list) or not all(isinstance(p, dict)
                                              for p in pages):
        return _bad("BAD_MANIFEST", "pages missing or not a list of objects")
    if not pages:
        return _bad("NO_PAGES", "empty manifest is not evidence of 'none "
                    "found'")
    default = manifest.get("expected_page_size")
    per_page = [_page_findings(p, default) for p in pages]
    stops = [s for v in per_page for s in v]
    stops += _index_findings(pages) + _terminal_findings(pages)
    stops += _cycle_findings(pages)
    unique, repeats = _dedupe(pages)
    stops += _repeat_findings(repeats) + _ambiguous_findings(unique)
    declared = _totals(manifest, pages)
    stops += _total_findings(declared, len(unique), _returned(pages))
    return _report(pages, per_page, stops, unique, repeats, declared)


def _report(pages, per_page, stops, unique, repeats, declared):
    valid = all(_is_int(v) for v in declared)  # lists/dicts are unhashable
    total = declared[0] if valid and len(set(declared)) == 1 else None
    verified = _verified(pages)  # wall/truncated pages never count
    cov = None
    if _is_int(total) and total > 0:
        cov = round(len(verified) / total, 4)
    ev = [{"page_id": _safe_pid(p),
           "index": p.get("index") if _is_int(p.get("index")) else None,
           "source_url": p.get("source_url")
           if _is_name(p.get("source_url")) else None,
           "ids_returned": len(p["ids"]) if isinstance(p.get("ids"), list)
           else None,
           "terminal": _flag(p.get("terminal")), "wall": _flag(p.get("wall")),
           "truncated": _flag(p.get("truncated")),
           "stops": [s["code"] for s in per_page[n]]}
          for n, p in enumerate(pages)]
    return {"verdict": "STOP" if stops else "complete", "stops": stops,
            "coverage": cov, "declared_total": total,
            "unique_ids": len(unique), "returned_ids": _returned(pages),
            "verified_ids": len(verified),
            "repeats": repeats, "pages": ev, "note": NOTE}


def _no_dup_keys(pairs):
    """Linear-time duplicate key rejection."""
    seen, dups = {}, []
    for k, v in pairs:
        if k in seen and len(dups) < 5:
            dups.append(_short(k))
        seen[k] = v
    if dups:
        raise ValueError(f"duplicate JSON key(s): {dups}")
    return seen


def load_manifest(path):
    """Read a manifest file; duplicate JSON object keys are rejected."""
    with open(path, encoding="utf-8") as fh:
        return json.load(fh, object_pairs_hook=_no_dup_keys)


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("usage: gwt_pagination_audit.py MANIFEST.json", file=sys.stderr)
        return EXIT_BAD_INPUT
    try:
        manifest = load_manifest(args[0])
    except (OSError, ValueError, RecursionError) as exc:
        print(f"cannot read manifest: {exc}", file=sys.stderr)
        return EXIT_BAD_INPUT
    try:
        report = audit(manifest)
        text = json.dumps(report, indent=2, sort_keys=True)
    except (RecursionError, ValueError) as exc:
        print(f"cannot audit manifest: {type(exc).__name__}", file=sys.stderr)
        return EXIT_BAD_INPUT
    print(text)
    return EXIT_STOP if report["verdict"] == "STOP" else EXIT_COMPLETE


if __name__ == "__main__":
    sys.exit(main())
