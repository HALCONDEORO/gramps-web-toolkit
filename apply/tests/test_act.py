import json

import pytest


@pytest.fixture(autouse=True)
def _scan(tmp_path, monkeypatch):
    (tmp_path / "a.pdf").write_bytes(b"%PDF-1.4 x")
    monkeypatch.chdir(tmp_path)

from gwt_apply.act import build, main

F = {"ruling": "R-1", "slug": "x-1900", "title": "Acte 1", "page": "Acte 1, 1900", "event": "E1", "file": "a.pdf",
     "transcript": "text", "margin_reading": "nothing", "declared": [{"page": "decl", "attach": [{"person": "I1"}]}]}


def test_build_makes_source_citations_media_and_transcript_in_order():
    ops = build(F)
    assert [o["op"] for o in ops] == ["add_source", "add_citation", "add_citation", "add_media", "add_transcription"]
    assert ops[1]["confidence"] == 4 and ops[2]["confidence"] == 3
    assert ops[3]["path"] == "a.pdf" and ops[3]["attach"] == [{"citation": "key:c-x-1900"}]


def test_missing_fields_and_half_a_transcript_are_refused():
    with pytest.raises(ValueError, match="missing"):
        build({"ruling": "R-1"})
    with pytest.raises(ValueError, match="go together"):
        build({**F, "margin_reading": ""})
    with pytest.raises(ValueError, match="slug"):
        build({**F, "slug": "Bad Slug"})


def test_cli_prints_json_and_errors_with_2(tmp_path, capsys):
    p = tmp_path / "f.json"
    p.write_text(json.dumps(F))
    assert main([str(p)]) == 0
    assert len(json.loads(capsys.readouterr().out)) == 5
    p.write_text("{}")
    assert main([str(p)]) == 2
