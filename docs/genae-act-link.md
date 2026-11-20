# gwt-genae-act-link

A [Genae](https://genae.org) row is an index lead, not the parish book. `read/gwt_genae_act_link.py` turns the candidates `gwt_genae` produces into a checklist for finding and reading the original act, and writes a review TSV and JSON for [gwt-acts](acts.md). It is offline: it fetches nothing, runs no OCR and writes no tree.

```sh
python3 read/gwt_genae_act_link.py candidates.json --evidence evidence.json --tsv review.tsv --json review.json
```

`candidates.json` is a list of candidates (or `{"candidates": [...]}`). `evidence.json` is optional and maps a candidate id to what a person found: `parish`, `book`, `page`, `cote`, `ark`, `image_url`, `readings`. Without `--tsv` the TSV goes to standard output. Exit 0 on success, 2 when a file cannot be read.

## Stages

| Stage | Meaning |
|---|---|
| `indexed-lead` | Only the index row, or evidence that does not locate an image. |
| `image-located` | A valid `image_url` or `ark`, **and** a `cote`. The image has not been read. |
| `readings-agree` | Two complete readings by two different readers, on two different valid crops, agreeing on `given`, `surname` and `date_iso` (case-insensitive). Still unreviewed. |

The bridge **never** produces `verificado-zoom` / `zoom-verified`: two strings and some URLs are not a human decision. Every record has `state` `por-confirmar-acta`, whatever the input (a `state` or `stage` in a candidate is ignored; in an evidence entry it is an unknown key and rejects the run). Only a human writer, outside this tool, may promote an act after reviewing it. `zoom` lists the crops of agreeing readings as a pointer for that review.

A reading is rejected (reason in `problems`) when: the reader is empty or not text; the crop is missing or invalid (whitespace, backslash, absolute or `..` path, non-https URL); `given` or `surname` is empty or incomplete (initials, one letter, `?`, `...`, `…`, brackets, digits); or `date_iso` is not a real `YYYY-MM-DD` date. A single reading, a repeated reader or crop, or a disagreement never counts as agreement. Readings given without a located image are ignored and say so. The stage is a checklist, not an identification.

Every reading and reader given is preserved in the output (`readings`, a copy of each reading as given, and `readers`), valid or not. Every reading must be complete and valid for `readings-agree`: a third reading that disagrees, or is incomplete, blocks it.

## Input checks

- Candidate ids must be non-empty text and unique (after trimming); otherwise the run is rejected (exit 2) and nothing is written. A candidate, the evidence file, or an evidence entry that is not an object is rejected the same way.
- A field that is a bool, number or other non-text value is treated as absent and listed in `problems`.
- An `image_url` equal to the index URL (ignoring fragment, trailing slash, host case and the default `:443` port) is never an image: the record stays `indexed-lead`.
- An `image_url` with an `ark:/` path must carry exactly the `ark` field's identifier (exact comparison); a substring or longer/shorter identifier is flagged.
- Any invalid or conflicting image locator (bad `image_url`, bad `ark`, differing ARK, candidate and evidence disagreeing on `image_url` or `ark`) keeps the record `indexed-lead`, even if the other locator is valid.
- Evidence entries accept only `locality`, `parish`, `book`, `page`, `cote`, `ark`, `image_url`, `date_iso`, `filiation`, `readings`; any other key rejects the run (exit 2). A JSON object that repeats a key rejects the run.
- A `date_iso` or `filiation` that differs between candidate, evidence and readings is an explicit `conflict: ...` entry in `blockers` and `problems`, and the record stays `indexed-lead` (readings are still preserved, not counted).
- An output path that is an existing directory is rejected. If moving the outputs into place fails midway, outputs already moved are restored from a snapshot and the error says so; if the restore fails, the error says which files may hold new content.
- An output path (`--tsv`, `--json`) that is the same file as an input (also through symlinks) or as the other output is rejected before anything is read or written. Both outputs are written to temporary files first, then moved into place.

## No guessing

- The candidate's `url` is copied verbatim, even when invalid; `problems` says why (not https, credentials, whitespace, host other than `genae.org`). A bare search URL is flagged as not a permalink of the row.
- An `ark` must look like `ark:/NAAN/name`. A URL in that field is invalid. An `image_url` that holds a different ARK than the `ark` field is flagged.
- No path, ARK or image URL is built from another field. Empty fields are listed in `missing`. If candidate and evidence disagree on a field, the candidate's value is kept and the difference is reported.
- Validation is syntactic only. Nothing was checked against a live site.

## Output

TSV columns: `link_id`, `candidate_id`, `stage`, `index_url`, `name_in_index`, `date_in_index`, `locality`, `parish`, `book`, `page`, `cote`, `ark`, `image_url`, `date_iso`, `filiation`, `state`, `zoom`, `readers`, `readings`, `missing`, `blockers`, `problems`, `next_steps`. Cells have no tab or newline and a leading `=`, `+`, `-` or `@` is neutralised. The JSON holds the same records with lists kept as lists.

`date_iso` and `filiation` are copied only from the candidate or the evidence (`date_iso`, `filiation` keys), exactly as provided, or left empty. They are never taken from the index date or from readings, and never guessed. When empty (or `date_iso` is not a valid date), `blockers` says so; `blockers` always also states that human writer review is pending. `gwt-acts` will reject a card until a person adds them from the act.
