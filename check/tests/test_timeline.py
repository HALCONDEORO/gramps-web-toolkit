"""The timeline report, against a fake client: invented data, no network."""
from gwt_check import timeline


class Fake:
    def get(self, path, params):
        self.seen = (path, params)
        return {}, [{"gramps_id": "E1", "type": "Birth", "role": "Primary", "date": "1850", "description": "private text",
                     "place": {"name": "x"}, "person": {"gramps_id": "I1", "name": "x"}}]


def test_rows_carry_ids_type_role_and_date_never_text():
    c = Fake()
    rows = timeline.timeline(c, ["h1", "h2"])
    assert rows == [{"person": "I1", "event": "E1", "type": "Birth", "role": "Primary", "date": "1850"}]
    assert c.seen == ("/timelines/people/", {"handles": "h1,h2", "pagesize": 200})


def test_limit_caps_the_rows():
    class Many(Fake):
        def get(self, path, params):
            return {}, [{"gramps_id": f"E{i}", "person": {"gramps_id": "I1"}} for i in range(5)]
    assert len(timeline.timeline(Many(), ["h"], limit=2)) == 2
