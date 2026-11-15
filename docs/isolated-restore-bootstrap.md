# Isolated restore: native bootstrap route and custody core

How to prove a Gramps Web backup actually restores, without touching the
production instance: bring up a throwaway isolated instance, create a tree and
owner through the native API, restore into it, and compare. This document is
the route; `restore/core.py` is the reusable custody component, tested against
mocks only. Nothing here runs against a live server by itself.

## Ground rules

- A brand-new isolated instance: its own empty user database, search index and
  task queue. No production auth database, tokens, sessions, OIDC or email, and
  no shared Celery/Redis.
- Never reuse the production tree's UUID. The clone tree must be a fresh UUID,
  checked to differ from the original before any restore.
- Secrets never travel in command-line arguments, shell history or logs (see
  the password caveat below).

## Native bootstrap sequence (Gramps Web API 3.25.1)

1. Create the bootstrap administrator with the supported module CLI:
   `python3 -m gramps_webapi user add`. In a multi-tree instance an admin may
   be treeless; a non-admin without a tree is rejected. Role constants:
   `ROLE_ADMIN = 5`, `ROLE_OWNER = 4` (`gramps_webapi/auth/const.py`).
2. The native token endpoint lets a treeless admin obtain a token (admins hold
   `PERM_VIEW_OTHER_TREE`). That token carries no tree claim and must never be
   handed to the restore runner.
3. As bootstrap admin, `POST /trees/` creates a fresh UUIDv4 tree. Compare the
   new UUID against the production original and reject equality.
4. Create the owner with the CLI again, this time with `ROLE_OWNER` and the new
   tree UUID, then log in as the owner: the server-issued owner token carries
   the tree claim.
5. Prove identity from the server's side: an authenticated `GET /trees/` (own
   tree) must return the new UUID, distinct from the original, before any
   backup upload or restore. A client-side JWT decode alone is not proof.
6. Only then: a restore dry run, and after it validates, one actual restore
   into the clone tree.

## Password caveat

The native CLI takes the password as a positional argument, so a literal
secret on the command line would leak into process lists and shell history. A
runner must instead call `auth.add_user` in-process inside
`create_app().app_context()` with a secret supplied through an approved
private channel, or use a verified secure prompt/file interface.

## The custody core (`restore/core.py`)

Import-only, standard library only, no network or subprocess:

- **Journal** writes a durable, fsynced INTENT record before any side effect
  and a result record only after the response validates. Receipts hold fixed
  state and timestamps only — never headers, bodies, tokens or person data.
- **Once** refuses to repeat a phase that already has an intent, so a lost
  acknowledgement after a landed mutation never triggers an automatic resend.
  Exceptions surface as a fixed UNKNOWN with no message or chained trace.
- **Runner** drives the bootstrap order above through an adapter it does not
  implement: a live isolation/authority attestation is required before
  bootstrap and before each restore call, and restore is impossible until
  the same runner completed the owner identity readback. A dry run precedes the one actual restore.
- **Oracles**: the graph oracle compares exact normalized
  namespace → handle → object maps (counts and SHA-256 digests only, no person
  fields); the media oracle verifies a supplied manifest against lexically confined,
  symlink-free paths with size and hash checks, holding on any missing file.

The 13 tests in `restore/test_core.py` run entirely against fakes: lost-ACK
replay protection, secret-sentinel non-disclosure, UUID mismatch rejection,
graph drift, media path escape/symlink/hash drift, the attestation gate,
bootstrap ordering and the restore identity gate. Passing them says nothing
about a live native API; a real adapter needs its own review.

## Sources (tagged upstream, fetched 2026-11-15)

- `gramps_webapi/__main__.py` — CLI user creation and the treeless-admin rule:
  https://raw.githubusercontent.com/gramps-project/gramps-web-api/v3.25.1/gramps_webapi/__main__.py
- `gramps_webapi/auth/__init__.py` — native add_user (new UUID, hashed
  password, no email sent): https://raw.githubusercontent.com/gramps-project/gramps-web-api/v3.25.1/gramps_webapi/auth/__init__.py
- `gramps_webapi/auth/const.py` — role and permission constants:
  https://raw.githubusercontent.com/gramps-project/gramps-web-api/v3.25.1/gramps_webapi/auth/const.py
- `gramps_webapi/api/resources/token.py` — treeless admin token and tree claim:
  https://raw.githubusercontent.com/gramps-project/gramps-web-api/v3.25.1/gramps_webapi/api/resources/token.py
- `gramps_webapi/api/resources/trees.py` — tree creation and own-tree checks:
  https://raw.githubusercontent.com/gramps-project/gramps-web-api/v3.25.1/gramps_webapi/api/resources/trees.py
- `gramps_webapi/api/resources/importers.py` — restore endpoints:
  https://raw.githubusercontent.com/gramps-project/gramps-web-api/v3.25.1/gramps_webapi/api/resources/importers.py
- User management docs (unversioned corroboration only):
  https://github.com/gramps-project/gramps-web-docs/blob/main/docs/en/install_setup/users.md
