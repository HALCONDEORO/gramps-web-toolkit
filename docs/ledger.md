# gwt-ledger

When several research agents work the same tree at once, they repeat each other's searches. A negative search costs as much as a positive one and leaves nothing in the tree, so nobody sees it was done. `gwt-ledger` is one TSV file every agent reads before a search and appends to after it.

Standard library only, Python 3.8 or newer (`coverage` also reads the tree through the toolkit's core, Python 3.11 or newer). Run it as `ledger/gwt-ledger` (symlink it onto your `PATH` if you like) or `python3 ledger/gwt_ledger.py`.

## The file

One row per search, tab-separated, with a header line:

| column | meaning |
|---|---|
| `person_key` | slug of the person searched, e.g. `ana-peiro-toledo` (or a tree id) |
| `person_label` | readable name |
| `source` | canonical id of where, from the [source registry](#sources): `familysearch`, `gazeta`, `bvph`. Names ending in `-verify`, `-sweep` or `-followup` are reserved for the [research loop](agent-loop.md), which logs those task kinds apart from plain searches (`fs-verify` is stored as `familysearch-verify`); `any` stands for a task with no source |
| `query` | the search, normalized: casefolded, no accents, no punctuation. Letters and digits of every script stay, so `Иван Петров, 1880` is stored as `иван петров 1880` and `Ørsted` as `ørsted` |
| `date` | ISO date of the search |
| `result` | one of the words in [Results](#results) |
| `evidence` | file that holds the result or the negative |
| `agent` | who searched |
| `next_date` | optional ISO date (`YYYY-MM-DD`) to look again; for an appeal, a few days before the deadline, so there is time to act. Any other text, typed into the file by hand, is ignored: the row is never due ([rows written by hand](#rows-written-by-hand)) |
| `control` | the proof a `none` stands on: the `--control-ok` reference ([below](#a-none-needs-proof)) |

The path comes from `--ledger`, else `$GWT_LEDGER`, else `./buscado.tsv`. A write never creates that last default: when no ledger is named, no `--ledger-dir` or `$GWT_LEDGER_DIR` is set and `./buscado.tsv` does not exist, `add` and `import-tsv` exit 2, name the path and write nothing, so a logger run from the wrong folder cannot log into a stray file. An existing `./buscado.tsv` is still written, and with a ledger directory both commands write the agent's lane file as before. The agent name defaults to `$GWT_AGENT`.

## Commands

```sh
export GWT_LEDGER=research/buscado.tsv GWT_AGENT=lane-7

# Before a search. Prints the matching rows (best score first, newest first among equals). Exit 0 if one of them
# is a search that ran (hit, none, partial, out-of-coverage) and is not due again; exit 1 if not; exit 2 on a usage error.
gwt-ledger check --person "Ana Peiro Toledo" --source familysearch \
  --query "Villanueva del Campo baptisms 1801-1850 Ana Peiro"

# After it, whatever came of it. A none names the control run that proved the site could answer.
gwt-ledger add --person "Ana Peiro Toledo" --label "Ana Peiro Toledo" --source familysearch \
  --query "Villanueva del Campo baptisms 1801-1850 Ana Peiro" --result none \
  --control-ok runs/fs-control.txt --evidence research/lane-7/negatives.md

gwt-ledger list --person ana-peiro-toledo [--source familysearch]
gwt-ledger stats [--by source]

# Refused with an appeal window: log it with the date to look again, then list what is due.
gwt-ledger add --person ... --source ... --query ... --result denied --next-date 2026-11-10
gwt-ledger due            # rows whose next_date is today or earlier; --on YYYY-MM-DD for another day
```

A ledger written with the older 8 or 9 columns needs no manual upgrade: rows read with empty missing columns. A write atomically upgrades its header, preserving the rows and any supported image-reading columns.

`add` records the lane from `--agent` (default `$GWT_AGENT`) and the date from `--date` (default today, `YYYY-MM-DD`). `check` accepts `--agent`, `--label` and `--date` too, so a lane can pass both commands the same arguments; on `check`, `--date` is the day the check judges by (whether a `next_date` has arrived). `check --person P --source S` without `--query` asks whether that source was searched for that person at all. `add` skips a row the ledger already holds, in any lane file (same person, source, normalized query, date and result), with a note on stderr naming it and exit 0, so a logger run twice logs each search once, even two runs at the same moment (the check runs under the file's lock); a voided row does not count, so a search run again after a void is logged.

In a script: `gwt-ledger check ... >/dev/null && echo "already done, skip"`. Exit 0 already means a search that ran and is not due again, so a `blocked` or `denied` row, or one whose `next_date` has arrived, never skips the search. Add `--any-result` only when you want to know whether anything at all was logged. A sweep that checks many searches in a row sends them as [one batch](#many-checks-at-once).

## Sources

One source has one id. Without that, the same site gets logged as `fs`, `familysearch` and `familysearch-index`, and a check under one name misses the searches logged under the others. [`ledger/sources.tsv`](https://github.com/GeiserX/gramps-web-toolkit/blob/main/ledger/sources.tsv) is the shipped registry: one row per source, tab-separated.

| column | meaning |
|---|---|
| `id` | the canonical id, a lowercase slug. An id ending in `-*` covers every id with that prefix: `parish-scan-*` covers `parish-scan-12` |
| `aliases` | other names for the same source, comma-separated: `fs,familysearch-index,fs-index` |
| `country` | ISO 3166 code, `XX` for a source that spans countries |
| `kind` | `index` (transcribed records), `image` (scans), `fulltext` (searchable text), `catalogue` (finding aids) or `newspaper` |
| `from`, `to` | the years its records cover; empty when open or too varied to say. [`coverage`](#coverage) uses them |

`add` and `check` turn `--source` into its id: `--source fs` is stored and matched as `familysearch`, and rows an older ledger logged under an alias count for the id. The ids of the sources [`gwt read`](read.md) knows (`boe`, `familysearch`, `genae`, `insee`, `pares`) are the same ids here.

A source the registry does not know is kept as its slug, and `add` notes it on stderr: your own archive works without a registry file. A name close to a known id or alias is different: `familysea`, `gaceta-anexo` or `familysearch-imagenes` is a typo or another spelling of a source already in the registry, and logging it would start a new spelling that `check` never matches. That is an error, exit 2, naming the close match, on `add`, `check`, `import-tsv` and `coverage`. Use the known name, add yours as its alias in your own registry file, or pass `--new-source` when it really is a different source:

```sh
gwt-ledger sources [--country ES]          # the registry, shipped and yours
export GWT_LEDGER_SOURCES=research/my-sources.tsv   # or --registry FILE; same columns, extends the shipped one
```

A row of your file whose id is already shipped adds its aliases to that source and replaces the fields it fills; it may leave `kind` and the rest empty. A file saved with a byte order mark (a UTF-8 export from a spreadsheet) reads the same. An alias that already names another source, a kind outside the five, or a year that is not a number stops every command with exit 2, so a broken registry never matches the wrong rows.

## Results

| result | meaning | counts as done |
|---|---|---|
| `hit` | found | yes |
| `none` | searched, nothing there, and a control run proved the source could answer (`--control-ok`) | yes |
| `partial` | something, not enough: this exact query ran, so rerunning it buys nothing; a narrower query is the next step. `coverage` and `stats` show it as `partial`, never as proof of absence | yes |
| `out-of-coverage` | the source cannot hold it (other years, other place) | yes |
| `unproven` | a `none` with no proof the source worked | no |
| `blocked` | could not search: a site block, or an authorization you lack; the evidence file says which | no |
| `denied` | the office refused the request | no |
| `restricted` | the record exists but your access cannot read it | no |
| `paid` | behind a payment you have not made | no |
| `in-person` | only readable at the archive | no |
| `void` | written by [`void`](#retracting-a-row) only: retracts earlier rows | no |

`add` takes exactly these words: `--result found` or `--result negative` is an error, exit 2. Only [`import-tsv`](#import-another-log) maps other words (`found` to `hit`; `negative`, `nothing`, `miss` to `none`; `not-reviewed` to `unproven`).

### Rows written by hand

A row typed straight into a ledger or lane file skips the checks of `add`. Every command reads such a row the way `import-tsv` would have stored it: a word of its synonym map counts as its result (`found` as `hit`; `negative`, `nothing` or `miss` as `none`, and as `unproven` when the `control` column is empty). The file keeps the word as written, so the row's id, and any `void` that names it, stay the same. `list` prints the mapped word.

A row whose result is still none of the words above counts as not searched. A row whose `next_date` is not a `YYYY-MM-DD` date (free text, `20270101`, `2026-02-30`) is never due: `check`, `due`, `due --ics`, `scan check` and `coverage` read it as having no `next_date`. `check`, `void`, `due`, `list`, `stats`, `coverage` and `scan check` print one note on stderr for each of the two cases, with the count and the first such row as `file:line`. Their stdout does not change, so a script that reads it keeps working. Fix the row in the file, or void it and log it again with `add`.

### A none needs proof

A search that finds nothing proves nothing unless the site could have answered: a soft block can serve an empty page with HTTP 200. So `add --result none` needs `--control-ok RUN`, a reference to the control run that proved the source answered. Without it the row is stored as `unproven`, a note goes to stderr, and the search counts as not done, so the next lane runs it again. A `none` logged before 1.1.0, when the ledger had no `control` column, reads the same way: every command takes it as `unproven`, though the file still says `none`.

```sh
gwt read boe control > runs/boe-control.txt     # the canaries, see gwt-read
gwt-ledger add --person "Luis Gil" --source boe --query "edicto 1902 Luis Gil" --result none \
  --control-ok runs/boe-control.txt --evidence runs/edicto-1902.json
```

`RUN` is either the saved output of [`gwt read <source> control`](read.md#canary-controls) or any run id your own runner gives a control. A file is checked: it must hold at least one `control ... ok` line and no `void` or `SHORT` line, or `add` stops with exit 2 and writes nothing. A run id is stored as given. A value that looks like a path (it has a `/` or a file extension such as `.txt`) but names no file is a typo, exit 2, never a run id; so are `no`, `-` and other words that prove nothing.

## Matching

Person keys compare as slugs and sources as registry ids, so `Ana Peiró Toledo` and `ana-peiro-toledo` are the same. `--person` and `--source` are optional on `check`; leave them out to look across the whole ledger.

Queries match fuzzily. The score is the best of three measures:

- word-set overlap (Jaccard);
- character similarity (`difflib`);
- for a new query of three words or more, the share of its words already in a logged query. A short query inside a longer logged one counts as done.

Before any of that, every word of the new query must already be in the logged one. Particles (`de`, `la`, `y`, `the`…) don't count, and a word of five letters or more may differ by one inserted, dropped or changed letter. Words with digits (years, film and catalogue numbers) must match exactly, and one logged word never covers two words of the new query. Without this gate, two searches of one catalogue that share a long prefix and differ in one surname (`Real Chancilleria de Granada: Toledo` after `Real Chancilleria de Granada: Peiro Villanueva`) scored over 0.8, and the second search was skipped as already done.

The default threshold is 0.75 (`--threshold` to change it). Reworded, reordered or re-accented queries still match. A different collection, place, name or date range does not.

### What counts as done

`check` prints every matching row but exits 0 only when one of them has a result that [counts as done](#results) (`hit`, `none`, `partial`, `out-of-coverage`) and an empty `next_date` or one after today. A `blocked`, `denied` or `unproven` row means the search never ran or never proved anything, and a row whose `next_date` has arrived asked to be searched again, so with only those rows `check` lists them and exits 1. `--any-result` gives the older answer: exit 0 on any matching row.

A `--query` or `--person` that has nothing left after normalising (only punctuation or symbols, say `"!!"` or `"«»"`) is an error, exit 2, on every command. It would otherwise match every row: an empty query scores 1.00 against anything, and an empty person key drops the person filter.

### Retracting a row

A row logged by mistake (the wrong person, a hit that turned out to be someone else, a none from a broken search) would otherwise count as done for ever. `void` retracts it:

```sh
gwt-ledger void --person ana-peiro-toledo --source familysearch --query "Ana Peiro bautismo 1850-1860" \
    --reason "the 1852 record is another Ana, daughter of Luis" --agent lane-2
```

It takes every live row with that person, source and identical normalized query (the `--exact` rule), prints them, and appends one row with result `void` whose evidence names them: `voids 3f1c0a9b2e4d 8a7b6c5d4e3f; the 1852 record is another Ana...`. `check`, `due`, `stats` and `coverage` then leave out the void row and every row it names; `list` still shows both, so the history stays readable. Only those rows go: a close but different query stays done, and a row of the same search logged after the void counts again, even one identical to a voided row. With nothing live to retract, `void` exits 2 and writes nothing; when the search is already voided, the message names the void rows. With `--ledger-dir` it needs `--agent` like `add`. The void row takes `--label`, or else the label of the first row it retracts.

A void takes every live row of that search, in every lane file and whatever its result: a right `partial` or `none` goes with the wrong `hit`. Read the rows it prints and add again the ones that were right. Adding the rows again is also the only way to undo a void.

`list` ends each row with two more cells: its id, and `voided` on a row a void names. So a voided row and an identical row logged after the void read apart.

A row's id is the first 12 hex digits of the sha1 of its line and of how many identical lines come before it, in reading order. The ledger is append-only, so ids never move. Moving lines around is harmless, but do not edit, copy or delete a line, delete a lane file, or put a copy of a lane file in the ledger dir: a voided line that changes, or a copy of it, counts again. An edited or deleted voided line makes every command warn on stderr that a void id names no row; a copy gives no warning.

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

### Many checks at once

Each `check` reads and normalises the whole ledger before it answers. On a large ledger that is most of the time of each call, so a sweep that checks hundreds of searches pays it hundreds of times. `check --jsonl FILE` reads the ledger once and answers one check per line. `-` reads the lines from stdin.

```sh
cat > checks.jsonl <<'END'
{"person": "Ana Example", "source": "familysearch", "query": "Example Town baptisms 1801-1850 Ana Example"}
{"person": "Ana Example", "source": "familysearch", "query": "Ana Example burial 1910", "exact": true}
{"person": "Ana Example", "source": "pares"}
END
gwt-ledger check --jsonl checks.jsonl --date 2026-11-10
```

A line is one JSON object with the fields of a single check: `person`, `source` and `query`, and optionally `exact`, `any_result` (true or false) and `threshold` (a number). A field that is missing or `null` is the same as an option left out. `--exact`, `--any-result` and `--threshold` on the command line apply to every line that does not set its own. `--date` and `--new-source` apply to the whole batch. `--jsonl` takes no `--person`, `--source` or `--query` (exit 2).

It prints one JSON object per line, in input order: the line's own fields, then `done` and `rows`. `done` is true exactly when the single `check` with the same options would exit 0. `rows` are the matching rows in the order `check` prints them, each with its `id`, `date`, `result`, `agent` and `next_date`:

```json
{"person": "Ana Example", "source": "pares", "done": true, "rows": [{"id": "3f1c0a9b2e4d", "date": "2026-01-01", "result": "hit", "agent": "lane-2", "next_date": ""}]}
```

Each line goes through the same code as a single `check`, so a batch never answers differently. The batch exits 0 when it ran, even when some searches are not done: read `done`. A line the single check would refuse stops the whole batch with exit 2, and no answer is printed. That covers a line that is not a JSON object, an unknown field, a field of the wrong type, and a usage error such as `exact` with no `source`. The message names the line: `gwt-ledger: line 3: check --exact needs a non-empty --source`. Blank lines are skipped.

## Stats by source

`stats --by source` gives, per canonical source, how many rows the ledger holds and how often each answered, from your own history: where searches pay off and which sites block.

```text
source	rows	hit	partial	none	blocked	hit_rate	partial_rate	blocked_rate
familysearch	3	1	1	0	1	0.33	0.33	0.33
boe	1	0	0	1	0	0.00	0.00	0.00
```

## Coverage

`coverage` prints a person by source matrix for a tree: what was searched where, and what is still open.

```sh
gwt-check dump work/tree.json          # or a gwt-apply snapshot
printf 'familysearch\nbvph\nmatchid\n' > work/columns.txt
gwt-ledger coverage --dump work/tree.json --sources work/columns.txt --root I0001
```

```text
id	name	familysearch	bvph	matchid
I0001	Ana Zorvalo	hit	-	n/a
I0002	Pedro Zorvalo	-	blocked	n/a
I0003	Juana Toledo	-	due	-
```

A cell is `due` when its newest row's `next_date` has arrived, `hit` when any row is a hit, else the newest row's result. With no row it is `-`, or `n/a` when the source's `from`/`to` years cannot hold the person's life: from their own birth to their own death, a missing end taken as 110 years from the other, and with neither, any of their own dated events give or take 110 years. A person with no dated event is never `n/a`. A person's rows are those whose `person_key` is their Gramps id (`I0001`), starts with it and a hyphen (`i0001-ana-zorvalo`; the longest id wins, so `i0001-b-ana` belongs to `I0001-b`), or is their name as a slug when no one else in the tree has that name; namesakes never get each other's rows, so a Gramps id in the key is the exact match. When some person keys of the ledger match nobody in the dump, a note on stderr says how many, so a column of `-` is not read as "never searched". `--root` limits the rows to that person and their ancestors; the `--sources` file takes the first column of each line and skips `-*` rows, so the registry itself works as one.

## Requests to archives

A letter to a parish, a form on a records office's site or an email to an archive is not a search: it has a channel, a thread, a date to chase, a cost and someone who pays. `request` keeps them in `requests.tsv` beside the ledger (`--requests FILE` or `$GWT_REQUESTS` for another path), one row per change, the newest field winning.

```sh
gwt-ledger request add --person "Ana Zorvalo" --archive "Parish of San Pedro" --channel email \
  --thread "mail 2026-11-02" --sent 2026-11-02 --next-date 2026-11-30 --cost 10 --currency EUR --payer "Zorvalo family"
# prints its id: rq-1
gwt-ledger request update rq-1 --status answered --cost 12.50
gwt-ledger request list [--person P] [--status S]
gwt-ledger request due [--on YYYY-MM-DD]       # open requests whose next_date has come
gwt-ledger costs --by payer                    # or archive, person; one line per currency, never summed across them
```

Statuses: `planned`, `sent` (the default), `answered`, `paid`, `closed`, `refused`. A `closed` or `refused` request is never due. `update` on an id that does not exist exits 2. The file is plain TSV you may edit by hand: a cost that is not a number (`12,50`) is left out of `costs`, and a date that is not `YYYY-MM-DD` is left out of `due --ics`, each with a note on stderr.

### Calendar

`due --ics` prints an iCalendar file with one all-day event per pending date: every ledger row with a `next_date`, and every open request with one. Import it into any calendar, or subscribe to a file you regenerate. `--on` limits it to dates up to that day. A request's event keeps the request id as its UID, so importing a newer file updates the event instead of adding a second one.

```sh
gwt-ledger due --ics > research/due.ics
```

## Import another log

`import-tsv` appends the rows of a TSV log kept some other way. The file needs a header; `--map` says which of its columns fills which ledger column, and `source`, `query` and `result` are required.

```sh
gwt-ledger import-tsv old-log.tsv --agent import \
  --map person_key=who,source=where,query=what,result=outcome,date=when,control=proof
```

Sources go through the registry (a name close to a known one refuses the file unless `--new-source`), results through the synonym map. A `none` needs a control like on `add`: its own `control` column value, else `--control-ok RUN`, which vouches for every row of the file with no control of its own. Without either it becomes `unproven`, so an old log imported bare counts as not searched until you vouch for it. Every row is checked before anything is written: one bad row (an unknown result, a source close to a known one, a control that is not a passing control run, a date that is not `YYYY-MM-DD`, an empty query) lists the lines and exits 2 with the ledger untouched. A row the ledger already holds (same person, source, query, date and result) is skipped, so importing the same file twice adds nothing; a voided row does not count, so a search run again after a void imports. A `void` row in the file is refused: retract with `void`. It prints `imported`, `skipped`, `unproven`, one `synonym` line per word it mapped and one `not-in-registry` line per source it kept as a new slug.

## Traps

- **Log the search, not the result.** The query is what you typed into the source: collection or site, film or years, name ("St Mary baptisms film 004400001 Ana Peiro"). Two long write-ups of the same search do not match each other. On a real ledger, the round-30 write-up and the round-25 write-up of the same film and the same images scored under 0.5 against each other. The same search as a short query scored 1.00 against both.
- **Seeding from prose guesses the person.** A seeder that pulls names out of free text also catches place names: a town called "Villa Nueva" became a person key. Many negatives cover a whole collection and name nobody, so they have no person key. Run `check` without `--person` for collection-level searches, or you miss those rows. A negative that names two people must be filed under each of them: our first seeder used only the first name it matched, so a check for the second person found nothing.
- **A negative from a blocked period is not a negative.** Log it as `blocked`, not `none`, so the next agent repeats it: `check` lists a `blocked` row but does not count it as done. A `none` with no control run is stored as `unproven` for the same reason.
- **One site, many names.** Lanes logged the same site as `fs`, `familysearch` and a typo of it, and each `check` saw only its own spelling. The registry folds the aliases, and a name close to a known one now stops with exit 2 instead of starting a new spelling.
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
python3 .github/scripts/run_tests.py --mutants --only ledger   # each guard in ledger/mutants.txt removed in turn
```

## Image ranges: check before opening a book

A sweep reads a digitised book image by image. `check` cannot tell whether images 1 to 30 of a book were already read: a row logged for images 1 to 530 never matches a query for 1 to 300, and nothing works out which images are left. `scan` does that arithmetic over ordinary rows.

```sh
# Before opening the book: which images of 1-300 has nobody read?
gwt-ledger scan check --source familysearch --book 'book-42' --start 1 --end 300 [--person "Ana Example"]

# After reading 1-120, every image read in full, except three that would not load.
gwt-ledger scan add --source familysearch --book 'book-42' --start 1 --end 120 --skip 17,88-89 \
  --result hit --evidence notes/book-42.md --agent lane-7
```

`scan add` writes one ordinary row per run of images read, with the query `<book> img <first> <last>`, normalized like every query: `book 42 img 1 16`, `book 42 img 18 87` and `book 42 img 90 120` above. `--skip` takes the images not read (`17,88-89`), and they stay gaps. Without `--person` the row has no person: every image was read in full, so the read counts for whoever is sought, and its `person_label` is the book. With `--person` (and `--label`) the row belongs to that person, as with `add`. `--result`, `--control-ok`, `--evidence`, `--agent` and `--date` work as on `add`: a `none` without `--control-ok` is stored as `unproven` and its images count as not read, and a row the ledger already holds is skipped.

`scan check` reads the live rows of that source whose normalized query is exactly `<book> img <first> <last>`, the book compared normalized (`Book 42` and `book-42` are one book). With `--person P` it counts P's rows and the rows with no person; without it, every row of the book. A row counts when its result [counts as done](#results) and its `next_date` has not arrived, as for `check`. It prints one JSON object: `gaps`, the image runs nobody read, and `rows`, the overlapping rows with their `id`, `person_key`, `date`, `result`, `agent`, `evidence`, `start` and `end`. Exit 0 when nothing is left to read, 1 when there are gaps, 2 on a usage error.

Number the images as the source's viewer does (image, vue, canvas), and give one book the same id every time: the film, catalogue or item id, not its title. A row of any other shape is simply no scan row, so a row typed by hand can never make `scan check` fail. A row you wrote yourself in the same shape counts: an older log that already says `<book> img <first> <last>` needs no conversion.

The rows are ordinary rows, so `check`, `stats` and `void` see them, and `list --person P` shows the rows logged for P. To retract a wrong read, `void` its query, for instance `void --person '' --source familysearch --query 'book 42 img 1 16' --reason "..."`; `--person ''` takes only the rows with no person.

Concurrent ledger writing is supported only for processes running under the same OS UID as the ledger owner. Group/ACL write sharing across UIDs is not supported, even when ledger permissions allow it. Foreign-owned ledgers/sidecars are refused without replacement. Sidecars remain stable, never delete them. Preserve ledger permissions/ACLs for existing readers; deploy all writers together.
