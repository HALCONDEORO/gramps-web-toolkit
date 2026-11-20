# Queue audit

`queue/gwt_queue_audit.py` is an offline, read-only audit of a `gwt_queue` TSV file. It reports problems and
suggests what a person might review. It never changes the queue.

```
python3 queue/gwt_queue_audit.py --queue cola.tsv [--stale-days 14] [--format json|tsv] \
    [--json-out report.json] [--tsv-out report.tsv]
```

The queue path defaults to `$GWT_QUEUE`, then `./cola.tsv`, as in `gwt-queue`. Exit status: `0` no findings,
`1` findings, `2` the queue could not be read. The report files must not be the queue file.

## Read-only guarantee

The queue is opened for reading only: no lock file, no rewrite, no reopening of expired claims, no
reordering, no completing. Standard library only, no network. Every finding has `review_only: true` and a
`suggested_action` starting with `REVIEW ONLY:`; nothing is applied.

## Checks

| code | severity | meaning |
|---|---|---|
| `schema-header` | error | header missing or different from the `gwt_queue` columns |
| `schema-columns` | error | row does not have 19 columns |
| `schema-value` | error | status/kind/result outside the allowed words; priority/tries not integers; bad ISO instants |
| `id-missing` / `id-format` | error / info | empty id; id not `q<number>` |
| `id-duplicate` | error | the same id on several rows |
| `id-collision` | warning | ids that differ only by case or spaces |
| `dedupe-collision` | warning | one `dedupe_key` on several open/claimed rows |
| `stale-blocked` | warning | done as `blocked`/`denied` for at least `--stale-days` (or undated) |
| `stale-claim` | warning | claimed with a lapsed or missing lease (reported only, not reopened) |
| `done-state` | error | done without result or valid `done_at`, holding a token/lease, `done_at` in the future or before `claimed_at`, no recorded try |
| `open-state` | warning | open/claimed/failed row holding done-only fields, or claimed without token |
| `provenance` | warning | empty `source` or `person_key`; `hit` without `evidence` |
| `prereq-missing` / `prereq-self` | error | prerequisite id not in the queue; task waits on itself |
| `prereq-cycle` | error | tasks wait on each other (one finding per member row) |

## Prerequisites

The queue has no prerequisite column. By convention a row lists them in `note` as `needs:q1,q2`
(`after:` and `prereq:` also work).

## Source and private flags

`source` is echoed as found. A row is `private` when its note contains the word `private` or its source
starts with `private`; the flag is echoed in every finding. Messages never quote query, evidence or note
text, so a private row leaks nothing beyond id, line, status and source.

## Output

JSON: `queue`, `read_only`, `rows`, `stale_days`, `summary` (`findings`, `by_severity`, `by_code`) and
`findings`, each with `finding`, `code`, `severity`, `task_id`, `line`, `status`, `source`, `private`,
`message`, `review_only`, `suggested_action`.

TSV: header line, then one line per finding with columns `finding severity code task_id line status source
private message review_only suggested_action` (booleans as `yes`/`no`).

Writing `--json-out` and `--tsv-out`: both reports are written to temp files first, then renamed into
place. If a rename fails, the files already replaced are restored from a snapshot taken beforehand
(best effort). This is not a global transaction: if the process dies (kill, power loss) between two
renames, or a restore itself fails, one report may be new and the other old.

## Tests

`python3 -m unittest queue/test_queue_audit.py` — synthetic rows only, written to temporary files.
