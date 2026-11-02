# gwt-act-packet

Builds an offline evidence packet for act (record) readings. For each act it ties a person, date and type to
an image and a crop, records two independent readings, and writes a writer-review TSV and a JSON manifest.

It runs no OCR or HTR engine, calls no model or paid API, uses no network and never touches the tree.
Standard library only. The examples and tests use invented people and dummy image bytes.

## Usage

```bash
python3 acts/gwt_act_packet.py acts.tsv --root evidence/ --out-tsv review.tsv --out-json manifest.json
```

Test: `cd acts && python3 -m unittest test_gwt_act_packet`

Exit codes: `0` written, `1` one or more rows rejected (nothing is written), `2` bad input file, header,
output path, or a write failure.

Output safety: `--out-tsv` and `--out-json` must be different files, must not be the input file, must not
be inside `--root` (so never an evidence file), are checked and written by canonical path (symlinks followed), must not be directories, and their parent directory must exist; otherwise nothing runs
(exit `2`). Existing outputs are replaced only after both new files are fully written to unique temp files
beside them. If the second replace fails, the first output is restored to its previous content (or removed if
it did not exist) and temp files are deleted, so you get both new outputs or the previous state. Temp files are
also deleted when a failure happens before the swap (for example an unreadable existing output). If a
rollback step itself fails, the tool says `rollback incomplete; outputs may be inconsistent`, names the
files, and exits `2`; it only says `outputs left as before` when the restore succeeded. A crash
between the two replaces can still leave a mismatched pair; this is not a transaction.

## Input TSV

Tab separated, one header line, no quoting. Required columns:
`person`, `date`, `type`, `image`, `crop`, `reading_1`, `reader_1`, `reading_2`, `reader_2`.
Optional: `review` (`approved` or `rejected`) and `reviewer`.

- `person`: an identifier of letters, digits, `_` or `-`, starting with a letter (for example `I0001`).
- `date`: `YYYY`, `YYYY-MM` or `YYYY-MM-DD`, ASCII digits only, and a real calendar date (leap years
  respected; `1850-02-30`, month `13` and year `0000` are rejected).
- The file may start with a UTF-8 BOM; it is ignored. A file the TSV reader cannot parse (for example a
  field over 131072 characters) is refused with `unreadable TSV` (exit `2`).
- No field may contain ASCII or C1 control characters, Unicode line/paragraph separators (U+2028, U+2029,
  U+0085) or bidirectional controls (U+061C, U+200E, U+200F, U+202A–U+202E, U+2066–U+2069); the row is
  rejected (exit `1`, nothing written).
- `type`: `birth`, `baptism`, `marriage`, `death` or `burial`.
- `image`, `crop`: paths relative to `--root`, with an image extension. Absolute paths, `..`, URLs, backslashes
  and symlinks that leave the root are rejected. The files must exist and be non-empty.
- `reader_1` and `reader_2` must be different ids (compared case-insensitively); equal ids are rejected.
- `review`/`reviewer`: a `reviewer` without a `review` is rejected; a reviewer must differ from both readers.
  Approval metadata is only recorded.
- `crop` must be a different file from `image`, compared by canonical path (so `./`, `//` and symlink
  spellings of the same file count as the same file).
- Two rows with the same person, type, date, image and crop, by canonical path, are rejected as duplicates.

## Output

Every output is for writer review only. The manifest holds, per act, the image and crop SHA-256, the file
signature hint of each, both readings with their readers, the `agreement` state (`agree` or `conflict`,
comparing Unicode form (NFC), full Unicode casefold and whitespace only; this is not an exact match, so for
example `Straße` and `STRASSE` agree while `José` and `Jose` conflict), `uncertain`, `review`, `reviewer`, `warnings` and `status`.
The review TSV carries the same fields for the writer.

Readings are kept verbatim in the JSON. Marks such as `[?]`, `?` (including fullwidth `？`), `...`, `…`,
`unclear` and `illegible` are preserved and set `uncertain`. In the TSV, a cell starting with `=`, `+`, `-` or `@` (after optional spaces) gets a leading `'`
so spreadsheets do not run it as a formula.

`status` is always `pending_review`. There is no promoted, promotable or verified state: `review: approved`
with a named reviewer is recorded as a claim and changes nothing, and approval on a conflicting or uncertain
act adds the warning `approval_ignored_conflict_or_uncertain`. The manifest has `mode: writer_review_only`,
`auto_promoted: 0` and `auto_verified: 0`. Any decision to use a reading happens outside this tool.

Signature hints (`jpeg-signature`, `png-signature`, `tiff-signature`, `unrecognized`) only name the first bytes
of a file. Arbitrary bytes under an image extension are not rejected; they are flagged
(`image_signature_unrecognized`, `crop_signature_unrecognized`, `crop_bytes_identical_to_image`) for the writer.

## Limits

- It does not read or verify image content. A signature hint does not show that a file is a valid image, that
  a crop belongs to its image, or that the readings match it. The readings are supplied by people or other
  tools, and this tool cannot tell whether they are right, or whether two readers were truly independent
  beyond having different ids.
- The signature hint and SHA-256 of a file come from the same single read of its bytes.
- Hashes show which bytes were referenced, not that the image is a genuine record.
- Agreement is textual. Two readings that differ in spelling are a conflict, and two that agree can both be wrong.
- Any rejected row blocks the whole run, so a partial packet is never written.
- No real records have been read with it.
