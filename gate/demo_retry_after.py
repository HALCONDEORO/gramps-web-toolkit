#!/usr/bin/env python3
"""Offline demo of gate/gwt_retry_after.py (stdlib only, fully synthetic).

Usage: python3 -I gate/demo_retry_after.py

No network, no accounts, no live gate state. Every file lives in a
TemporaryDirectory that is removed on exit. Exit status is 0 only if every
scenario behaves as documented in docs/retry-after.md.
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import gwt_retry_after as ra  # noqa: E402

NOW = 1_445_412_480.0  # synthetic "local" epoch: Wed, 21 Oct 2015 07:28:00 GMT
DATE_60S = "Wed, 21 Oct 2015 07:29:00 GMT"
HOST, SCOPE = "example.com", "host"


def expect(cond: bool, label: str) -> None:
    if not cond:
        raise AssertionError(label)
    print("  ok:", label)


def raises(exc, fn, *args, **kw) -> bool:
    try:
        fn(*args, **kw)
    except exc:
        return True
    return False


def demo_delta() -> None:
    p = ra.parse_retry_after("120", NOW)
    expect(p.ok and p.kind == "delta" and p.delay_seconds == 120.0, "delta 120")
    for bad in ("-5", "1.5", "1e3", "١٢", "", "x" * 200):
        expect(not ra.parse_retry_after(bad, NOW).ok, f"invalid {bad[:8]!r}")


def demo_date() -> None:
    p = ra.parse_retry_after(DATE_60S, NOW)
    expect(p.ok and p.kind == "date" and p.delay_seconds == 60.0, "date +60s")
    near = ra.parse_retry_after("Wed, 21 Oct 2015 07:26:00 GMT", NOW)
    expect(near.ok and near.delay_seconds == 0.0, "slightly past -> 0")
    old = ra.parse_retry_after("Wed, 21 Oct 2015 06:00:00 GMT", NOW)
    expect(not old.ok, "far past rejected")


def demo_clock_skew() -> None:
    skewed_now = NOW + 3600  # local clock one hour fast
    naive = ra.parse_retry_after(DATE_60S, skewed_now)
    expect(not naive.ok, "skewed local clock alone misjudges date")
    fixed = ra.parse_retry_after(
        DATE_60S, skewed_now, server_date="Wed, 21 Oct 2015 07:28:00 GMT")
    expect(fixed.ok and fixed.delay_seconds == 60.0, "server Date cancels skew")
    junk = ra.parse_retry_after(DATE_60S, NOW, server_date="not a date")
    expect(not junk.ok, "bad server Date invalid")


def demo_huge_numbers() -> None:
    p = ra.parse_retry_after("999999999999", NOW)
    expect(p.ok and p.clamped and p.delay_seconds == ra.MAX_DELAY_SECONDS,
           "12 digits clamped to 24h")
    expect(not ra.parse_retry_after("9" * 13, NOW).ok, "13 digits invalid")
    expect(not ra.parse_retry_after("5", 10 ** 400).ok, "huge now invalid")
    expect(raises(ra.RetryAfterError, ra.propose_block,
                  HOST, SCOPE, "5", 10 ** 400), "propose huge now raises")
    expect(not ra.parse_retry_after("5", float("nan")).ok, "NaN now invalid")


def demo_normalization() -> None:
    p = ra.propose_block(" ExAmple.COM. ", SCOPE, "30", NOW)
    expect(p is not None and p.host == "example.com", "host lowercased/dot")
    for bad in ("examp_le.com", "Kelvin.com", "a..b", "-a.com", ""):
        expect(raises(ra.RetryAfterError, ra.normalize_host, bad),
               f"host rejected {bad!r}")
    expect(raises(ra.RetryAfterError, ra.validate_scope, "bad scope"),
           "scope rejected")
    expect(ra.propose_block(HOST, SCOPE, "0", NOW) is None, "zero -> None")


def _prop(delay: str):
    return ra.propose_block(HOST, SCOPE, delay, NOW)


def demo_longer_block_merge() -> None:
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "scratch.json")
        ra.persist_proposal(_prop("60"), path)
        ra.persist_proposal(_prop("3600"), path, ra.read_revision(path))
        expect(ra.load_proposal(path).delay_seconds == 3600.0, "longer wins")
        ra.persist_proposal(_prop("30"), path, ra.read_revision(path))
        expect(ra.load_proposal(path).delay_seconds == 3600.0,
               "shorter never shortens")
        expect(raises(ra.ConflictError, ra.persist_proposal, _prop("5"), path),
               "create-if-absent conflicts on existing")
        expect(raises(ra.ConflictError, ra.persist_proposal,
                      _prop("5"), path, "0" * 64), "stale revision conflicts")


def demo_stale_lock() -> None:
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "scratch.json")
        lock = path + ".lock"
        open(lock, "w").close()
        expect(raises(ra.ConflictError, ra.persist_proposal, _prop("60"), path),
               "stale lock fails closed")
        expect(os.path.exists(lock), "lock not auto-removed")
        expect(not os.path.exists(path), "nothing written under lock")


def demo_corrupt_scratch() -> None:
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "scratch.json")
        junk = b'{"host": "example.com", garbage'
        with open(path, "wb") as fh:
            fh.write(junk)
        expect(raises(ra.RetryAfterError, ra.load_proposal, path),
               "corrupt load raises")
        expect(raises(ra.ConflictError, ra.persist_proposal, _prop("60"), path),
               "create over corrupt conflicts")
        expect(raises(ra.RetryAfterError, ra.persist_proposal,
                      _prop("60"), path, ra.read_revision(path)),
               "update over corrupt refused")
        with open(path, "rb") as fh:
            expect(fh.read() == junk, "corrupt file untouched")
        expect(not os.path.exists(path + ".lock"), "lock released")


SCENARIOS = (
    ("delta", demo_delta),
    ("date", demo_date),
    ("clock skew", demo_clock_skew),
    ("huge numbers", demo_huge_numbers),
    ("normalization", demo_normalization),
    ("longer-block merge", demo_longer_block_merge),
    ("stale lock fail-closed", demo_stale_lock),
    ("corrupt scratch no-overwrite", demo_corrupt_scratch),
)


def main() -> int:
    failed = 0
    for name, fn in SCENARIOS:
        print(f"[{name}]")
        try:
            fn()
        except AssertionError as exc:
            failed += 1
            print("  FAIL:", exc)
    print(f"{len(SCENARIOS) - failed}/{len(SCENARIOS)} scenarios passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
