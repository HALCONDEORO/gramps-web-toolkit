"""Direct tests of `media.media_checks` on small synthetic trees: a status that is not a dict, int/float guards on
the measures, malformed dHash values, and every NON_VIEWABLE MIME type. No server, no Pillow."""

import pytest

from gwt_check import media

GOOD = "0" * 64  # a well-formed dHash, int 0


class Out:
    def __init__(self):
        self.rows = []

    def add(self, kind, key, detail):
        self.rows.append((kind, key, detail))

    def found(self):
        return {(k, key) for k, key, _ in self.rows}


def _run(objs, status, citations=None, scope=None):
    """media_checks over media objects `objs` ({handle: fields}); returns (Out, info)."""
    M = {h: {"handle": h, "gramps_id": h.upper(), **f} for h, f in objs.items()}
    if citations is None:
        citations = {"c1": {"handle": "c1", "gramps_id": "C1",
                            "media_list": [{"ref": h} for h in M]}}
    T = {"media": M, "citations": citations}
    out, info = Out(), {}
    in_scope = (lambda kind, h: True) if scope is None else (lambda kind, h: (kind, h) in scope)
    media.media_checks(T, status, out, in_scope, info, lambda kind, h: M[h]["gramps_id"], lambda s: s)
    return out, info


def _img(**kw):
    return {"mime": "image/jpeg", "path": "a.jpg", **kw}


# -- status that is not a dict ---------------------------------------------------------------------

@pytest.mark.parametrize("bad", ["garbage", ["file_missing"], 7, None, True])
def test_a_status_that_is_not_a_dict_is_unchecked_not_a_crash(bad):
    out, info = _run({"h1": _img()}, {"h1": bad})
    assert out.rows == []
    assert info == {"media files not checked": 1}


def test_a_handle_absent_from_status_is_unchecked_and_good_ones_still_report():
    out, info = _run({"h1": _img(), "h2": _img(), "h3": _img()},
                     {"h1": "x", "h2": {"file_missing": True}})
    assert out.found() == {("media-file-missing", "H2")}
    assert info == {"media files not checked": 2}


# -- int / float guards on the measures --------------------------------------------------------------

@pytest.mark.parametrize("w,h", [("40", 100), (40, "100"), (40.0, 40.0), (None, 40), (40, None), ([40], [40])])
def test_a_size_that_is_not_an_int_is_never_tiny(w, h):
    out, _ = _run({"h1": _img()}, {"h1": {"file_missing": False, "w": w, "h": h}})
    assert out.rows == []


def test_an_int_size_under_min_side_is_tiny_and_says_the_size():
    out, _ = _run({"h1": _img()}, {"h1": {"file_missing": False, "w": 40, "h": 30}})
    assert [(k, d.split(":")[0]) for k, _, d in out.rows] == [("media-tiny", "40x30 px")]


@pytest.mark.parametrize("tone", ["1", "0.99", None, [1.0], {"x": 1}])
def test_a_tone_that_is_not_a_number_is_never_uniform(tone):
    out, _ = _run({"h1": _img()}, {"h1": {"file_missing": False, "tone": tone}})
    assert out.rows == []


@pytest.mark.parametrize("tone", [1, 0.99, 0.98])
def test_an_int_or_float_tone_at_or_over_the_share_is_uniform(tone):
    out, _ = _run({"h1": _img()}, {"h1": {"file_missing": False, "tone": tone}})
    assert out.found() == {("media-uniform", "H1")}


# -- malformed dHash ---------------------------------------------------------------------------------

def _pair(other_hash):
    return _run({"h1": _img(), "h2": _img()},
                {"h1": {"file_missing": False, "dhash": GOOD},
                 "h2": {"file_missing": False, "dhash": other_hash}})


def test_a_well_formed_hash_pair_is_the_control():
    out, _ = _pair(GOOD)
    assert out.found() == {("media-dup-page", "C1 H1,H2")}


@pytest.mark.parametrize("bad", [
    123, None, ["0"] * 64, b"0" * 64,   # not a string
    "", "0", "0" * 63, "0" * 65,        # wrong length: would equal int 0 if parsed
    "z" * 64, "0" * 63 + "g",           # right length, not hex
])
def test_a_malformed_hash_is_no_hash(bad):
    assert media._hash({"dhash": bad}) is None
    out, info = _pair(bad)
    assert out.rows == []
    assert info == {}


def test_hash_reads_a_64_digit_hex_string_and_a_missing_key():
    assert media._hash({"dhash": "0" * 63 + "f"}) == 15
    assert media._hash({"dhash": "F" * 64}) == 2 ** 256 - 1
    assert media._hash({}) is None


# -- every NON_VIEWABLE type ---------------------------------------------------------------------------

@pytest.mark.parametrize("mime", sorted(media.NON_VIEWABLE))
def test_every_listed_type_is_non_viewable_and_not_told_to_be_a_transcript(mime):
    assert media.non_viewable({"mime": mime}) == mime
    out, _ = _run({"h1": {"mime": mime, "path": "x"}}, {"h1": {"file_missing": False}})
    assert out.found() == {("media-non-viewable", "H1")}
    detail = out.rows[0][2]
    assert detail.startswith(mime) and "unpack or convert" in detail and "add_transcription" not in detail


@pytest.mark.parametrize("mime", ["application/xhtml+xml", "application/x-gzip", "application/zip",
                                  "application/gzip", "application/x-tar", "application/json"])
def test_the_named_types_are_in_the_set(mime):
    assert mime in media.NON_VIEWABLE


@pytest.mark.parametrize("mime", ["text/csv", "message/rfc822", "TEXT/Plain"])
def test_text_and_message_prefixes_suggest_a_transcript(mime):
    out, _ = _run({"h1": {"mime": mime}}, {"h1": {"file_missing": False}})
    assert out.found() == {("media-non-viewable", "H1")}
    assert "add_transcription" in out.rows[0][2]


@pytest.mark.parametrize("mime", ["application/octet-stream", "application/pdf", "image/png", "audio/mpeg",
                                  "application/x-zip", "texture/x"])
def test_other_types_are_not_non_viewable(mime):
    assert media.non_viewable({"mime": mime}) is None
    out, _ = _run({"h1": {"mime": mime}}, {"h1": {"file_missing": False}})
    assert out.rows == []


def test_scope_limits_single_kinds_but_the_count_covers_every_media():
    out, info = _run({"h1": {"mime": "text/plain"}, "h2": {"mime": "application/zip"}}, {},
                     scope={("media", "h2")})
    assert out.found() == {("media-non-viewable", "H2")}
    assert info == {"media files not checked": 2}
