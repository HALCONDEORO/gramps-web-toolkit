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
