import unicodedata

from gwt_check.fill import _fold


def _reference(text):
    out = []
    for c in text:
        b = "".join(x for x in unicodedata.normalize("NFD", c) if not unicodedata.combining(x))
        b = b if len(b) == 1 else c
        lo = b.lower()
        out.append(lo if len(lo) == 1 else b)
    return "".join(out)


def test_fold_gives_what_folding_each_character_gives():
    for t in ("", "Plain ASCII 123", "Peña Ñandú ÁÉÍÓÚ ü", "İstanbul ß", "한국어 text", "e\u0301 combining"):
        assert _fold(t) == _reference(t)
        assert len(_fold(t)) == len(t) or any(ord(c) > 127 for c in t)
