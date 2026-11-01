"""gwt-check act-media on invented records only (persons Alfa and Beta, registers refsR1 and refsR2)."""

import copy
import json

import pytest

from gwt_check import selftest
from gwt_check.actmedia import media_report, slug
from gwt_check.cli import main
from gwt_check.tree import BadInput, Tree


def _d():
    d = copy.deepcopy(selftest.clean_tree())
    o = d["media"][0]
    o.update(path="refsR1/alfa.jpg", desc="capture invented for Alfa")
    return d


def _get(d, t, gid):
    return next(o for o in d[t] if o["gramps_id"] == gid)


def _media(d, gid, **kw):
    d["media"].append({**copy.deepcopy(d["media"][0]), "handle": "h" + gid, "gramps_id": gid,
                       "checksum": gid * 4, "path": f"refsR2/{gid}.jpg", **kw})
    return "h" + gid


def _link(d, t, gid, handle):
    _get(d, t, gid)["media_list"].append(selftest._ref(handle, rect=[]))


def _only(d, person="I0001"):
    [r] = media_report(Tree(d), [person])["targets"]
    return r


def _bare(d):
    _get(d, "citations", "C0001")["media_list"] = []


def test_a_media_on_the_events_citation_is_a_candidate_with_exact_provenance():
    r = _only(_d())
    [m] = r["media"]["rows"]
    assert m["level"] == "event-citation" and r["media"]["status"].startswith("candidate")
    assert m["via"] == {"type": "citations", "gramps_id": "C0001", "handle": "hC0001"}
    assert m["media"]["handle"] == "hO0001" and m["media"]["path"] == "refsR1/alfa.jpg" and m["field"] == "media_list"
    assert "unverified" in r["media"]["status"] and "not read" in m["claim"]


def test_a_media_on_the_event_itself_is_its_own_level():
    d = _d()
    _bare(d)
    _link(d, "events", "E0001", _media(d, "O0002"))
    [m] = _only(d)["media"]["rows"]
    assert m["level"] == "event" and m["via"]["handle"] == "hE0001"


def test_a_media_only_on_the_person_is_not_the_act():
    d = _d()
    _bare(d)
    _link(d, "people", "I0001", _media(d, "O0002"))
    r = _only(d)
    assert [m["level"] for m in r["media"]["rows"]] == ["person"]
    assert r["media"]["status"].startswith("indirect only") and "candidate" not in r["media"]["status"]


def test_a_media_only_on_a_person_citation_is_not_the_act():
    d = _d()
    _bare(d)
    _get(d, "people", "I0001")["citation_list"] = ["hC0002"]
    _link(d, "citations", "C0002", _media(d, "O0002"))
    r = _only(d)
    assert [m["level"] for m in r["media"]["rows"]] == ["person-citation"]
    assert r["media"]["status"].startswith("indirect only")


def test_a_media_on_the_source_of_the_events_citation_is_not_the_act():
    d = _d()
    _bare(d)
    _link(d, "sources", "S0001", _media(d, "O0002"))
    r = _only(d)
    [m] = r["media"]["rows"]
    assert m["level"] == "event-source" and m["via"]["gramps_id"] == "S0001"
    assert r["media"]["status"].startswith("indirect only")


def test_a_source_media_does_not_outweigh_a_direct_candidate():
    d = _d()
    _link(d, "sources", "S0001", _media(d, "O0002"))
    r = _only(d)
    assert sorted(m["level"] for m in r["media"]["rows"]) == ["event-citation", "event-source"]
    assert r["media"]["status"].startswith("candidate")


def test_no_media_is_said_as_a_gap_of_the_dump_not_as_proof():
    d = _d()
    _bare(d)
    r = _only(d)
    assert r["media"]["rows"] == [] and "not proof" in r["media"]["status"]


def test_several_direct_media_are_ambiguous_and_none_is_chosen():
    d = _d()
    _link(d, "events", "E0001", _media(d, "O0002"))
    r = _only(d)
    assert r["media"]["status"].startswith("ambiguous") and len(r["media"]["rows"]) == 2


def test_the_same_media_on_event_and_citation_is_not_ambiguous():
    d = _d()
    _link(d, "events", "E0001", "hO0001")
    assert _only(d)["media"]["status"].startswith("candidate")


def test_a_private_media_is_held_back_and_its_path_is_not_reported():
    d = _d()
    _get(d, "media", "O0001")["private"] = True
    r = _only(d)
    assert r["media"]["rows"] == [] and r["held_back"] == [
        {"type": "media", "gramps_id": "O0001", "handle": "hO0001", "why": "private"}]
    assert "alfa.jpg" not in json.dumps(r)


def test_a_media_handle_missing_from_the_dump_is_listed_not_invented():
    d = _d()
    _get(d, "citations", "C0001")["media_list"] = [selftest._ref("hO9999", rect=[])]
    r = _only(d)
    assert r["media"]["rows"] == []
    assert r["held_back"] == [{"type": "media", "gramps_id": "", "handle": "hO9999", "why": "not in the dump"}]


def test_a_dangling_citation_is_listed_and_the_event_is_still_reported():
    d = _d()
    _get(d, "events", "E0001")["citation_list"] = ["hC9999"]
    r = _only(d)
    assert r["held_back"][0]["why"] == "not in the dump" and r["media"]["rows"] == []


def _note(d, gid, text, typ="Transcript", **kw):
    d["notes"].append(selftest._base(gid, type=typ, format=0, text={"_class": "StyledText", "string": text, "tags": []}, **kw))
    return "h" + gid


def test_a_transcript_note_on_the_citation_is_found_and_its_text_is_not_copied():
    d = _d()
    _get(d, "citations", "C0001")["note_list"].append(_note(d, "N0010", "texte litteral inventé Alfa"))
    r = _only(d)
    [n] = r["transcription"]["rows"]
    assert (n["level"], n["basis"], n["note"]["gramps_id"], n["note"]["type"]) == ("event-citation", "type-transcript", "N0010", "transcript")
    assert r["transcription"]["status"].startswith("note of type Transcript")
    assert "litteral" not in json.dumps(r) and "not checked" in r["transcription"]["status"]


def test_a_transcript_note_on_the_media_counts_at_the_media_level():
    d = _d()
    _get(d, "media", "O0001")["note_list"].append(_note(d, "N0010", "Alfa"))
    [n] = _only(d)["transcription"]["rows"]
    assert n["level"] == "event-citation" and n["via"]["type"] == "media"


def test_a_transcript_note_only_on_the_person_is_indirect():
    d = _d()
    _get(d, "people", "I0001")["note_list"] = [_note(d, "N0010", "Alfa")]
    r = _only(d)
    assert r["transcription"]["status"].startswith("indirect only")


def test_an_untyped_note_claiming_a_literal_transcription_is_unverified():
    d = _d()
    _get(d, "events", "E0001")["note_list"].append(_note(d, "N0010", "Transcripción literal: Alfa nació", typ="General"))
    r = _only(d)
    assert [n["basis"] for n in r["transcription"]["rows"]] == ["text-marker"]
    assert r["transcription"]["status"].startswith("unverified")


def test_an_ordinary_note_and_an_empty_transcript_are_not_a_transcription():
    d = _d()
    _get(d, "events", "E0001")["note_list"] += [_note(d, "N0010", "comentario", typ="General"), _note(d, "N0011", "  ")]
    r = _only(d)
    assert [n["basis"] for n in r["transcription"]["rows"]] == ["type-transcript-empty"]
    assert r["transcription"]["status"] == "only an empty Transcript note"


def test_a_private_transcript_note_is_held_back():
    d = _d()
    _get(d, "events", "E0001")["note_list"].append(_note(d, "N0010", "Alfa", private=True))
    r = _only(d)
    assert r["transcription"]["rows"] == [] and "not proof" in r["transcription"]["status"]
    assert r["held_back"][0]["gramps_id"] == "N0010"


def test_no_transcription_is_a_gap_of_the_dump():
    assert "not proof" in _only(_d())["transcription"]["status"]


def test_the_stem_keeps_unknown_parts_unknown():
    f = _only(_d())["filename"]
    assert f["stem"] == "lucia-ortega-sanz_birth_1900-03-01_unknown_unknown"
    assert f["unknown"] == ["ref", "vue"] and f["sources"]["cote"]["status"] == "not found"


def test_cote_and_vue_are_read_from_the_events_citation_with_provenance():
    d = _d()
    _get(d, "citations", "C0001")["page"] = "cote 4E123, vue 12"
    f = _only(d)["filename"]
    assert f["stem"] == "lucia-ortega-sanz_birth_1900-03-01_cote-4e123_vue12" and f["unknown"] == []
    assert f["sources"]["cote"]["from"][0] == {"type": "citations", "gramps_id": "C0001", "handle": "hC0001", "field": "page"}


def test_a_call_number_of_the_source_repository_is_a_cote():
    d = _d()
    _get(d, "sources", "S0001")["reporef_list"][0]["call_number"] = "2E 99/3"
    assert "cote-2e-99-3" in _only(d)["filename"]["stem"]


def test_an_ark_is_used_only_when_there_is_no_cote_and_cote_wins_otherwise():
    d = _d()
    _get(d, "citations", "C0001")["page"] = "ark:/12345/abcd1234 vue 7"
    assert _only(d)["filename"]["stem"].endswith("_ark-12345-abcd1234_vue7")
    _get(d, "citations", "C0001")["page"] = "ark:/12345/abcd1234 cote 4E1 vue 7"
    assert "_cote-4e1_vue7" in _only(d)["filename"]["stem"]


def test_two_different_views_in_the_citations_are_a_conflict_and_stay_unknown():
    d = _d()
    _get(d, "citations", "C0001")["page"] = "vue 12"
    _get(d, "events", "E0001")["citation_list"].append("hC0002")
    _get(d, "citations", "C0002")["page"] = "vue 13"
    f = _only(d)["filename"]
    assert f["parts"]["vue"] == "unknown" and f["sources"]["vue"] == {"value": "unknown", "status": "conflict", "candidates": ["12", "13"]}


def test_cote_and_view_cited_only_on_the_person_or_source_media_are_not_used():
    d = _d()
    _get(d, "citations", "C0001")["page"] = ""
    _get(d, "people", "I0001")["citation_list"] = ["hC0002"]
    _get(d, "citations", "C0002")["page"] = "cote 9X9 vue 3"
    assert _only(d)["filename"]["unknown"] == ["ref", "vue"]


def test_a_conflicting_cote_is_not_replaced_by_an_ark():
    d = _d()
    _get(d, "citations", "C0001")["page"] = "cote A1 cote B2 ark:/12345/abcd1234"
    f = _only(d)["filename"]
    assert f["parts"]["ref"] == "unknown" and f["sources"]["cote"]["status"] == "conflict"


@pytest.mark.parametrize("date,expected", [
    ({"dateval": [0, 3, 1900, False]}, "1900-03"), ({"dateval": [0, 0, 1900, False]}, "1900"),
    ({"dateval": [31, 2, 1900, False]}, "unknown"), ({"dateval": [0, 0, 0, False]}, "unknown"),
    ({"dateval": [1, 3, 1900, False], "modifier": 2}, "unknown"), ({"dateval": [1, 3, 1900, False], "quality": 1}, "unknown"),
    ({"dateval": [1, 3, 1900, False], "calendar": 3}, "unknown"), ({"dateval": [5, 0, 1900, False]}, "unknown"),
    ({"dateval": [1, 3, 1900, False, 2, 3, 1901, False], "modifier": 0}, "unknown"), (None, "unknown")])
def test_only_a_plain_date_is_written_into_the_stem(date, expected):
    d = _d()
    e = _get(d, "events", "E0001")
    e["date"] = None if date is None else {**e["date"], "modifier": 0, "quality": 0, "calendar": 0, **date}
    assert _only(d)["filename"]["parts"]["date"] == expected


def test_a_name_and_a_cote_with_unsafe_characters_become_a_cross_platform_stem():
    d = _d()
    p = _get(d, "people", "I0001")
    p["primary_name"]["first_name"] = 'CON/Ñandú:*?"'
    p["primary_name"]["surname_list"][0]["surname"] = "<Ortega>|.. "
    _get(d, "citations", "C0001")["page"] = "cote a/b.c vue 1"
    stem = _only(d)["filename"]["stem"]
    assert stem == "con-nandu-ortega_birth_1900-03-01_cote-a-b-c_vue1"
    assert not set(stem) & set('<>:"/\\|?* .') and stem == stem.lower()


def test_a_long_name_is_capped_and_never_ends_in_a_dash():
    assert len(slug("a" * 100, 30)) == 30 and slug("ab " + "c" * 40, 3) == "ab"
    assert slug("***", 30) == "" and slug("Ñ", 30) == "n"


def test_a_person_without_a_name_has_an_unknown_person_part():
    d = _d()
    p = _get(d, "people", "I0001")
    p["primary_name"].update(first_name="", surname_list=[])
    assert _only(d)["filename"]["parts"]["person"] == "unknown"


def test_the_report_changes_nothing(tmp_path):
    d = _d()
    t = Tree(d)
    before = copy.deepcopy(t.objs)
    media_report(t, ["I0001", "I0002"])
    assert t.objs == before and list(tmp_path.iterdir()) == []


def test_only_own_events_are_read_and_the_event_option_picks_them():
    d = _d()
    _get(d, "people", "I0001")["event_ref_list"].append(selftest._ref("hE0002", role="Godparent"))
    rows = media_report(Tree(d), ["I0001"])["targets"]
    assert [r["event"]["gramps_id"] for r in rows] == ["E0001"]
    rows = media_report(Tree(d), ["I0002"], ["E0002"])["targets"]
    assert [r["event"]["gramps_id"] for r in rows] == ["E0002"]


def test_an_event_that_is_not_an_own_event_of_the_named_persons_is_refused():
    with pytest.raises(BadInput):
        media_report(Tree(_d()), ["I0001"], ["E0002"])


def test_an_unknown_person_or_no_person_or_a_backup_is_refused():
    with pytest.raises(BadInput):
        media_report(Tree(_d()), ["I9999"])
    with pytest.raises(BadInput):
        media_report(Tree(_d()), [])
    t = Tree(_d())
    t.partial = True
    with pytest.raises(BadInput):
        media_report(t, ["I0001"])


def test_a_private_person_or_event_shows_only_ids():
    d = _d()
    _get(d, "people", "I0002")["private"] = True
    _get(d, "events", "E0001")["private"] = True
    rows = media_report(Tree(d), ["I0001", "I0002"])["targets"]
    assert rows == [{"person": {"type": "people", "gramps_id": "I0001", "handle": "hI0001"},
                     "event": {"type": "events", "gramps_id": "E0001", "handle": "hE0001"}, "private": True},
                    {"person": {"type": "people", "gramps_id": "I0002", "handle": "hI0002"}, "private": True}]


def test_a_person_with_no_own_event_gets_a_row_saying_so():
    d = _d()
    _get(d, "people", "I0001")["event_ref_list"] = []
    [r] = media_report(Tree(d), ["I0001"])["targets"]
    assert r["event"] is None and "no own" in r["note"]


def _dump(tmp_path, d):
    f = tmp_path / "t.json"
    f.write_text(json.dumps({"tree": "x", "dumped_at": "", "objects": d}))
    return str(f)


def test_the_command_prints_the_three_answers_apart_and_exits_0(tmp_path, capsys):
    assert main(["act-media", _dump(tmp_path, _d()), "--person", "I0001"]) == 0
    out = capsys.readouterr().out
    assert "media: candidate" in out and "transcription:" in out and "filename proposal: lucia-ortega-sanz_birth" in out
    assert "(unknown: ref, vue)" in out and "nothing was renamed" in out


def test_the_command_json_carries_exact_references(tmp_path, capsys):
    assert main(["act-media", _dump(tmp_path, _d()), "--person", "I0001", "--event", "E0001", "--json"]) == 0
    rep = json.loads(capsys.readouterr().out)
    assert rep["read_only"] is True
    assert rep["targets"][0]["media"]["rows"][0]["media"]["handle"] == "hO0001"


def test_the_command_limit_caps_the_printed_rows(tmp_path, capsys):
    assert main(["act-media", _dump(tmp_path, _d()), "--person", "I0001", "--person", "I0002", "--limit", "1"]) == 0
    out = capsys.readouterr().out
    assert "first 1 of 2 rows" in out and "I0002" not in out


def test_the_command_reports_bad_input_with_exit_2(tmp_path, capsys):
    assert main(["act-media", _dump(tmp_path, _d()), "--person", "I9999"]) == 2
    assert "no such person" in capsys.readouterr().err
    assert main(["act-media", str(tmp_path / "missing.json"), "--person", "I0001"]) == 2


def test_the_command_needs_a_dump_and_a_person_and_has_no_write_option(tmp_path):
    for argv in (["act-media", "--person", "I0001"], ["act-media", _dump(tmp_path, _d())],
                 ["act-media", _dump(tmp_path, _d()), "--person", "I0001", "--ops", "x"],
                 ["act-media", _dump(tmp_path, _d()), "--person", "I0001", "--rename"]):
        with pytest.raises(SystemExit) as exc:
            main(argv)
        assert exc.value.code == 2

@pytest.mark.parametrize('holder,gid', [('events', 'E0001'), ('citations', 'C0001'),
                                       ('people', 'I0001'), ('sources', 'S0001')])
def test_private_media_subreference_is_not_followed(holder, gid):
    d = _d()
    _bare(d)
    _link(d, holder, gid, 'hO0001')
    _get(d, holder, gid)['media_list'][-1]['private'] = True
    r = _only(d)
    assert not r['media']['rows']
    assert any(x['why'] == 'private media reference' for x in r['held_back'])
    assert 'refsR1/alfa.jpg' not in json.dumps(r)
    assert 'capture invented for Alfa' not in json.dumps(r)


def test_private_event_reference_withholds_event_body():
    d = _d()
    _get(d, 'people', 'I0001')['event_ref_list'][0]['private'] = True
    r = _only(d)
    assert r.get('private') is True
    assert 'media' not in r and 'filename' not in r


@pytest.mark.parametrize('where', ['primary_name', 'surname'])
def test_private_name_component_withholds_whole_filename_person(where):
    d = _d()
    n = _get(d, 'people', 'I0001')['primary_name']
    if where == 'primary_name':
        n['private'] = True
    else:
        n['surname_list'][0]['private'] = True
    r = _only(d)
    assert r['filename']['parts']['person'] == 'unknown'


@pytest.mark.parametrize('date', [
    {'dateval': [1, 3, 1700, True]},
    {'dateval': [1, 3, 1700, False], 'newyear': 1},
])
def test_dual_year_and_newyear_filename_dates_stay_unknown(date):
    d = _d()
    _get(d, 'events', 'E0001')['date'] = date
    assert _only(d)['filename']['parts']['date'] == 'unknown'
