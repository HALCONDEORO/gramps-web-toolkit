"""Offline, bounded surname search suggestions; never identity or merge evidence."""
from __future__ import annotations

import json
import os
import stat
import re
import sys
import unicodedata

from .names import fold

MAX_LENGTH = 128
MAX_FILE_BYTES = 1024 * 1024
CONSONANTS = frozenset("bcdfghjklmnpqrstvwxyz")
PAIRS = (("s", "z"), ("z", "s"), ("c", "s"), ("s", "c"),
         ("ph", "f"), ("f", "ph"), ("y", "i"), ("i", "y"))


def validate(value: str) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > MAX_LENGTH:
        raise ValueError("surname/spelling must contain 1-128 characters and not be blank")
    if any(unicodedata.category(c).startswith("C") or c in "\u2028\u2029" for c in value):
        raise ValueError("surname/spelling must be one line without control characters")


def _unique_keys(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise ValueError("duplicate JSON key in variants file")
        out[key] = value
    return out


def load(path: str) -> dict[str, list[str]]:
    # CLI explicitly selects a local file, not a server-controlled base directory.
    # Normalize aliases before opening and refuse devices/FIFOs/directories.
    path = os.path.realpath(os.path.abspath(os.path.expanduser(path)))
    if not stat.S_ISREG(os.stat(path).st_mode):
        raise ValueError("variants file must be a regular local file")
    with open(path, "rb") as stream:
        raw = stream.read(MAX_FILE_BYTES + 1)
    if len(raw) > MAX_FILE_BYTES:
        raise ValueError("variants file exceeds 1 MiB")
    data = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_keys)
    if not isinstance(data, dict):
        raise ValueError("variants file must be a JSON object of surname: [spellings]")
    result = {}
    for surname, spellings in data.items():
        validate(surname)
        if not isinstance(spellings, list):
            raise ValueError("each surname in variants file needs a list of strings")
        seen = set()
        if not spellings:
            print("gwt-fetch: warning: empty variants list", file=sys.stderr)
        for spelling in spellings:
            if isinstance(spelling, str) and not spelling.strip():
                print("gwt-fetch: warning: empty variant ignored", file=sys.stderr)
                continue
            validate(spelling)
            if spelling in seen:
                print("gwt-fetch: warning: duplicate variant ignored", file=sys.stderr)
            seen.add(spelling)
        result[surname] = sorted(seen)
    return result


def rule_variants(original: str) -> set[str]:
    """One edit at one original position; never feed output back into the rules.

    At most six edits per character, hence at most 1 + 6*128 spellings including
    the original. Accented/non-ASCII letters and punctuation are left alone.
    """
    out = set()
    for i, char in enumerate(original):
        for source, target in PAIRS:
            token = original[i:i + len(source)]
            if token.lower() == source:
                if original.isupper() or (len(token) > 1 and token.isupper()):
                    replacement = target.upper()
                else:
                    replacement = target.capitalize() if token[0].isupper() else target
                out.add(original[:i] + replacement + original[i + len(source):])
        low = char.lower()
        if low in CONSONANTS:
            if original[i:i + 2].lower() == low * 2:
                out.add(original[:i] + original[i + 1:])
            elif (i == 0 or original[i - 1].lower() != low):
                out.add(original[:i] + char + original[i:])
    return out


def suggest(surname: str, configured: dict[str, list[str]], rules: bool) -> list[str]:
    validate(surname)
    found = {surname, *configured.get(surname, [])}
    if rules:
        found.update(rule_variants(surname))
    return sorted(found)


def regex(spellings: list[str]) -> str:
    return "(?i)(?:" + "|".join(re.escape(s) for s in sorted({fold(x) for x in spellings})) + ")"


def run(args) -> int:
    try:
        validate(args.surname)
        configured = load(args.file) if args.file else {}
        spellings = suggest(args.surname, configured, args.rules)
    except (OSError, ValueError) as exc:
        print(f"gwt-fetch: {exc}", file=sys.stderr)
        return 2
    print(regex(spellings) if args.regex else "\n".join(spellings))
    print("gwt-fetch: search suggestions only; never identity or merge evidence.", file=sys.stderr)
    return 0
