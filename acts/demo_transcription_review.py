"""Offline demonstration of gwt_transcription_review on a synthetic fixture.

Builds the two-reading disagreement fixture from
contributions/reviews/transcription-fixture-spec.md in memory, runs the real
API (and the real CLI entry point against a throwaway temp directory), and
asserts the ACTUAL status counts and reader warnings. All data is invented.
Nothing persists: temp files are removed on exit, and only a short summary is
printed. This demonstrates tool behaviour only; it proves nothing about which
reading is correct, and it reports no accuracy figure for any engine or reader.

Usage: python3 acts/demo_transcription_review.py
"""
import contextlib
from pathlib import Path
import copy
import io
import json
import os
import sys
import tempfile

import gwt_transcription_review as tr

def check(condition, detail):
    """Checks remain active even when Python runs with optimization."""
    if not condition:
        raise AssertionError(detail)


SAME_READER = "both transcriptions share the same reader: not independent"
SAME_ENGINE_READER = "both transcriptions share the same engine and reader: not independent"
IDENTICAL_PREFIX = "transcriptions are identical"

BASE = {
    "image_sha256": "a" * 64,  # invented, not the hash of any image
    "crop": "synthetic-crop-top",
    "transcriptions": [
        {"engine": "synthetic-A", "reader": "synthetic-reader-1", "version": "v0",
         "fields": {"given_names": "Rosa Maria", "surname": "Testin",
                    "date": "9 mars 1771", "act_type": "bapteme",
                    "father": "Jean Testin", "mother": "Anne [illegible]"}},
        {"engine": "synthetic-B", "reader": " SYNTHETIC-Reader-1 ", "version": "v0",
         "fields": {"given_names": "Rosa Marie", "surname": "Testan",
                    "date": "09/03/1771", "act_type": "bapteme",
                    "father": "Jean [unclear]", "mother": "Anne [illegible]"}},
    ],
}


def variant(edit):
    doc = copy.deepcopy(BASE)
    edit(doc["transcriptions"])
    return doc


def statuses(packet):
    return {k: v["status"] for k, v in packet["fields"].items()}


def check_base():
    p = tr.build_packet(BASE)
    check((p["schema"] == "gwt.transcription-review/1"), ('demonstration check failed at line 56'))
    check((p["status"] == "needs-writer-review"), ('demonstration check failed at line 57'))
    check((p["auto_verified"] is False and p["refusal"]), ('demonstration check failed at line 58'))
    check((p["status_counts"] == {"disagree": 4, "match": 1, "uncertain": 1}), (p["status_counts"]))
    check((statuses(p) == {"given_names": "disagree", "surname": "disagree",
                           "date": "disagree", "act_type": "match",
                           "father": "disagree", "mother": "uncertain"}), (statuses(p)))
    check((p["warnings"] == [SAME_READER]), (p["warnings"]))
    check((p["sources"][1]["reader"] == " SYNTHETIC-Reader-1 "), ('demonstration check failed at line 64'))  # echoed raw
    date = p["fields"]["date"]["differences"]
    check((len(date) == 1 and date[0]["a"]["range"] == [0, 3] and date[0]["b"]["range"] == [0, 1]), ('demonstration check failed at line 66'))
    check((p["fields"]["father"]["uncertain_tokens"]["b"] == ["[unclear]"]), ('demonstration check failed at line 67'))
    check((p["fields"]["mother"]["differences"] == []), ('demonstration check failed at line 68'))
    check_no_proof(p)
    return p


def check_no_proof(p):
    """Assert refusal and accuracy disclaimers."""
    check((not {"verified", "accuracy", "correct", "winner"} & set(p)), ('demonstration check failed at line 75'))
    check((any("not evidence of correctness" in n for n in p["notes"])), ('demonstration check failed at line 76'))
    check((any("No accuracy figure" in n for n in p["notes"])), ('demonstration check failed at line 77'))
    try:
        tr.auto_verify(p)
    except PermissionError:
        pass
    else:
        raise AssertionError("auto_verify must refuse")


def check_variants():
    def v1(ts):
        ts[1]["engine"], ts[1]["reader"] = "synthetic-A", "synthetic-reader-1"

    def v2(ts):
        ts[0]["reader"] = ts[1]["reader"] = "unknown"

    def v3(ts):
        ts[1]["reader"] = "synthetic-reader-1\u200b"

    def v4(ts):
        ts[1]["fields"] = dict(ts[0]["fields"])

    def v5(ts):
        ts[0]["fields"]["mother"] = "Anne Éxemple"
        ts[1]["fields"]["mother"] = "Anne Éxemple"

    check((tr.build_packet(variant(v1))["warnings"] == [SAME_ENGINE_READER]), ('demonstration check failed at line 103'))
    w2 = tr.build_packet(variant(v2))["warnings"]
    check((len(w2) == 2 and all("not identified" in w for w in w2) and SAME_READER not in w2), (w2))
    check((tr.build_packet(variant(v3))["warnings"] == [SAME_READER]), ('demonstration check failed at line 106'))
    p4 = tr.build_packet(variant(v4))
    check((any(w.startswith(IDENTICAL_PREFIX) for w in p4["warnings"])), (p4["warnings"]))
    s4 = statuses(p4)
    check((s4["mother"] == "uncertain"), ('demonstration check failed at line 110'))
    check((all(v == "match" for k, v in s4.items() if k != "mother")), (s4))
    m5 = tr.build_packet(variant(v5))["fields"]["mother"]
    check((m5["status"] == "uncertain" or m5["status"] == "match"), ('demonstration check failed at line 113'))
    check((m5["differences"] == [] and m5["nfc_recomposed"] == {"a": False, "b": True}), (m5))
    return 5


def _rc_err(argv):
    """Run the CLI, capturing stderr so the expected rejection is not printed.
    Returns (exit code, stderr with the 'error: ' prefix and any suffix trimmed to the match key)."""
    buf = io.StringIO()
    with contextlib.redirect_stderr(buf):
        rc = tr.main(argv)
    msg = buf.getvalue().strip()
    check((msg.startswith("error: ")), (msg))
    msg = msg[len("error: "):]
    for key in ("--out already exists", "--out must not be the input file"):
        if msg.startswith(key):
            msg = key
    return rc, msg



def check_cli(packet):
    with tempfile.TemporaryDirectory(prefix="demo-tr-") as d:
        src = os.path.join(d, "in.json")
        with open(src, "w", encoding="utf-8") as fh:
            json.dump(BASE, fh)
        out_json, out_txt = os.path.join(d, "o.json"), os.path.join(d, "o.txt")
        check((tr.main([src, "--out", out_json]) == 0), ('demonstration check failed at line 140'))
        check((tr.main([src, "--text", "--out", out_txt]) == 0), ('demonstration check failed at line 141'))
        with open(out_json, encoding="utf-8") as fh:
            check((json.load(fh) == packet), ('demonstration check failed at line 143'))
        with open(out_txt, encoding="utf-8") as fh:
            text = fh.read()
        check((SAME_READER in text and "uncertain in B" in text), ('demonstration check failed at line 146'))
        check(("Agreement between two outputs is not evidence of correctness." in text), ('demonstration check failed at line 147'))
        before = Path(out_json).read_bytes()
        check((_rc_err([src, "--out", out_json]) == (2, "--out already exists")), ("no-clobber"))
        check((Path(out_json).read_bytes() == before), ('demonstration check failed at line 150'))
        # Symlink aliases of the input and a dangling symlink must also be refused.
        alias, dangling = os.path.join(d, "alias.json"), os.path.join(d, "dangling.json")
        src_bytes = Path(src).read_bytes()
        try:
            os.symlink(src, alias)
            os.symlink(os.path.join(d, "missing-target"), dangling)
        except (OSError, NotImplementedError):
            return False  # symlinks unsupported here; variant skipped
        check((_rc_err([src, "--out", alias, "--overwrite"]) == (2, "--out must not be the input file")), ('demonstration check failed at line 159'))
        check((_rc_err([src, "--out", dangling]) == (2, "--out already exists")), ('demonstration check failed at line 160'))
        check((Path(src).read_bytes() == src_bytes and not os.path.exists(os.path.join(d, "missing-target"))), ('demonstration check failed at line 161'))
        return True


def main():
    packet = check_base()
    n = check_variants()
    symlinks = check_cli(packet)
    print("transcription review demo (synthetic data, offline, nothing persisted)")
    print(f"  packet status: {packet['status']}  auto_verified={packet['auto_verified']}")
    print(f"  status counts: {packet['status_counts']}")
    print(f"  warnings: {packet['warnings']}")
    print(f"  variants checked: {n}; CLI json/text/no-clobber checked in a temp dir")
    print("  rejections (no-clobber, symlink alias): stderr captured and asserted"
          + ("" if symlinks else "; symlink variant skipped (unsupported)"))
    print("  note: shows tool behaviour only; no claim which reading is correct")
    return 0


if __name__ == "__main__":
    sys.exit(main())
