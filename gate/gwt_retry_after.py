"""Offline Retry-After parser and per-host block proposal helper.

Pure functions: never sleeps, never touches the network, never edits live
gate state. Persistence happens only via ``persist_proposal`` when the caller
passes an explicit scratch path.
"""
from __future__ import annotations

import json
import math
import os
import re
import stat
import tempfile
from dataclasses import dataclass, asdict, replace
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Optional

MAX_DELAY_SECONDS = 24 * 3600
DEFAULT_SKEW_TOLERANCE = 5 * 60
_MAX_VALUE_LEN = 128
_DELTA_RE = re.compile(r"[0-9]{1,12}")
_LABEL_RE = re.compile(r"[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?")
_SCOPE_RE = re.compile(r"[A-Za-z0-9._:/*-]{1,128}")


class RetryAfterError(ValueError):
    """Invalid input to a proposal function."""


class ConflictError(RuntimeError):
    """Scratch file changed since the caller last read it."""


@dataclass(frozen=True)
class ParsedRetryAfter:
    ok: bool
    delay_seconds: float = 0.0
    kind: str = "invalid"  # delta | date | invalid
    reason: str = ""
    clamped: bool = False


@dataclass(frozen=True)
class BlockProposal:
    host: str
    scope: str
    blocked_until: float  # epoch seconds (caller's clock)
    delay_seconds: float
    kind: str
    clamped: bool
    created_at: float


def _is_num(x) -> bool:
    """Finite real number; bools are rejected."""
    if not isinstance(x, (int, float)) or isinstance(x, bool):
        return False
    try:
        return math.isfinite(x)
    except OverflowError:  # huge int (e.g. 10**400) not representable as float
        return False


def _bad(reason: str) -> ParsedRetryAfter:
    return ParsedRetryAfter(False, 0.0, "invalid", reason)


def _clamp(delay: float, max_delay: float, kind: str) -> ParsedRetryAfter:
    clamped = delay > max_delay
    return ParsedRetryAfter(True, float(min(delay, max_delay)), kind, "", clamped)


def _parse_date(text: str) -> Optional[datetime]:
    try:
        dt = parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError, OverflowError):
        return None
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _epoch(dt: datetime) -> Optional[float]:
    try:
        return dt.timestamp()
    except (OverflowError, OSError, ValueError):
        return None


def parse_retry_after(
    value,
    now: float,
    *,
    server_date: Optional[str] = None,
    max_delay: float = MAX_DELAY_SECONDS,
    skew_tolerance: float = DEFAULT_SKEW_TOLERANCE,
) -> ParsedRetryAfter:
    """Parse a Retry-After value into a bounded non-negative delay.

    ``now`` is the caller's epoch time. If ``server_date`` (the response Date
    header) is given, an HTTP-date is measured against it so local clock skew
    cancels out. Past dates within ``skew_tolerance`` yield 0; far-past dates
    are rejected as malformed/stale.
    """
    if not (_is_num(now) and _is_num(max_delay) and _is_num(skew_tolerance)
            and max_delay >= 0 and skew_tolerance >= 0):
        return _bad("bad numeric argument")
    if not isinstance(value, str):
        return _bad("not a string")
    text = value.strip()
    if not text or len(text) > _MAX_VALUE_LEN:
        return _bad("empty or too long")
    if text.isascii() and _DELTA_RE.fullmatch(text):
        return _clamp(float(int(text)), max_delay, "delta")
    if text.lstrip().startswith("-") and text[1:].strip().isdigit():
        return _bad("negative delta")
    return _parse_http_date(text, now, server_date, max_delay, skew_tolerance)


def _reference_time(now: float, server_date: Optional[str]) -> Optional[float]:
    if server_date is None:
        return now
    dt = _parse_date(server_date.strip()) if isinstance(server_date, str) else None
    return None if dt is None else _epoch(dt)


def _parse_http_date(text, now, server_date, max_delay, tolerance):
    dt = _parse_date(text)
    target = None if dt is None else _epoch(dt)
    if target is None:
        return _bad("unparseable")
    ref = _reference_time(now, server_date)
    if ref is None:
        return _bad("bad server date")
    delta = target - ref
    if delta < -tolerance:
        return _bad("date in the past")
    return _clamp(max(delta, 0.0), max_delay, "date")


def normalize_host(host: str) -> str:
    if not isinstance(host, str):
        raise RetryAfterError("host must be a string")
    h = host.strip()
    if not h.isascii():  # before lower(): U+212A lowercases to ASCII 'k'
        raise RetryAfterError("invalid host")
    h = h.lower()
    if h.endswith("."):
        h = h[:-1]  # a single trailing root dot only
    if not h or len(h) > 253 or not all(
            _LABEL_RE.fullmatch(lbl) for lbl in h.split(".")):
        raise RetryAfterError("invalid host")
    return h


def validate_scope(scope: str) -> str:
    if not isinstance(scope, str) or not _SCOPE_RE.fullmatch(scope):
        raise RetryAfterError("explicit valid scope required")
    return scope


def propose_block(
    host: str, scope: str, value, now: float, **kwargs
) -> Optional[BlockProposal]:
    """Return a proposal, or None if the header is unusable or zero-delay."""
    h = normalize_host(host)
    s = validate_scope(scope)
    if not _is_num(now):
        raise RetryAfterError("now must be a finite non-bool number")
    parsed = parse_retry_after(value, now, **kwargs)
    if not parsed.ok or parsed.delay_seconds <= 0:
        return None
    return BlockProposal(
        h, s, now + parsed.delay_seconds, parsed.delay_seconds,
        parsed.kind, parsed.clamped, now,
    )


def merge_proposals(a: Optional[BlockProposal], b: Optional[BlockProposal]):
    """Combine two proposals for the same host+scope; the later block wins."""
    if a is None or b is None:
        return a or b
    if (a.host, a.scope) != (b.host, b.scope):
        raise RetryAfterError("host/scope mismatch")
    return a if a.blocked_until >= b.blocked_until else b


def is_blocked(p: Optional[BlockProposal], now: float) -> bool:
    if not _is_num(now):
        raise RetryAfterError("now must be a finite non-bool number")
    return p is not None and now < p.blocked_until


def _revision(raw: bytes) -> str:
    import hashlib
    return hashlib.sha256(raw).hexdigest()


def _check_plain(path) -> None:
    """Reject symlinks and non-regular files at the scratch path."""
    if not isinstance(path, str) or not path or path.endswith(os.sep):
        raise RetryAfterError("explicit scratch path required")
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return
    if not stat.S_ISREG(st.st_mode):
        raise RetryAfterError("scratch path must be a regular file, not a symlink")


def read_revision(path: str) -> Optional[str]:
    """Revision token of the scratch file, or None if it does not exist."""
    _check_plain(path)
    try:
        with open(path, "rb") as fh:
            return _revision(fh.read())
    except FileNotFoundError:
        return None


def _write_tmp(directory: str, data: bytes) -> str:
    fd, tmp = tempfile.mkstemp(prefix=".ra-", dir=directory)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return tmp


def _commit(tmp: str, path: str, expected: Optional[str]) -> None:
    if expected is None:
        try:
            os.link(tmp, path)  # atomic create-if-absent
        except FileExistsError:
            raise ConflictError("file already exists") from None
        os.unlink(tmp)
        return
    if read_revision(path) != expected:
        raise ConflictError("revision mismatch")
    os.replace(tmp, path)


def persist_proposal(
    proposal: BlockProposal, scratch_path: str, expected_revision: Optional[str] = None
) -> str:
    """Atomically write ``proposal`` (mode 0600) to an explicit scratch path.

    ``expected_revision`` is None to require the file not exist, otherwise the
    token from ``read_revision``. Raises ConflictError on mismatch. Returns the
    new revision token.
    """
    _check_plain(scratch_path)
    # Same directory as the target (no lexical ".." normalisation) so the
    # final rename stays on one filesystem.
    directory = os.path.dirname(scratch_path) or "."
    # Stale lock policy: a leftover "<path>.lock" (e.g. after a crash) is
    # intentionally fail-closed: ConflictError, never auto-deleted or broken.
    # An operator must verify no writer is running and remove it manually.
    lock = scratch_path + ".lock"
    try:
        lock_fd = os.open(
            lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    except FileExistsError:
        raise ConflictError("concurrent writer holds lock") from None
    tmp = None
    try:
        if expected_revision is not None and read_revision(scratch_path) == expected_revision:
            # Never shorten an existing block for the same host+scope.
            existing = load_proposal(scratch_path)
            if (existing.host, existing.scope) == (proposal.host, proposal.scope):
                proposal = merge_proposals(existing, proposal)
        data = (json.dumps(asdict(proposal), sort_keys=True) + "\n").encode()
        tmp = _write_tmp(directory, data)
        _commit(tmp, scratch_path, expected_revision)
        tmp = None
    finally:
        os.close(lock_fd)
        os.unlink(lock)
        if tmp is not None:
            try:
                os.unlink(tmp)
            except OSError:
                pass
    return _revision(data)


def _validate_fields(p: BlockProposal) -> BlockProposal:
    """Type, range and coherence checks; returns p with normalized host."""
    p = replace(p, host=normalize_host(p.host), scope=validate_scope(p.scope))
    if not (_is_num(p.blocked_until) and _is_num(p.delay_seconds)
            and _is_num(p.created_at) and isinstance(p.clamped, bool)
            and p.kind in ("delta", "date")):
        raise ValueError("bad field types")
    if p.blocked_until < 0 or p.created_at < 0 or p.delay_seconds <= 0:
        raise ValueError("negative or zero field")
    # Absolute-only bound. propose_block computes blocked_until as the same
    # float sum and json round-trips floats exactly, so legitimate files differ
    # by 0; 1e-3 s only absorbs hand-edited/rounded values. A relative term
    # (1e-9) would allow ~1.8 s at epoch 1.8e9 and grows with magnitude.
    if not math.isclose(p.created_at + p.delay_seconds, p.blocked_until,
                        rel_tol=0.0, abs_tol=1e-3):
        raise ValueError("incoherent timestamps")
    return p


def _finite_float(text: str) -> float:
    import argparse
    try:
        x = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError("not a number: %r" % text) from None
    if not _is_num(x):
        raise argparse.ArgumentTypeError("must be finite")
    return x


def main(argv=None) -> int:
    """Offline CLI: print a JSON proposal to stdout. Never persists, sleeps,
    touches the network or live gate state. Exit 0 ok, 1 unusable value,
    2 invalid arguments."""
    import argparse
    ap = argparse.ArgumentParser(
        prog="gwt-retry-after",
        description="Offline Retry-After parser; prints a JSON proposal.")
    ap.add_argument("--value", required=True, help="Retry-After header value")
    ap.add_argument("--now", required=True, type=_finite_float,
                    help="caller epoch seconds")
    ap.add_argument("--host", required=True)
    ap.add_argument("--scope", required=True)
    ap.add_argument("--server-date", default=None,
                    help="response Date header, to cancel clock skew")
    args = ap.parse_args(argv)
    try:
        host, scope = normalize_host(args.host), validate_scope(args.scope)
    except RetryAfterError as exc:
        ap.error(str(exc))  # exits 2
    parsed = parse_retry_after(args.value, args.now, server_date=args.server_date)
    if not parsed.ok:
        print(json.dumps({"ok": False, "reason": parsed.reason}, sort_keys=True))
        return 1
    prop = propose_block(host, scope, args.value, args.now,
                         server_date=args.server_date)
    print(json.dumps({"ok": True, "proposal": None if prop is None else asdict(prop)},
                     sort_keys=True))
    return 0


def load_proposal(scratch_path: str) -> Optional[BlockProposal]:
    """Load a proposal. Corrupt/invalid files raise RetryAfterError.

    A corrupt scratch file is never reset or deleted automatically; recovery
    is a manual, explicit operator action (inspect, then remove the file).
    """
    _check_plain(scratch_path)
    try:
        with open(scratch_path, "r", encoding="utf-8") as fh:
            return _validate_fields(BlockProposal(**json.load(fh)))
    except FileNotFoundError:
        return None
    except (ValueError, TypeError, OverflowError, RetryAfterError) as exc:
        raise RetryAfterError("corrupt scratch file") from exc
