# Transcription review

`acts/gwt_transcription_review.py` compares **two independent transcriptions**
of one act image, entirely offline, and produces a **writer-review packet**.
It runs no OCR/HTR engine, calls no network or paid service, reads only the
JSON file you give it, and writes no tree data.

## Input (JSON)

```json
{
  "image_sha256": "<64 lowercase hex of the image or crop>",
  "crop": "optional free-text description of the crop",
  "transcriptions": [
    {"engine": "name", "version": "optional", "reader": "name or 'none'",
     "fields": {"given_names": "...", "surname": "...", "date": "...",
                "act_type": "...", "father": "...", "mother": "..."}},
    {"engine": "...", "reader": "...", "fields": {}}
  ]
}
```

Exactly two transcriptions are required; `engine`, `reader` and the hash are
mandatory and explicit. Unknown field names are rejected. The hash is recorded,
not checked against any file.

## Behaviour

- Each field is split on whitespace and compared token by token, **exactly**:
  case, accents, spelling, digits and date format all count. `9 mars 1771` vs
  `09/03/1771` is a disagreement, not a match. No name, identity or date
  normalisation is done.
- Text is compared after Unicode NFC composition only, so a decomposed `E` + U+0301
  equals a precomposed `É`; `É` vs `E` or `é` is still a disagreement.
- Input is type- and size-checked: engine, reader, version, crop and field values
  must be strings (field values may be null); fields are limited to 2000 characters
  and 300 tokens, metadata to 500 characters, the file to 1,000,000 characters.
  The hash must be fully lowercase hex (no trailing newline). A field of exactly
  300 tokens is accepted; 301 tokens is rejected (in either transcription).
- Per field status: `match`, `disagree`, `missing` (one side empty),
  `uncertain`.
- Differences are `mismatch`, `missing_in_a` or `missing_in_b`, with token
  ranges.
- Tokens such as `[illegible]`, `[unclear]`, `illegible,`, `[...]`, `???`, `…` or any
  token containing `?` (including fullwidth `？`) are listed as uncertain. Two
  outputs sharing the same marker are **not** a match: nobody read the word.
- Original text is always shown unchanged. The only normalisation is whitespace
  splitting and NFC composition; `nfc_recomposed` flags fields where NFC changed
  the text, and the packet `notes` disclose this.
- A reader of `none`, `unknown`, `n/a` and similar placeholders is never a trusted
  identity: two such readers are not reported as "the same reader", and
  `unknown`/`n/a` raises a warning that independence cannot be established.
- Duplicate JSON keys (at any level) are rejected.
- Warnings are raised (never hidden) when both sides share engine and reader, when
  both share the same reader other than `none`, or when the transcriptions are
  identical. Engine/reader identity ignores case, spacing, compatibility forms and
  invisible format characters, so `Human-1 ` and `human-1` count as the same reader.
- The packet always has `"auto_verified": false` and status
  `needs-writer-review`. `auto_verify()` raises `PermissionError`. Even full
  agreement is not verification; two outputs can share the same error.

## Usage

```
python acts/gwt_transcription_review.py input.json [--out packet.json] [--overwrite] [--text]
```

`--text` output includes crop, sources, status counts, warnings, uncertain tokens
and notes, not just the field comparison. Output is fully encoded before any file
is touched, written to a temp file and atomically published. An existing `--out`
file is refused unless `--overwrite` is given. Output that cannot be encoded
(e.g. lone surrogates) or a stdout encoding that cannot represent it exits 2
without truncating anything.

Exit code 2 on malformed input, unwritable output, or when `--out` is the same
file as the input (including via symlink or hard link); the input is never
overwritten.

## Possible future local-engine adapter (not implemented)

A later, separate adapter could run a locally installed engine (see
`docs/ocr-bench.md`) on a crop and emit the input JSON above, filling `engine`
and `version` from the tool itself. It would be optional, would need explicit
user opt-in, and would keep this comparison module free of engine imports. The
review packet and its refusal to auto-verify would not change.

## Limits

This tool makes **no claim about handwriting recognition accuracy** for any
engine or reader, and none has been measured here. It surfaces disagreement and
uncertainty for a human writer to resolve against the image. Tests use
synthetic fixtures only.

## Demonstration

`acts/demo_transcription_review.py` runs the real module on one invented
two-reading fixture (all data synthetic, offline, nothing persisted; temp files
are removed on exit):

```
python3 acts/demo_transcription_review.py
```

Expected result for the base fixture: packet status `needs-writer-review`,
`auto_verified` false, and status counts of **4 disagree, 1 match, 1 uncertain**
(`given_names`, `surname`, `date`, `father` disagree; `act_type` matches;
`mother` is uncertain because both sides carry `[illegible]`). One warning is
raised: the two readers are the same after case/space normalisation of reader
identity.

The demo also checks **5 variants** of that fixture:

1. Same engine and reader on both sides: shared engine/reader warning.
2. Both readers `unknown`: two "not identified" warnings, no "same reader" claim.
3. Reader differing only by an invisible zero-width character: same-reader warning.
4. Identical fields on both sides: identical-transcriptions warning; everything
   matches except the uncertain `mother`.
5. Same text in NFC and decomposed form: no differences, `nfc_recomposed`
   flagged on one side only.

It also exercises the CLI in a temp directory (JSON and `--text` output,
refusal to overwrite, refusal of a symlink alias of the input or a dangling
symlink as `--out`), and asserts the rejections on captured stderr. The demo
shows tool behaviour only; it makes no claim about which reading is correct and
reports no accuracy figure.
