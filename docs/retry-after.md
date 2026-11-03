# Retry-After helper

`gate/gwt_retry_after.py` is an offline, standard-library-only helper that turns
an HTTP `Retry-After` header into a *proposal* for a shared per-host block. It
is not wired into the existing gate; existing gate behavior is unchanged.

## Guarantees

- Never sleeps, never opens sockets, never reads or edits live gate state.
- Pure by default. The only I/O is `persist_proposal`, which runs only when the
  caller passes an explicit scratch path.
- The caller supplies `now` (epoch seconds), so behavior is deterministic.

## Parsing (`parse_retry_after`)

| Input | Result |
|---|---|
| ASCII digits, up to 12 | delta seconds, clamped to `max_delay` (default 24 h) |
| HTTP-date | delay = date − reference time, clamped |
| negative, fractional, exponent, non-ASCII digits, empty, over 128 chars, non-string | invalid (`ok=False`) |
| date more than `skew_tolerance` (default 300 s) in the past | invalid |
| date slightly in the past (within tolerance) | valid, delay 0 |
| unparseable or out-of-range date | invalid |

**Clock skew:** pass the response `Date` header as `server_date`. An HTTP-date
is then measured against the server's clock instead of the local one, so local
skew cancels. An unparseable `server_date` makes the result invalid.

## Proposals

`propose_block(host, scope, value, now)` returns a frozen `BlockProposal`
(`host`, `scope`, `blocked_until`, `delay_seconds`, `kind`, `clamped`,
`created_at`), or `None` when the header is unusable or the delay is zero.

- `host` is lowercased with the trailing dot stripped, and validated.
- `scope` is required and explicit (e.g. `host`, `host:/api/*`). It is validated
  against `[A-Za-z0-9._:/*-]{1,128}`.
- `merge_proposals` keeps the later `blocked_until` for the same host and scope
  and raises on a mismatch, so concurrent 429s extend the shared block and never
  shorten it.
- `is_blocked(proposal, now)` is a pure check.

## Optional persistence

`persist_proposal(proposal, scratch_path, expected_revision=None)`:

- Writes a temp file in the same directory (mode 0600, fsync), then commits
  atomically: `os.link` for create-if-absent, `os.replace` for updates.
- Conflict detection: `expected_revision=None` means the file must not exist.
  Otherwise pass the token from `read_revision(path)`. A mismatch, or a held
  `<path>.lock` from a concurrent writer, raises `ConflictError`. Nothing is
  overwritten on conflict.
- Returns the new revision token (SHA-256 of the content).
- `load_proposal(path)` reads it back (`None` if absent).

Hardening:

- `now`, `max_delay`, `skew_tolerance` must be finite non-bool numbers
  (`parse_retry_after` returns invalid; `propose_block`/`is_blocked` raise).
- Hosts must be ASCII (so e.g. U+212A cannot lowercase into `k`), at most one
  trailing dot, valid DNS labels (1-63 chars, no leading/trailing hyphen).
  Uppercase ASCII letters are valid and are lowercased (`ExampKe.com` becomes
  `exampke.com`); characters such as `_` are rejected (`examp_le.com`).
- The scratch path must not be a symlink or non-regular file (checked with
  `lstat` in persist/read/load); the lock is opened with `O_EXCL|O_NOFOLLOW`;
  the temp file is created beside the target without lexical `..` collapsing.
- Updates are merged under the lock: if the file holds the same host+scope, the
  later `blocked_until` is kept, so a stale-but-matching writer cannot shorten a
  block. A different scope or host replaces the file.
- `load_proposal` validates field types (no bools, NaN, Infinity, or ints too
  large for a float such as `10**400`, which are treated as non-numbers rather
  than raising `OverflowError`), requires non-negative timestamps, a positive
  delay, a known `kind`, and `created_at + delay_seconds == blocked_until`. It
  returns the proposal with the normalized host.
- **Stale lock: fail-closed by design.** A leftover `<path>.lock` (e.g. after a
  crash) always raises `ConflictError`. It is never auto-deleted or broken; an
  operator must confirm no writer is running and remove it manually.
- **Corrupt scratch: no automatic reset.** `load_proposal` raises
  `RetryAfterError` and `persist_proposal` will not overwrite it. Recovery is
  manual and explicit only: inspect the file, then remove it yourself.

Typical flow: `rev = read_revision(p)`, load, merge, `persist_proposal(merged, p, rev)`.
On `ConflictError`, re-read and retry. Retry policy belongs to the caller.

## Offline demo

`python3 -I gate/demo_retry_after.py` runs eight synthetic scenarios (delta,
date, clock skew, huge numbers, host/scope normalization, longer-block merge,
stale-lock fail-closed, corrupt-scratch no-overwrite). It uses only the standard
library and `TemporaryDirectory`s: no network, no live gate state. Exit status
is 0 only if all scenarios pass.

## Tests

`python3 -m unittest gate/test_retry_after.py` (synthetic data and temp
directories only).
