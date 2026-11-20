# Install doctor

`doctor/gwt_install_doctor.py` is an offline, read-only diagnostic of how this
toolkit is packaged. It answers: will each `[project.scripts]` console script
resolve to a real module and function, and is the packaging config consistent?

```
python3 doctor/gwt_install_doctor.py [--root DIR] [--json] [--strict]
```

Exit codes: `0` ok, `1` errors (or warnings with `--strict`), `2` no usable `pyproject.toml`.

## What it does

- Parses `pyproject.toml` with `tomllib` when available (stdlib on Python 3.11+).
  The project supports Python 3.10, which has no `tomllib`; there the doctor
  does not guess, does not install anything, and does not use a partial TOML
  parser. It reports `fatal_code: "toml-parser-unavailable"`, `status:
  "unknown"`, a message saying the result is UNKNOWN (not a packaging fault),
  and exits `2`. Re-run under Python 3.11+ for a real diagnosis.
- For each console script, resolves the module through the wheel `packages`,
  `force-include` map, and `dev-mode-dirs`, then parses the file with `ast`
  and checks that the target function is defined. Nothing is imported or run.
- Checks that wheel packages have `__init__.py`, force-include sources exist,
  dev-mode dirs exist, and the readme / license files exist.
- Warns when a script is reachable only through editable-install dirs (`dev-only`).
- Looks up optional tools and the console scripts on `PATH` with `shutil.which`.
- Rejects any configured path that is absolute or escapes the root.

## What it deliberately does not do

No network, subprocess, shell, installs, credential reads, or writes. Because
it never imports anything, it cannot know whether an optional extra (such as
`mh` → `playwright`) is installed. Those are reported as `installed: "unknown"`,
along with which scripts statically import the extra's packages.
PATH results are informational only.

## Output

Human summary by default; `--json` emits the full report: `scripts`
(status and issues per script), `packaging`, `extras`, `tools`, `summary`, `ok`.
Issue levels are `error` or `warning`. Codes: `bad-entry`, `module-unreachable`,
`module-unparsable`, `function-missing`, `dev-only`, `package-missing`,
`force-include-missing`, `dev-dir-missing`, `project-file-missing`,
`undeclared-optional`.

## Limits

Static analysis only: functions created dynamically (e.g. via `globals()`) or
star imports are not seen, and `force-include` is matched on `dest == module.py`.

## Tests

`python3 -m unittest doctor/test_install_doctor.py` uses synthetic temp directories only.

On Python 3.10 (no `tomllib`) the TOML-dependent `DoctorTests` class is skipped
(`skipUnless`), because full diagnosis is not supported there: the doctor only
emits the UNKNOWN / `toml-parser-unavailable` diagnostic. This is a test
portability fix, not a functionality change; no parser dependency or fake
parser is added. `NoParserTests` (UNKNOWN diagnostic, path-escape, missing
`pyproject.toml`, fatal exit code, requirement-name parsing) always run on all
versions. On 3.11+ every test runs.
