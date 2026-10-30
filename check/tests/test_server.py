"""The server helpers, against a fake client: invented people, no network."""
from gwt_check import server


class Fake:
    def __init__(self, answers):
        self.answers, self.calls = answers, []

    def get(self, path, params):
        self.calls.append((path, params))
        return {}, self.answers[path]


def test_history_is_newest_first_and_keeps_no_contents():
    rows = [{"id": 1, "timestamp": 1, "description": "old", "changes": [1]},
            {"id": 7, "timestamp": 9, "description": "Edit person", "changes": [1, 2], "connection": {"user": "u"}, "obj": {"x": 1}}]
    c = Fake({"/transactions/history/": rows})
    assert server.history(c, 1) == [{"id": 7, "timestamp": 9, "description": "Edit person", "user": "u", "changes": 2}]


def test_can_undo_only_asks():
    c = Fake({"/transactions/history/7/undo": {"conflicts": []}})
    assert server.can_undo(c, 7) == {"conflicts": []}
    assert [p for p, _ in c.calls] == ["/transactions/history/7/undo"]


def test_relation_single_and_all():
    c = Fake({"/relations/a/b": {"relationship_string": "father"},
              "/relations/a/b/all": [{"relationship_string": "father"}, {"relationship_string": "uncle"}]})
    assert server.relation(c, "a", "b") == "father"
    assert server.relation(c, "a", "b", all_paths=True) == ["father", "uncle"]
    assert server.relation(Fake({"/relations/a/c/all": [{}]}), "a", "c", all_paths=True) == []


def test_search_returns_no_text():
    c = Fake({"/search/": [{"object_type": "note", "object": {"handle": "h", "gramps_id": "N1", "text": "x"}, "score": 3}]})
    assert server.search(c, "q") == [{"type": "note", "handle": "h", "gramps_id": "N1", "score": 3}]


def test_living_diff_finds_the_server_only_case():
    people = [{"handle": "a", "gramps_id": "I1", "event_ref_list": []},
              {"handle": "b", "gramps_id": "I2", "event_ref_list": [{"ref": "d", "role": "Primary"}]}]
    events = {"d": {"type": "Death", "date": {"dateval": [0, 0, 1900, False]}}}
    c = Fake({"/living/a": {"living": True}, "/living/b": {"living": True}})
    r = server.living_diff(c, people, events, year=2026, workers=1)
    assert r["server_only"] == ["I2"] and r["agree"] == 1  # I1: undated, both say alive; I2: dead in 1900
