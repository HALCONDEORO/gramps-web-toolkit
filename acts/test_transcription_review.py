import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pytest
import gwt_transcription_review as R

SHA = "a" * 64


def doc(fa=None, fb=None, **kw):
    base = {"given_names": "Rosa Maria", "surname": "Testin", "date": "9 mars 1771",
            "act_type": "bapteme", "father": "Jean Testin", "mother": "Anne Exemple"}
    d = {"image_sha256": SHA, "transcriptions": [
        {"engine": "synthetic-A", "reader": "none", "fields": {**base, **(fa or {})}},
        {"engine": "synthetic-B", "reader": "human-1", "fields": {**base, **(fb or {})}}]}
    d.update(kw)
    return d


def test_full_agreement_still_not_verified():
    p = R.build_packet(doc())
    assert p["auto_verified"] is False
    assert p["status"] == "needs-writer-review"
    assert set(r["status"] for r in p["fields"].values()) == {"match"}


def test_auto_verify_refuses():
    with pytest.raises(PermissionError):
        R.auto_verify(R.build_packet(doc()))


def test_date_not_normalised():
    p = R.build_packet(doc({"date": "9 mars 1771"}, {"date": "09/03/1771"}))
    assert p["fields"]["date"]["status"] == "disagree"


def test_case_and_accent_disagree():
    p = R.build_packet(doc({"surname": "Testin"}, {"surname": "testin"}))
    assert p["fields"]["surname"]["status"] == "disagree"
    p = R.build_packet(doc({"mother": "Anne Éxemple"}, {"mother": "Anne Exemple"}))
    assert p["fields"]["mother"]["differences"][0]["kind"] == "mismatch"


def test_missing_token():
    p = R.build_packet(doc({"given_names": "Rosa Maria"}, {"given_names": "Rosa"}))
    d = p["fields"]["given_names"]["differences"]
    assert d[0]["kind"] == "missing_in_b" and d[0]["a"]["tokens"] == ["Maria"]


def test_empty_field_is_missing():
    p = R.build_packet(doc({"father": ""}, {}))
    assert p["fields"]["father"]["status"] == "missing"


def test_shared_illegible_marker_not_a_match():
    p = R.build_packet(doc({"surname": "[illegible]"}, {"surname": "[illegible]"}))
    assert p["fields"]["surname"]["status"] == "uncertain"
    p = R.build_packet(doc({"surname": "Tes?in"}, {"surname": "Tes?in"}))
    assert p["fields"]["surname"]["status"] == "uncertain"


def test_uncertain_tokens_listed():
    p = R.build_packet(doc({"father": "Jean [illegible]"}, {"father": "Jean Testin"}))
    f = p["fields"]["father"]
    assert f["status"] == "disagree" and f["uncertain_tokens"]["a"] == ["[illegible]"]


def test_same_engine_and_identical_warn():
    d = doc()
    d["transcriptions"][1].update(engine="synthetic-A", reader="none")
    w = R.build_packet(d)["warnings"]
    assert any("not independent" in x for x in w) and any("identical" in x for x in w)


@pytest.mark.parametrize("mutate", [
    lambda d: d.update(image_sha256="XYZ"),
    lambda d: d.pop("image_sha256"),
    lambda d: d["transcriptions"].pop(),
    lambda d: d["transcriptions"][0].pop("engine"),
    lambda d: d["transcriptions"][1].update(reader=" "),
    lambda d: d["transcriptions"][0]["fields"].update(place="x"),
    lambda d: d["transcriptions"][0].update(fields="text"),
])
def test_invalid_input_rejected(mutate):
    d = doc()
    mutate(d)
    with pytest.raises(R.ReviewError):
        R.build_packet(d)


def test_unicode_decomposed_equals_precomposed():
    p = R.build_packet(doc({"mother": "Anne Éxemple"}, {"mother": "Anne Éxemple"}))
    assert p["fields"]["mother"]["status"] == "match"
    assert p["fields"]["mother"]["differences"] == []


def test_unicode_decomposed_accent_vs_plain_still_disagrees():
    p = R.build_packet(doc({"mother": "Anne Éxemple"}, {"mother": "Anne Exemple"}))
    assert p["fields"]["mother"]["status"] == "disagree"


def test_unicode_case_still_disagrees_after_nfc():
    p = R.build_packet(doc({"surname": "Émile"}, {"surname": "émile"}))
    assert p["fields"]["surname"]["status"] == "disagree"


@pytest.mark.parametrize("mutate", [
    lambda d: d["transcriptions"][0].update(reader=123),
    lambda d: d["transcriptions"][0].update(reader=["human-1"]),
    lambda d: d["transcriptions"][1].update(engine={"a": 1}),
    lambda d: d["transcriptions"][1].update(engine=None),
    lambda d: d["transcriptions"][0].update(version=1.5),
    lambda d: d["transcriptions"][0]["fields"].update(surname=42),
    lambda d: d["transcriptions"][0]["fields"].update(surname=["Testin"]),
    lambda d: d["transcriptions"][1]["fields"].update(date={"y": 1771}),
    lambda d: d["transcriptions"][1]["fields"].update(date=0),
    lambda d: d.update(image_sha256=123),
    lambda d: d.update(image_sha256=SHA + "\n"),
    lambda d: d.update(image_sha256=SHA.upper()),
    lambda d: d.update(crop=["x"]),
    lambda d: d.update(transcriptions="ab"),
    lambda d: d["transcriptions"].__setitem__(0, "text"),
    lambda d: d["transcriptions"][0].update(reader="​"),
])
def test_malformed_types_rejected(mutate):
    d = doc()
    mutate(d)
    with pytest.raises(R.ReviewError):
        R.build_packet(d)


def test_non_object_top_level_rejected():
    for bad in ([], "x", 5, None):
        with pytest.raises(R.ReviewError):
            R.build_packet(bad)


def test_none_field_value_is_missing_not_error():
    p = R.build_packet(doc({"father": None}, {}))
    assert p["fields"]["father"]["status"] == "missing"


@pytest.mark.parametrize("ra,rb", [
    ("human-1", " Human-1 "),
    ("human-1", "HUMAN-1"),
    ("Human  1", "human 1"),
    ("human-1", "human-​1"),
    ("human-1", "ｈuman-1"),
])
def test_spoofed_same_reader_warns(ra, rb):
    d = doc()
    d["transcriptions"][0]["reader"] = ra
    d["transcriptions"][1]["reader"] = rb
    w = R.build_packet(d)["warnings"]
    assert any("same reader" in x for x in w)


def test_spoofed_same_engine_and_reader_warns():
    d = doc()
    d["transcriptions"][0].update(engine="Eng-A", reader="none")
    d["transcriptions"][1].update(engine=" eng-a\t", reader="NONE")
    w = R.build_packet(d)["warnings"]
    assert any("same engine and reader" in x for x in w)


def test_distinct_readers_no_reader_warning():
    w = R.build_packet(doc({"date": "1771"}, {"date": "1772"}))["warnings"]
    assert w == []


def test_identical_warning_ignores_whitespace_layout():
    p = R.build_packet(doc({"father": "Jean  Testin"}, {"father": " Jean Testin "}))
    assert any("identical" in x for x in p["warnings"])


def test_cli_refuses_output_equal_to_input(tmp_path, capsys):
    src = tmp_path / "in.json"
    content = json.dumps(doc({"date": "1771"}, {"date": "1777"}))
    src.write_text(content)
    assert R.main([str(src), "--out", str(src)]) == 2
    assert R.main([str(src), "--out", str(tmp_path / "." / "in.json")]) == 2
    assert src.read_text() == content
    assert "--out" in capsys.readouterr().err


def test_cli_refuses_output_symlink_or_hardlink_to_input(tmp_path):
    src = tmp_path / "in.json"
    content = json.dumps(doc())
    src.write_text(content)
    link = tmp_path / "sym.json"
    link.symlink_to(src)
    hard = tmp_path / "hard.json"
    os.link(src, hard)
    assert R.main([str(src), "--out", str(link)]) == 2
    assert R.main([str(src), "--out", str(hard)]) == 2
    assert src.read_text() == content


def test_cli_unwritable_output_returns_2(tmp_path):
    src = tmp_path / "in.json"
    src.write_text(json.dumps(doc()))
    assert R.main([str(src), "--out", str(tmp_path)]) == 2


def test_excessively_long_field_rejected():
    long = "x" * (R.MAX_FIELD_CHARS + 1)
    with pytest.raises(R.ReviewError):
        R.build_packet(doc({"father": long}, {}))


def test_too_many_tokens_rejected():
    many = " ".join(["a"] * (R.MAX_FIELD_TOKENS + 1))
    with pytest.raises(R.ReviewError):
        R.build_packet(doc({}, {"father": many}))


def test_long_metadata_rejected():
    d = doc()
    d["transcriptions"][0]["reader"] = "r" * (R.MAX_META_CHARS + 1)
    with pytest.raises(R.ReviewError):
        R.build_packet(d)


def test_field_at_limit_accepted():
    many = " ".join(["a"] * R.MAX_FIELD_TOKENS)
    other = " ".join(["a"] * (R.MAX_FIELD_TOKENS - 1) + ["b"])
    assert len(many.split()) == len(other.split()) == R.MAX_FIELD_TOKENS
    p = R.build_packet(doc({"father": many}, {"father": other}))
    assert p["fields"]["father"]["status"] == "disagree"


def test_field_one_over_token_limit_rejected_on_either_side():
    ok = " ".join(["a"] * R.MAX_FIELD_TOKENS)
    over = " ".join(["a"] * (R.MAX_FIELD_TOKENS + 1))
    assert len(over.split()) == 301
    with pytest.raises(R.ReviewError):
        R.build_packet(doc({"father": over}, {"father": ok}))
    with pytest.raises(R.ReviewError):
        R.build_packet(doc({"father": ok}, {"father": over}))


def test_cli_oversized_and_deeply_nested_input_returns_2(tmp_path):
    big = tmp_path / "big.json"
    big.write_text(" " * (R.MAX_INPUT_CHARS + 1))
    assert R.main([str(big)]) == 2
    deep = tmp_path / "deep.json"
    deep.write_text("[" * 100000)
    assert R.main([str(deep)]) == 2


def test_insertion_alignment_in_b():
    p = R.build_packet(doc({"given_names": "Rosa Maria"}, {"given_names": "Rosa Anne Maria"}))
    d = p["fields"]["given_names"]["differences"]
    assert len(d) == 1
    assert d[0]["kind"] == "missing_in_a"
    assert d[0]["a"] == {"tokens": [], "range": [1, 1]}
    assert d[0]["b"] == {"tokens": ["Anne"], "range": [1, 2]}


def test_insertion_alignment_in_a_keeps_later_tokens_aligned():
    p = R.build_packet(doc({"father": "Jean Pierre Testin"}, {"father": "Jean Testin"}))
    d = p["fields"]["father"]["differences"]
    assert len(d) == 1 and d[0]["kind"] == "missing_in_b"
    assert d[0]["a"] == {"tokens": ["Pierre"], "range": [1, 2]}
    assert d[0]["b"] == {"tokens": [], "range": [1, 1]}


def test_insertion_plus_mismatch_ranges_are_independent():
    p = R.build_packet(doc({"father": "Jean Testin Sr"}, {"father": "Jean Pierre Testan Sr"}))
    d = p["fields"]["father"]["differences"]
    assert [x["kind"] for x in d] == ["mismatch"]
    assert d[0]["a"]["range"] == [1, 2] and d[0]["b"]["range"] == [1, 3]


def test_insertion_and_separate_mismatch_two_differences():
    p = R.build_packet(doc({"father": "Jean Testin Sr"}, {"father": "Jean Pierre Testin Jr"}))
    d = p["fields"]["father"]["differences"]
    assert [x["kind"] for x in d] == ["missing_in_a", "mismatch"]
    assert d[0]["b"]["range"] == [1, 2]
    assert d[1]["a"]["range"] == [2, 3] and d[1]["b"]["range"] == [3, 4]


def test_cli_writes_packet_and_text(tmp_path, capsys):
    src = tmp_path / "in.json"
    src.write_text(json.dumps(doc({"date": "1771"}, {"date": "1777"})))
    out = tmp_path / "out.json"
    assert R.main([str(src), "--out", str(out)]) == 0
    assert json.loads(out.read_text())["auto_verified"] is False
    assert R.main([str(src), "--text"]) == 0
    assert "[DISAGREE] date" in capsys.readouterr().out


def test_text_and_metadata_exactly_at_char_limit_accepted():
    d = doc({"father": "x" * R.MAX_FIELD_CHARS}, {})
    d["transcriptions"][0]["reader"] = "r" * R.MAX_META_CHARS
    p = R.build_packet(d)
    assert p["fields"]["father"]["status"] == "disagree"


def test_chars_one_over_limit_rejected():
    with pytest.raises(R.ReviewError):
        R.build_packet(doc({}, {"father": "x" * (R.MAX_FIELD_CHARS + 1)}))


def test_input_file_exactly_at_size_limit_accepted(tmp_path):
    src = tmp_path / "exact.json"
    body = json.dumps(doc())
    src.write_text(body + " " * (R.MAX_INPUT_CHARS - len(body)))
    assert len(src.read_text()) == R.MAX_INPUT_CHARS
    assert R.main([str(src), "--out", str(tmp_path / "o.json")]) == 0


def test_hash_trailing_newline_and_short_rejected():
    for bad in (SHA + "\n", SHA[:-1], SHA + "a", "g" * 64):
        with pytest.raises(R.ReviewError):
            R.build_packet(doc(image_sha256=bad))


def test_mutant_rows_exact_and_unique():
    here = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(here, "mutants-transcription.txt"), encoding="utf-8") as fh:
        rows = [ln.rstrip("\n").split(" | ") for ln in fh if ln.strip()]
    with open(os.path.join(here, "gwt_transcription_review.py"), encoding="utf-8") as fh:
        src = fh.read()
    assert len(rows) >= 8
    assert len({r[1] for r in rows}) == len(rows)
    for r in rows:
        assert len(r) == 4 and r[0] == "gwt_transcription_review.py"
        assert r[1] != r[2] and src.count(r[1]) == 1, r[1]


def test_render_text_includes_all_writer_critical_fields(tmp_path):
    d = doc({"father": "Jean [unclear]"}, {"father": "Jean Testin"}, crop="top-left")
    d["transcriptions"][1].update(reader="unknown", version="v9")
    txt = R.render_text(R.build_packet(d))
    for needle in ("auto_verified: False", "top-left", "synthetic-A", "synthetic-B", "v9",
                   "Warnings:", "reader is not identified", "Notes:", "Normalisation disclosure",
                   "uncertain in A"):
        assert needle in txt, needle
    assert "Status counts:" in txt


def test_render_text_discloses_nfc_recomposition():
    p = R.build_packet(doc({"mother": "Anne Éxemple"}, {"mother": "Anne Éxemple"}))
    assert p["fields"]["mother"]["a"] == "Anne Éxemple"  # original preserved
    assert p["fields"]["mother"]["nfc_recomposed"] == {"a": True, "b": False}
    assert "NFC recomposed" in R.render_text(p)
    assert any("Normalisation disclosure" in n for n in p["notes"])


@pytest.mark.parametrize("tok", ["[unclear]", "illegible,", "(illegible).", "Te？stin",
                                 "？", "unclear;", "[Gap]", "Jean...", "…"])
def test_uncertain_variants(tok):
    assert R.is_uncertain(tok)


def test_certain_word_not_uncertain():
    assert not R.is_uncertain("Testin")


@pytest.mark.parametrize("r", ["unknown", "N/A", "n/a", "Unknown"])
def test_unidentified_reader_not_trusted_identity(r):
    d = doc()
    d["transcriptions"][0]["reader"] = r
    d["transcriptions"][1]["reader"] = r
    w = R.build_packet(d)["warnings"]
    assert not any("share the same reader" in x for x in w)
    assert sum("not identified" in x for x in w) == 2


def test_duplicate_json_keys_rejected(tmp_path, capsys):
    src = tmp_path / "dup.json"
    src.write_text('{"image_sha256": "%s", "image_sha256": "%s", "transcriptions": []}'
                   % (SHA, SHA))
    assert R.main([str(src)]) == 2
    assert "duplicate" in capsys.readouterr().err
    nested = tmp_path / "dup2.json"
    d = json.dumps(doc())
    nested.write_text(d.replace('"surname": "Testin"', '"surname": "a", "surname": "b"', 1))
    assert R.main([str(nested)]) == 2


def test_surrogate_output_does_not_truncate_existing_file(tmp_path, capsys):
    src = tmp_path / "in.json"
    src.write_text(json.dumps(doc({"father": "J" + chr(0xD800)}, {})))
    out = tmp_path / "out.json"
    out.write_text("precious")
    assert R.main([str(src), "--out", str(out), "--overwrite"]) == 2
    assert out.read_text() == "precious"
    assert [p.name for p in tmp_path.iterdir() if p.name.startswith(".review-")] == []


def test_existing_output_protected_without_overwrite(tmp_path):
    src = tmp_path / "in.json"
    src.write_text(json.dumps(doc()))
    out = tmp_path / "out.json"
    out.write_text("precious")
    assert R.main([str(src), "--out", str(out)]) == 2
    assert out.read_text() == "precious"
    assert R.main([str(src), "--out", str(out), "--overwrite"]) == 0
    assert json.loads(out.read_text())["auto_verified"] is False
    assert [p.name for p in tmp_path.iterdir() if p.name.startswith(".review-")] == []


def test_stdout_surrogate_encoding_error_returns_2(tmp_path, capsys):
    src = tmp_path / "in.json"
    src.write_text(json.dumps(doc({"father": "J" + chr(0xD800)}, {})))
    assert R.main([str(src)]) == 2
    assert R.main([str(src), "--text"]) == 2
    cap = capsys.readouterr()
    assert cap.out == "" and "error" in cap.err


def test_cli_bad_input_returns_2(tmp_path, capsys):
    src = tmp_path / "bad.json"
    src.write_text("{not json")
    assert R.main([str(src)]) == 2
    assert R.main([str(tmp_path / "missing.json")]) == 2
