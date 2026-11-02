"""Offline review of two independent OCR/HTR transcriptions of one act image.

Takes two transcriptions plus explicit provenance (engine, reader, image/crop
SHA-256), compares names, date, act type and parents token by token, and writes
a writer-review packet. It never verifies anything automatically, never calls an
engine or service, and never normalises names/dates in a way that would hide a
disagreement (comparison is exact: case, accents, spelling, digits).

Standard library only.  See docs/transcription-review.md.
"""
import argparse
import difflib
import json
import os
import re
import sys
import tempfile
import unicodedata

MAX_INPUT_CHARS = 1000000
MAX_FIELD_CHARS = 2000
MAX_FIELD_TOKENS = 300
MAX_META_CHARS = 500
FIELDS = ("given_names", "surname", "date", "act_type", "father", "mother")
PROVENANCE = ("engine", "reader")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
# Matched on the NFKC form of a token, so fullwidth "？" and "…" are caught, and
# words such as "[unclear]," or "illegible." are caught despite punctuation.
UNCERTAIN_RE = re.compile(r"\?|…|\.\.\.|\b(?:illegible|unclear|unreadable|gap)\b", re.I)
# Reader values that name nobody: never a trusted identity.
NO_IDENTITY = frozenset({"none", "unknown", "n/a", "na", "unspecified", "anonymous",
                         "tbd", "-", "?"})
REFUSAL = ("Automatic verification refused: this tool only compares two machine/reader "
           "outputs. A human writer must check the image and decide.")


class ReviewError(ValueError):
    """Raised for malformed input."""


def is_uncertain(token):
    return bool(UNCERTAIN_RE.search(unicodedata.normalize("NFKC", token)))


def tokens(text):
    """Whitespace tokens, Unicode-canonically composed (NFC) so that a decomposed
    and a precomposed encoding of the same letter are not a false disagreement.
    Case, accents and spelling are otherwise untouched."""
    return unicodedata.normalize("NFC", text or "").split()


def _norm_id(value):
    """Identity key for engine/reader: ignores case, spacing, compatibility forms
    and invisible format characters so spoofed variants compare equal."""
    s = unicodedata.normalize("NFKC", value)
    s = "".join(c for c in s if unicodedata.category(c) != "Cf")
    return " ".join(s.casefold().split())


def _check_text(value, idx, label, limit):
    if isinstance(value, str) and any(0xD800 <= ord(c) <= 0xDFFF for c in value):
        raise ReviewError('text contains an invalid Unicode surrogate')
    if not isinstance(value, str):
        raise ReviewError(f"transcription {idx}: {label} must be a string")
    if len(value) > limit:
        raise ReviewError(f"transcription {idx}: {label} too long (max {limit})")


def _check_field_value(value, idx, name):
    if value is None:
        return
    _check_text(value, idx, f"field {name}", MAX_FIELD_CHARS)
    if len(value.split()) > MAX_FIELD_TOKENS:
        raise ReviewError(f"transcription {idx}: field {name} has too many tokens")


def _check_provenance(t, idx):
    for key in PROVENANCE:
        _check_text(t.get(key), idx, key, MAX_META_CHARS)
        if not _norm_id(t[key]):
            raise ReviewError(f"transcription {idx}: missing {key}")
    if "version" in t:
        _check_text(t["version"], idx, "version", MAX_META_CHARS)
    if not isinstance(t.get("fields"), dict):
        raise ReviewError(f"transcription {idx}: fields must be an object")
    unknown = sorted(set(t["fields"]) - set(FIELDS))
    if unknown:
        raise ReviewError(f"transcription {idx}: unknown fields {unknown}")
    for name, value in t["fields"].items():
        _check_field_value(value, idx, name)


def validate(doc):
    """Validate input; return list of non-fatal warnings."""
    if not isinstance(doc, dict):
        raise ReviewError("input must be a JSON object")
    sha = doc.get("image_sha256")
    if not isinstance(sha, str) or not SHA256_RE.fullmatch(sha):
        raise ReviewError("image_sha256 must be 64 lowercase hex characters")
    crop = doc.get("crop")
    if crop is not None and (not isinstance(crop, str) or len(crop) > MAX_META_CHARS):
        raise ReviewError("crop must be a short string")
    ts = doc.get("transcriptions")
    if not isinstance(ts, list) or len(ts) != 2:
        raise ReviewError("exactly two transcriptions are required")
    for i, t in enumerate(ts):
        if not isinstance(t, dict):
            raise ReviewError(f"transcription {i} must be an object")
        _check_provenance(t, i)
    return _warnings(ts)


def _warnings(ts):
    warns = []
    a, b = ts
    ea, eb = _norm_id(a["engine"]), _norm_id(b["engine"])
    ra, rb = _norm_id(a["reader"]), _norm_id(b["reader"])
    if (ea, ra) == (eb, rb):
        warns.append("both transcriptions share the same engine and reader: not independent")
    elif ra == rb and ra not in NO_IDENTITY:
        warns.append("both transcriptions share the same reader: not independent")
    for label, r in (("A", ra), ("B", rb)):
        if r in NO_IDENTITY and r != "none":
            warns.append(f"transcription {label} reader is not identified ({r!r}): "
                         "independence cannot be established")
    if all(tokens(a["fields"].get(f)) == tokens(b["fields"].get(f)) for f in FIELDS):
        warns.append("transcriptions are identical: agreement may reflect shared "
                     "error or copying, not confirmation")
    return warns


def _span(toks, i1, i2):
    return {"tokens": toks[i1:i2], "range": [i1, i2]}


def compare_field(text_a, text_b):
    """Token-level comparison of one field. Returns a dict of findings."""
    ta, tb = tokens(text_a), tokens(text_b)
    diffs = []
    for op, a1, a2, b1, b2 in difflib.SequenceMatcher(None, ta, tb, autojunk=False).get_opcodes():
        if op == "equal":
            continue
        kind = {"replace": "mismatch", "delete": "missing_in_b", "insert": "missing_in_a"}[op]
        diffs.append({"kind": kind, "a": _span(ta, a1, a2), "b": _span(tb, b1, b2)})
    unc = {"a": [t for t in ta if is_uncertain(t)], "b": [t for t in tb if is_uncertain(t)]}
    empty = {"a": not ta, "b": not tb}
    nfc_changed = {"a": bool(text_a) and unicodedata.normalize("NFC", text_a) != text_a,
                   "b": bool(text_b) and unicodedata.normalize("NFC", text_b) != text_b}
    return {"a": text_a or "", "b": text_b or "", "differences": diffs,
            "nfc_recomposed": nfc_changed,
            "uncertain_tokens": unc, "empty": empty,
            "status": field_status(diffs, unc, empty)}


def field_status(diffs, unc, empty):
    if empty["a"] or empty["b"]:
        return "missing"
    if diffs:
        return "disagree"
    if unc["a"] or unc["b"]:
        return "uncertain"  # identical markers still mean nobody read the word
    return "match"


def build_packet(doc):
    """Build the writer-review packet. Always needs human review."""
    warnings = validate(doc)
    a, b = doc["transcriptions"]
    fields = {f: compare_field(a["fields"].get(f), b["fields"].get(f)) for f in FIELDS}
    counts = {}
    for r in fields.values():
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    return {
        "schema": "gwt.transcription-review/1",
        "status": "needs-writer-review",
        "auto_verified": False,
        "refusal": REFUSAL,
        "image_sha256": doc["image_sha256"],
        "crop": doc.get("crop"),
        "sources": [{k: t[k] for k in PROVENANCE + ("version",) if k in t} for t in (a, b)],
        "fields": fields,
        "status_counts": counts,
        "warnings": warnings,
        "notes": ["Agreement between two outputs is not evidence of correctness.",
                  "Normalisation disclosure: fields are shown exactly as supplied; "
                  "comparison only splits on whitespace and applies Unicode NFC "
                  "composition (see nfc_recomposed). No case, accent, spelling, name "
                  "or date normalisation.",
                  "No accuracy figure is claimed for any engine or reader."],
    }


def auto_verify(_packet):
    """Deliberately unsupported."""
    raise PermissionError(REFUSAL)


def render_text(packet):
    lines = [f"Transcription review packet ({packet['status']})",
             f"auto_verified: {packet['auto_verified']}",
             "image sha256: " + packet["image_sha256"],
             f"crop: {packet.get('crop')!r}", packet["refusal"], "", "Sources:"]
    for label, s in zip("AB", packet["sources"]):
        lines.append(f"  {label}: " + ", ".join(f"{k}={v!r}" for k, v in s.items()))
    lines.append("Status counts: " + ", ".join(f"{k}={v}" for k, v in
                                               sorted(packet["status_counts"].items())))
    lines.append("Warnings:" + ("" if packet["warnings"] else " none"))
    lines += [f"  - {w}" for w in packet["warnings"]]
    lines.append("")
    for name, r in packet["fields"].items():
        lines.append(f"[{r['status'].upper()}] {name}")
        lines.append(f"  A: {r['a']!r}")
        lines.append(f"  B: {r['b']!r}")
        for d in r["differences"]:
            lines.append(f"  {d['kind']}: A{d['a']['tokens']} vs B{d['b']['tokens']}")
        for side in "ab":
            if r["uncertain_tokens"][side]:
                lines.append(f"  uncertain in {side.upper()}: {r['uncertain_tokens'][side]}")
        if any(r["nfc_recomposed"].values()):
            lines.append(f"  NFC recomposed before comparing: {r['nfc_recomposed']}")
    lines += ["", "Notes:"] + [f"  - {n}" for n in packet["notes"]]
    lines += ["", "Writer decision: ________ (not recorded by this tool)"]
    return "\n".join(lines)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("input", help="JSON file with two transcriptions")
    p.add_argument("--out", help="write packet JSON here (default: stdout)")
    p.add_argument("--text", action="store_true", help="print a plain-text packet")
    p.add_argument("--overwrite", action="store_true",
                   help="allow --out to replace an existing file (default: refuse)")
    args = p.parse_args(argv)
    try:
        if args.out and _same_file(args.input, args.out):
            raise ReviewError("--out must not be the input file")
        if args.out and not args.overwrite and os.path.lexists(args.out):
            raise ReviewError("--out already exists; pass --overwrite to replace it")
        packet = build_packet(_load(args.input))
        out = render_text(packet) if args.text else json.dumps(packet, indent=2, ensure_ascii=False)
        data = (out + "\n").encode("utf-8")  # encode fully before touching any file
        if args.out:
            _write_out(args.out, data, args.overwrite)
        else:
            out.encode(sys.stdout.encoding or "utf-8")  # fail before printing anything
    except (OSError, ValueError, LookupError, RecursionError) as exc:  # incl. encoding errors
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if not args.out:
        print(out)
    return 0


def _same_file(a, b):
    if os.path.realpath(a) == os.path.realpath(b):
        return True
    return os.path.exists(a) and os.path.exists(b) and os.path.samefile(a, b)


def _write_out(path, data, overwrite):
    """Stage bytes in a temp file beside the target, then atomically publish.
    Without overwrite, os.link fails if the target exists, so nothing is clobbered."""
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(os.path.abspath(path)),
                               prefix=".review-", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        if overwrite:
            os.replace(tmp, path)
        else:
            os.link(tmp, path)
    finally:
        if os.path.lexists(tmp):
            os.unlink(tmp)


def _no_dup_pairs(pairs):
    keys = [k for k, _ in pairs]
    dup = sorted({k for k in keys if keys.count(k) > 1})
    if dup:
        raise ReviewError(f"duplicate JSON keys: {dup}")
    return dict(pairs)


def _load(path):
    with open(path, encoding="utf-8") as fh:
        text = fh.read(MAX_INPUT_CHARS + 1)
    if len(text) > MAX_INPUT_CHARS:
        raise ReviewError(f"input file too large (max {MAX_INPUT_CHARS} characters)")
    return json.loads(text, object_pairs_hook=_no_dup_pairs)


if __name__ == "__main__":
    sys.exit(main())
