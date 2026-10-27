# Load test against a disposable Gramps Web tree

`run_loadtest.sh` runs `bench.py` in an ephemeral `python:3.11-slim` container on the Docker network of a
Gramps Web stack that holds an invented tree named `LoadTest` (for example 100,000 people from a generated
GEDCOM). It installs the toolkit from `main`, then measures page latency per type, a full `gwt-check dump`,
the checks on that dump, and a 300-operation `gwt-apply` batch: dry run, apply, verify, and a second run that
must change nothing.

```sh
GWT_REF=<40-char commit> LT_PASS=... ./run_loadtest.sh <docker-network> [web-container]
```

- `bench.py` refuses to run unless the host is exactly the loadtest web container, there is exactly one tree named `LoadTest`, and the login token is bound to it. It checks again before every phase and stops at the first failing step or if the second `gwt-apply` run is not idempotent.
- `GWT_REF` pins the toolkit commit that is installed, so the run tests exactly that code.
- Memory is capped at 1 GB with no swap. A dump that does not fit ends with exit 137, which is a result.
- The password comes from the environment. Nothing is written to the repository.
- Results go to `out/result.json`.

Not tested against a live server yet; only syntax and the install from the zip were checked.

## First run (27 October 2026)

Toolkit commit `5c525bd` (current upstream `main`), invented tree of 100,000 people (31,835 families, 100,000 events; no places, citations, sources, notes or media), 1 CPU, 1 GiB RAM, no swap, nice 15, dump with 1 worker. Real Gramps was not contacted.

| Step | Seconds | Result |
| --- | ---: | --- |
| `gwt-check dump` | 400 | exit 0, 250 requests |
| `gwt-check check` on the dump | 139 | exit 1 (findings; read 4.9 s, checks 131 s) |
| `gwt-apply` dry run, 2000 ops on the snapshot | 9 | 2000 done |
| `gwt-apply` dry run, 300 ops, live reads | 20 | 300 done |
| `gwt-apply --apply`, 300 ops | 72 | 300 done |
| `gwt-apply --verify` | 29 | 300 of 300 in the tree |
| same 300 ops again | 33 | 0 done, 300 skip |

Page reads (first three pages of 1000, not the whole set): people 3.97 s, families 0.87 s, events 0.23 s per 1000 objects.

Limits, stated plainly:

- The tree has no media, citations, sources, notes or places, so it is not like a fully documented tree. Read and check times on a real one will differ.
- The 944 MiB figure is the largest child process size reported by the harness, not the container's measured peak. A container sample during the check showed 892 MiB, which is not the peak either.
- The harness could not write `result.json` (read-only output directory), so the figures above come from the console log. The harness now prints the whole result to the console as well.
- The findings of the check were not kept, and the 1-worker dump is not comparable with a 4-worker one. Apply logs had 1209 and 609 lines: log lines, not API requests.
- This run shows the dump, batch write, verify and repeat stages complete at this size. It does not claim the tools are correct, only measured.
