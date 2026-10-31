"""gwt-act: turn the fields of one civil or parish act into the change list gwt-apply takes.

An act that is read by hand costs five ops written by hand (source, citation, filiation citation, media,
transcription), each with keys, attach lists and the same ruling. This writes them from one small JSON, checks the
result with the same validator gwt-apply uses, and prints the list. It reads nothing from the tree and writes nothing.

Fields (JSON object): ruling, slug, title, author, pubinfo, page, event (Gramps ID the act proves), confidence
(0-4, default 4), file and path (the scan; optional), desc, mime, transcript, margin_reading, and the optional
declared: [{"page", "confidence", "attach": [...]}] for what the act only declares (a birth, parents, a spouse).
"""
from __future__ import annotations

import argparse
import json
import re
import sys

from .ops import validate

REQUIRED = ("ruling", "slug", "title", "page", "event")


def build(f: dict) -> list[dict]:
    missing = [k for k in REQUIRED if not f.get(k)]
    if missing:
        raise ValueError("missing field(s): " + ", ".join(missing))
    if bool(f.get("transcript")) != bool(f.get("margin_reading")):
        raise ValueError("transcript and margin_reading go together (what the margins say is required)")
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", f["slug"]):
        raise ValueError("slug is lowercase letters, digits and dashes")
    s, c, r = f"s-{f['slug']}", f"c-{f['slug']}", f["ruling"]
    ops: list[dict] = []
    src = {"op": "add_source", "key": s, "ruling": r, "title": f["title"]}
    for k in ("author", "pubinfo"):
        if f.get(k):
            src[k] = f[k]
    ops.append(src)
    ops.append({"op": "add_citation", "key": c, "ruling": r, "source": f"key:{s}", "page": f["page"],
                "confidence": int(f.get("confidence", 4)), "attach": [{"event": f["event"]}]})
    for i, d in enumerate(f.get("declared") or [], 1):
        ops.append({"op": "add_citation", "key": f"{c}-decl{i}", "ruling": r, "source": f"key:{s}", "page": d["page"],
                    "confidence": int(d.get("confidence", 3)), "attach": d["attach"]})
    if f.get("file"):
        m = {"op": "add_media", "key": f"m-{f['slug']}", "ruling": r, "file": f["file"],
             "path": f.get("path") or f["file"].rsplit("/", 1)[-1], "attach": [{"citation": f"key:{c}"}]}
        for k in ("desc", "mime"):
            if f.get(k):
                m[k] = f[k]
        ops.append(m)
    if f.get("transcript"):
        ops.append({"op": "add_transcription", "key": f"t-{f['slug']}", "ruling": r, "text": f["transcript"],
                    "margin_reading": f["margin_reading"], "attach": [{"citation": f"key:{c}"}]})
    problems = validate(ops)
    if problems:
        raise ValueError("; ".join(problems))
    return ops


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="gwt-act", description=__doc__.split("\n\n")[0])
    ap.add_argument("fields", help="JSON file with the fields of the act, or - for stdin")
    a = ap.parse_args(argv)
    try:
        data = json.load(sys.stdin if a.fields == "-" else open(a.fields, encoding="utf-8"))
        ops = build(data)
    except (OSError, ValueError) as exc:
        print(f"gwt-act: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(ops, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
