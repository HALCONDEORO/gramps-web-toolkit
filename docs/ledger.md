# gwt-ledger

When several research agents work the same tree at once, they repeat each other's searches. A negative search costs as much as a positive one and leaves nothing in the tree, so nobody sees it was done. `gwt-ledger` is one TSV file every agent reads before a search and appends to after it.

Standard library only, Python 3.8 or newer. Run it as `ledger/gwt-ledger` (symlink it onto your `PATH` if you like) or `python3 ledger/gwt_ledger.py`.

## The file

One row per search, tab-separated, with a header line:

| column | meaning |
|---|---|
| `person_key` | slug of the person searched, e.g. `ana-peiro-toledo` (or a tree id) |
| `person_label` | readable name |
| `source` | slug of where: `familysearch`, `gaceta`, `ad33`, `newspapers`. Names ending in `-verify`, `-sweep` or `-followup` are reserved for the [research loop](agent-loop.md), which logs those task kinds apart from plain searches; `any` stands for a task with no source |
| `query` | the search, normalized: casefolded, no accents, no punctuation. Letters and digits of every script stay, so `Иван Петров, 1880` is stored as `иван петров 1880` and `Ørsted` as `ørsted` |
| `date` | ISO date of the search |
| `result` | `hit`, `none`, `partial`, `blocked` (could not search: a site block, or an authorization you lack; the evidence file says which) or `denied` (the office refused the request) |
| `evidence` | file that holds the result or the negative |
| `agent` | who searched |
| `next_date` | optional ISO date to look again; for an appeal, a few days before the deadline, so there is time to act |

The path comes from `--ledger`, else `$GWT_LEDGER`, else `./buscado.tsv`. The agent name defaults to `$GWT_AGENT`.

## Commands

```sh
export GWT_LEDGER=research/buscado.tsv GWT_AGENT=lane-7

# Before a search. Prints the matching rows (best score first, newest first among equals). Exit 0 if one of them
# is a search that ran (hit, none, partial) and is not due again; exit 1 if not; exit 2 on a usage error.
gwt-ledger check --person "Ana Peiro Toledo" --source familysearch \
  --query "Villanueva del Campo baptisms 1801-1850 Ana Peiro"

# After it, whatever came of it.
gwt-ledger add --person "Ana Peiro Toledo" --label "Ana Peiro Toledo" --source familysearch \
  --query "Villanueva del Campo baptisms 1801-1850 Ana Peiro" --result none \
  --evidence research/lane-7/negatives.md

gwt-ledger list --person ana-peiro-toledo [--source familysearch]
gwt-ledger stats

# Refused with an appeal window: log it with the date to look again, then list what is due.
gwt-ledger add --person ... --source ... --query ... --result denied --next-date 2026-11-10
gwt-ledger due            # rows whose next_date is today or earlier; --on YYYY-MM-DD for another day
```

A ledger written with the older 8 columns needs no manual upgrade: its rows read with an empty `next_date`. A write upgrades its header and preserves its rows.

`add` records the lane from `--agent` (default `$GWT_AGENT`) and the date from `--date` (default today).

In a script: `gwt-ledger check ... >/dev/null && echo "already done, skip"`. Exit 0 already means a search that ran and is not due again, so a `blocked` or `denied` row, or one whose `next_date` has arrived, never skips the search. Add `--any-result` only when you want to know whether anything at all was logged.

## Matching

Person keys and sources compare as slugs, so `Ana Peiró Toledo` and `ana-peiro-toledo` are the same. `--person` and `--source` are optional on `check`; leave them out to look across the whole ledger.

Queries match fuzzily. The score is the best of three measures:

- word-set overlap (Jaccard);
- character similarity (`difflib`);
- for a new query of three words or more, the share of its words already in a logged query. A short query inside a longer logged one counts as done.

Before any of that, every word of the new query must already be in the logged one. Particles (`de`, `la`, `y`, `the`…) don't count, and a word of five letters or more may differ by one inserted, dropped or changed letter. Words with digits (years, film and catalogue numbers) must match exactly, and one logged word never covers two words of the new query. Without this gate, two searches of one catalogue that share a long prefix and differ in one surname (`Real Chancilleria de Granada: Toledo` after `Real Chancilleria de Granada: Peiro Villanueva`) scored over 0.8, and the second search was skipped as already done.

The default threshold is 0.75 (`--threshold` to change it). Reworded, reordered or re-accented queries still match. A different collection, place, name or date range does not.

### What counts as done

`check` prints every matching row but exits 0 only when one of them has the result `hit`, `none` or `partial` and an empty `next_date` or one after today. A `blocked` or `denied` row means the search never ran, and a row whose `next_date` has arrived asked to be searched again, so with only those rows `check` lists them and exits 1. `--any-result` gives the older answer: exit 0 on any matching row.

A `--query` or `--person` that has nothing left after normalising (only punctuation or symbols, say `"!!"` or `"«»"`) is an error, exit 2, on every command. It would otherwise match every row: an empty query scores 1.00 against anything, and an empty person key drops the person filter.

Rows with the same score come out newest first: by `date`, then the row logged last. So a first-row reader sees a recent `hit` before an old `none` for the same search.

### Exact mode, for automatic reuse

`check --exact` matches only rows of the given source whose normalized query is identical to the new one. It ignores `--threshold`, and it refuses to run without a non-empty `--source` (exit 2).

```sh
gwt-ledger check --exact --source familysearch --query "Villanueva del Campo baptisms 1801-1850 Ana Peiro"
```

The default fuzzy check is for a person deciding whether to search. Before a script reuses a logged result in place of searching, use `--exact`, for two reasons:

- **An empty `--source` matches every source.** Leaving `--source` out, or passing an empty value such as an unset `$source`, looks across the whole ledger. A `none` from another site, or from a narrower search there, then answers the check, and the task closes without the search it needed. `--exact` turns that empty value into an error instead.
- **A fuzzy match is not the same search.** A shorter query counts as done when a longer logged one covers it, and one typo is allowed, so the logged row can be a different search that happens to share the words.

Exit 0 from `--exact` already leaves out `blocked` and `denied` rows and rows whose `next_date` has arrived. The first printed row can still be one of those (a newer `blocked` above an older `hit`), so a script that reuses the first row checks its `result` and `next_date` too.

## Traps

- **Log the search, not the result.** The query is what you typed into the source: collection or site, film or years, name ("St Mary baptisms film 004400001 Ana Peiro"). Two long write-ups of the same search do not match each other. On a real ledger, the round-30 write-up and the round-25 write-up of the same film and the same images scored under 0.5 against each other. The same search as a short query scored 1.00 against both.
- **Seeding from prose guesses the person.** A seeder that pulls names out of free text also catches place names: a town called "Villa Nueva" became a person key. Many negatives cover a whole collection and name nobody, so they have no person key. Run `check` without `--person` for collection-level searches, or you miss those rows. A negative that names two people must be filed under each of them: our first seeder used only the first name it matched, so a check for the second person found nothing.
- **A negative from a blocked period is not a negative.** Log it as `blocked`, not `none`, so the next agent repeats it: `check` lists a `blocked` row but does not count it as done.
- **Ledgers written before Unicode normalisation dropped every letter outside a-z.** A query in Cyrillic, Greek or another script was stored as its digits alone (`Иван Петров 1880` as `1880`), and a letter such as `ø` or `ł` vanished from the word. Those rows no longer match the same search typed again, which errs towards searching again. Rows in plain Latin letters, accents included, are unchanged.
- **The ledger in git:** agents that share one checkout also share its git index. Commit the ledger with `git commit -- <ledger path>`, or rows another agent staged go into your commit.

## Concurrency

`add` and `mark` resolve directory aliases to one canonical real path for the ledger and its `<canonical-ledger-path>.lock`. File symlinks (including dangling links), hardlinked ledgers and non-regular files are refused without changing the ledger or link. Hardlinks cannot safely share this replacement protocol because replacing one name separates its inode from the other names.

Writers check the schema before creating a sidecar, then take an exclusive `flock` and check it again before reading the current rows. They accept the legacy eight- and nine-column search layouts and the full reading layout. Unknown headers, additional columns (even if empty), duplicate columns and incompatible row widths are refused with exit 2; extended values such as `extra=KEEP` remain untouched. Reading an unfamiliar header does not imply that writing it is supported.

Writers write all rows to a unique temporary file in the same directory, flush and `fsync` it, then atomically replace the ledger with `os.replace` before unlocking. Existing owner, group, exact permission bits and extended attributes (including POSIX ACLs) are copied and verified before replacement. Failure to inspect, copy or verify these metadata refuses the write and leaves the original file intact; permissions are never widened beyond the original. Platforms without extended-attribute inspection cannot rewrite existing ledgers. New ledgers use mode `0600`. Metadata and replacement errors remove the temporary file. Readers see a complete old or new file. Search values still use the shared tab/newline cleaning helper.

Keep the sidecar lock file: deleting it while writers run would break coordination. Stop **all writers**, upgrade them together, then resume them; old versions lock the ledger inode and cannot coordinate with this replacement protocol. Mixed old/new writers are unsafe. Earlier sidecar writers that did not canonicalize directory aliases also need this coordinated upgrade. Each write rewrites the file, so its cost grows with the ledger. The tests retain the 16-process, 400-search-row check and add concurrent mixed search/reading writes and directory-alias writes. The protocol assumes cooperating writers on a stable filesystem namespace; do not change links or directories while writes run.

`flock` works between processes on one machine. Agents on several machines need the file on one host, or a separate ledger per host merged afterwards.

### One file per lane

When the ledger lives in git and several lanes commit to the same repository, one shared file is a problem even with the lock: a lane's new rows sit uncommitted in a file the other lanes also change. With `--ledger-dir DIR` (or `$GWT_LEDGER_DIR`), `add` writes to `DIR/<agent>.tsv` instead, so each lane only ever changes its own file, and `add` refuses to run without an agent name. `check`, `list`, `due` and `stats` read the `--ledger` file plus every `DIR/*.tsv`, so a row one lane logged counts for all of them, and an older shared ledger keeps counting after the switch.

```sh
export GWT_LEDGER=research/buscado.tsv GWT_LEDGER_DIR=research/ledger.d GWT_AGENT=lane-7
```

## Shared reading index (offline)

Use `gwt-ledger mark|query` (also `gwt ledger mark|query`) for images read in a source. This extends the existing ledger instead of introducing a separate Touraine ledger: reading coverage applies to any source, uses the same path/environment settings, lane discovery, TSV helpers and writer lock, and makes no network requests. The ark is an exact opaque identifier; it is never fetched or normalized. References are stored without opening them.

`mark` records `ark`, `image_from`, `image_to`, `reader`, `date`, `result` and `ref`. Image numbers are positive, inclusive integers. Results are `negative`, `found` or `partial`; all three record images read, without claiming that the source search is complete. Date defaults to today, or accepts `--date YYYY-MM-DD`. A reversed range, zero/negative image, invalid result/date or blank required value exits 2 before any file changes. Reader identities compare exactly; use a consistent spelling.

The first mark extends the TSV header with `ark`, `image_from`, `image_to`, `reader`, `ref`, reusing `date` and `result`. Search rows have empty reading fields and reading rows have empty search fields. Existing 8/9-column search files remain readable and keep their rows when upgraded. Search `check`, `list`, `due`, `stats` and the `find` API ignore reading marks; reading `query` ignores search rows. `read_rows` returns both kinds.

All identifiers in this example are invented:

```sh
gwt-ledger --ledger research/shared.tsv mark --ark ark:/fake/register-A \
  --image-from 1 --image-to 15 --reader ReaderA --date 2026-11-01 \
  --result negative --ref invented-notes.txt
gwt-ledger --ledger research/shared.tsv mark --ark ark:/fake/register-A \
  --image-from 17 --image-to 19 --reader ReaderB --date 2026-11-01 \
  --result found --ref invented-notes.txt
gwt-ledger --ledger research/shared.tsv query --ark ark:/fake/register-A --total 30
```

`query` prints JSON with the exact `ark`, all original reading `rows`, merged inclusive `covered` ranges, `unread` ranges, `double_read` ranges, `total` and `unknown_tail`. The example has `covered: [[1,15],[17,19]]` and `unread: [[16,16],[20,30]]`. ReaderA on 5–10 and ReaderB on 8–12 remain separate rows, with `double_read: [[8,10]]`. Repeated marks by one reader remain recorded but do not count as independent double reading. Interval calculations use range endpoints rather than allocating one entry per image.

With no `--total`, `total` is null and `unknown_tail` is true. Only gaps from image 1 through the highest logged image can be listed: the example then reports image 16 unread, with the tail after 19 unknown. An empty index has no known gaps and an unknown tail. Supply `--total 30` to report 1–30 unread on an empty index. A total below a logged image is an error (exit 2), so it cannot hide coverage. A successful query exits 0 even when gaps remain; usage and read/write errors exit 2.

With `--ledger-dir`, marks write `<dir>/<key(reader)>.tsv` using the existing lane slug helper and query reads the shared ledger plus all lane TSV files. Read commands include each resolved path once, even when the shared ledger is inside the lane directory or reached through a directory alias. Identical rows in separate files remain separate records. Separate reading rows and overlapping readers are preserved even if their names select the same lane filename. Each file is atomic; querying several lane files is not a single transaction across them. Locks coordinate processes on one host, as for search rows.

## Tests

```sh
python3 -m unittest discover -s ledger
python3 .github/scripts/run_tests.py --mutants --only ledger
```

Concurrent ledger writing is supported only for processes running under the same OS UID as the ledger owner. Group/ACL write sharing across UIDs is not supported, even when ledger permissions allow it. Foreign-owned ledgers/sidecars are refused without replacement. Sidecars remain stable, never delete them. Preserve ledger permissions/ACLs for existing readers; deploy all writers together.
