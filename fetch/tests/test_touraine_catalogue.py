"""Invented catalogue evidence only; even accidental real HTTP is forbidden."""
import csv
import importlib
import io

import pytest
import requests

from gwt_fetch import touraine
from gwt_fetch.cli import parser
from gwt_fetch.net import Client
from test_fetch import FakeClient, Resp, _table_row


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("live HTTP forbidden")
    monkeypatch.setattr(requests.Session, "request", forbidden)


@pytest.fixture
def catalogue():
    return importlib.import_module("gwt_fetch.touraine_catalogue")


def row(ark="invent001", cote="6NUM/001", date="1700-1702", **kw):
    return _table_row(ark, cote, date, kw.get("place", "Exampleville"),
                      kw.get("kind", "état civil"), "2 medias", kw.get("title", "Registre"))


def table(*rows):
    return '<table class="results-table"><tbody>' + "".join(rows) + '</tbody></table>'


def client_for(*pages):
    def answer(url, params):
        assert params["page"] <= len(pages), "unexpected extra request"
        return pages[params["page"] - 1]
    return FakeClient({touraine.BASE + "/search/results": answer})


def args(*extra):
    return parser().parse_args(["touraine", "catalogue", "Exampleville", *extra])


def evidence(catalogue, *rows):
    return catalogue.collect(client_for(Resp(table(*rows)), Resp(table())), "Exampleville")


def coverage_input(catalogue, *rows):
    """Unit-test coverage with an assumed precondition, never terminal-page proof.

    No captured terminal contract exists. This deliberate input override keeps
    date/conflict/place guards testable while collection always stays incomplete.
    """
    result = evidence(catalogue, *rows)
    assert not result["complete"]
    result["complete"] = True
    return result


def test_disjoint_ranges_leave_only_1703(catalogue):
    result = coverage_input(catalogue, row(), row("invent002", "6NUM/002", "1704"))
    report = catalogue.year_coverage(result)
    assert result["complete"] is True
    assert report == {"complete": True, "span": "1700-1704", "uncovered": "1703"}


def test_overlapping_ranges_do_not_make_a_gap(catalogue):
    result = coverage_input(catalogue, row(), row("invent002", "6NUM/002", "1702-1704"))
    assert catalogue.year_coverage(result)["uncovered"] == ""


@pytest.mark.parametrize("date", ["1700? / unknown", "1700?", "unknown", "", "1704-1700",
                                 "1700/1702", "vers 1700", "0000", "１７００", "1700-1702 extra"])
def test_unknown_dates_stay_unknown_and_withhold_gaps(catalogue, date):
    result = coverage_input(catalogue, row(), row("invent002", "6NUM/002", date),
                      row("invent003", "6NUM/003", "1704"))
    assert catalogue.years(date) is None
    assert result["rows"][1]["date"] == date
    assert catalogue.year_coverage(result)["complete"] is False
    assert catalogue.year_coverage(result)["uncovered"] is None


def test_same_cote_different_arks_preserve_conflict_and_provenance(catalogue):
    result = coverage_input(catalogue, row(), row("invent002", "6NUM/001", "1704"))
    assert len(result["rows"]) == 2
    assert {r["ark"] for r in result["rows"]} == {"invent001", "invent002"}
    assert all(r["conflict"] == "yes" for r in result["rows"])
    for n, r in enumerate(result["rows"], 1):
        assert r["source_page"] == 1 and r["source_row"] == n
        assert "q=Exampleville" in r["source_url"] and "page=1" in r["source_url"]
        assert r["record_url"] == f'{touraine.BASE}/ark:/{touraine.NAAN}/{r["ark"]}'
    assert catalogue.year_coverage(result)["uncovered"] is None


def test_cross_page_conflict_marks_both_rows(catalogue):
    c = client_for(Resp(table(row())), Resp(table(row("invent002", date="1704"))), Resp(table()))
    result = catalogue.collect(c, "Exampleville")
    assert [r["source_page"] for r in result["rows"]] == [1, 2]
    assert [r["conflict"] for r in result["rows"]] == ["yes", "yes"]


@pytest.mark.parametrize("status", [403, 429, 500, 302])
def test_full_page_then_refusal_keeps_rows_and_never_certifies_gaps(status, capsys):
    full = table(*(row(f"invent{n:03}", f"6NUM/{n:03}", "1700" if n < 19 else "1704")
                   for n in range(20)))
    c = client_for(Resp(full), Resp("refused", status=status))
    assert touraine.run(args(), c) == 1
    output = capsys.readouterr()
    assert "pagination=incomplete" in output.err and "coverage=incomplete" in output.err
    assert "refused" in output.err and "uncovered=" not in output.err
    assert len(list(csv.DictReader(io.StringIO(output.out), delimiter="\t"))) == 20
    assert len(c.calls) == 2 and c.retries == 0
    assert all(kw.get("allow_redirects") is False for kw in c.kw)


@pytest.mark.parametrize("bad", ["<html>challenge</html>", "", table('<tr class="result record"><td>unparsed</td></tr>')])
def test_unrecognised_page_is_incomplete(catalogue, bad):
    c = client_for(Resp(table(row())), Resp(bad))
    result = catalogue.collect(c, "Exampleville")
    assert result["complete"] is False
    assert catalogue.year_coverage(result)["uncovered"] is None
    assert len(c.calls) == 2
    assert "unreadable" in result["reason"]


def test_short_page_is_not_completion_evidence(catalogue):
    c = client_for(Resp(table(row())), Resp("refused", status=403))
    result = catalogue.collect(c, "Exampleville")
    assert result["complete"] is False and len(c.calls) == 2


def test_repeated_page_stops_without_false_completion(catalogue):
    c = client_for(Resp(table(row())), Resp(table(row())))
    result = catalogue.collect(c, "Exampleville")
    assert not result["complete"] and "repeated" in result["reason"]
    assert catalogue.year_coverage(result)["uncovered"] is None


def test_page_limit_withholds_gaps(catalogue):
    c = client_for(Resp(table(row())))
    result = catalogue.collect(c, "Exampleville", max_pages=1)
    assert not result["complete"] and "limit" in result["reason"]
    assert catalogue.year_coverage(result)["uncovered"] is None


def test_empty_table_with_next_link_is_not_terminal(catalogue):
    c = client_for(Resp(table(row())), Resp(table() + '<a rel="next" href="?page=3">next</a>'))
    result = catalogue.collect(c, "Exampleville")
    assert not result["complete"] and len(c.calls) == 2
    assert catalogue.year_coverage(result)["uncovered"] is None


def test_same_ark_changed_metadata_is_conflict(catalogue):
    result = coverage_input(catalogue, row(), row(cote="6NUM/002", date="1704"))
    assert len(result["rows"]) == 2
    assert all(r["conflict"] == "yes" for r in result["rows"])
    assert catalogue.year_coverage(result)["uncovered"] is None


def test_missing_cote_withholds_gaps(catalogue):
    result = coverage_input(catalogue, row(cote=""), row("invent002", "6NUM/002", "1704"))
    assert catalogue.year_coverage(result)["uncovered"] is None


def test_numeric_year_order_and_inclusive_endpoints(capsys):
    c = client_for(Resp(table(row(date="0009-0011"))), Resp(table()))
    assert touraine.run(args(), c) == 1
    records = list(csv.DictReader(io.StringIO(capsys.readouterr().out), delimiter="\t"))
    assert [r["year"] for r in records] == ["9", "10", "11"]


def test_en_dash_and_whitespace_are_exact_ranges(catalogue):
    assert list(catalogue.years(" 1700 – 1702 ")) == [1700, 1701, 1702]


def test_filled_page_then_unmarked_empty_is_unknown(catalogue):
    c = client_for(Resp(table(*(row(f"invent{n:03}", f"6NUM/{n:03}", "1700")
                               for n in range(20)))), Resp(table()))
    result = catalogue.collect(c, "Exampleville")
    assert not result["complete"] and len(c.calls) == 2
    assert "UNKNOWN" in result["reason"]
    assert catalogue.year_coverage(result)["uncovered"] is None


def test_empty_catalogue_has_no_certifiable_span(catalogue):
    result = catalogue.collect(client_for(Resp(table())), "Exampleville")
    assert result["complete"] is False
    assert "UNKNOWN" in result["reason"]
    assert catalogue.year_coverage(result) == {"complete": False, "span": "", "uncovered": None}


def test_transport_error_retains_partial_rows(catalogue):
    c = client_for(Resp(table(row())))
    original = c.get
    def get(url, **kw):
        if kw["params"]["page"] == 2:
            raise requests.Timeout("invented timeout")
        return original(url, **kw)
    c.get = get
    result = catalogue.collect(c, "Exampleville")
    assert len(result["rows"]) == 1 and not result["complete"]
    assert catalogue.year_coverage(result)["uncovered"] is None


def test_query_is_value_and_all_tsv_cells_are_formula_safe(capsys):
    c = client_for(Resp(table(row(cote="=2+3", place="@place", kind="+kind",
                                title="&#9;=1+1\nnext"))), Resp(table()))
    parsed = parser().parse_args(["touraine", "catalogue", "=Exampleville"])
    assert touraine.run(parsed, c) == 1
    assert c.calls[0][2]["q"] == "=Exampleville"
    records = list(csv.DictReader(io.StringIO(capsys.readouterr().out), delimiter="\t"))
    assert len(records) == 3
    assert {r["query"] for r in records} == {"'=Exampleville"}
    assert records[0]["cote"] == "'=2+3" and records[0]["title"] == "'=1+1 next"
    assert all(cell[:1] not in "=+-@" for r in records for cell in r.values() if cell)


def test_output_orders_year_then_cote_and_prints_summary(capsys):
    c = client_for(Resp(table(row(cote="6NUM/B", date="1700-1702"),
                              row("invent002", "6NUM/A", "1702-1704"))), Resp(table()))
    assert touraine.run(args(), c) == 1
    output = capsys.readouterr()
    records = list(csv.DictReader(io.StringIO(output.out), delimiter="\t"))
    assert [(r["year"], r["cote"]) for r in records] == [
        ("1700", "6NUM/B"), ("1701", "6NUM/B"), ("1702", "6NUM/A"),
        ("1702", "6NUM/B"), ("1703", "6NUM/A"), ("1704", "6NUM/A")]
    assert "pagination=incomplete" in output.err and "coverage=incomplete" in output.err
    assert "UNKNOWN" in output.err and "uncovered=" not in output.err


def test_unknown_cli_row_and_incomplete_exit(capsys):
    c = client_for(Resp(table(row(date="1700? / unknown"))), Resp(table()))
    assert touraine.run(args(), c) == 1
    output = capsys.readouterr()
    records = list(csv.DictReader(io.StringIO(output.out), delimiter="\t"))
    assert records[0]["year"] == "unknown" and records[0]["date"] == "1700? / unknown"
    assert "coverage=incomplete" in output.err and "uncovered=" not in output.err


@pytest.mark.parametrize("extra", [["--max-pages", "0"], ["--per-page", "21"], ["   "]])
def test_bad_arguments_make_no_requests(extra, capsys):
    c = FakeClient({})
    try:
        parsed = args(*extra) if extra != ["   "] else parser().parse_args(["touraine", "catalogue", "   "])
    except SystemExit as exc:
        assert exc.code == 2
    else:
        assert touraine.run(parsed, c) == 2
    assert not c.calls


def test_real_client_pacing_no_redirects_no_retries(monkeypatch, capsys):
    calls, sleeps = [], []
    class Session:
        headers = {}
        def get(self, url, **kw):
            calls.append((url, kw))
            return Resp(table(row())) if len(calls) == 1 else Resp("refused", status=429)
    monkeypatch.setattr("gwt_fetch.net.time.monotonic", lambda: 100.0)
    monkeypatch.setattr("gwt_fetch.net.time.sleep", sleeps.append)
    c = Client(delay=0, retries=4, session=Session())
    assert touraine.run(args(), c) == 1
    assert len(calls) == 2 and sleeps == [1.0]
    assert all(kw["allow_redirects"] is False for _, kw in calls)
    assert c._last == {"archives.touraine.fr": 100.0}


@pytest.mark.parametrize("extra", [
    '<tr><td>unparsed</td></tr>',
    '<tr class="unexpected"><td>unparsed</td></tr>',
    '<tr><th>Heading</th><td>unparsed</td></tr>',
])
@pytest.mark.parametrize("populated", [False, True])
def test_every_non_header_row_must_be_accounted_for(catalogue, extra, populated):
    bad = table(*([row()] if populated else []), extra)
    c = client_for(Resp(table(row(date="1700"), row("invent002", "6NUM/002", "1704"))), Resp(bad))
    result = catalogue.collect(c, "Exampleville")
    assert not result["complete"]
    assert "unaccounted" in result["reason"]
    assert catalogue.year_coverage(result)["uncovered"] is None
    assert len(c.calls) == 2


@pytest.mark.parametrize("rel", ['rel="nofollow next"', "REL='NoFoLlOw NeXt'", 'rel = "prev&#9;NEXT"', 'rel=next'])
def test_rel_next_is_a_case_insensitive_token(catalogue, rel):
    c = client_for(Resp(table(row())), Resp(table() + f'<a {rel} href="?page=3">more</a>'))
    result = catalogue.collect(c, "Exampleville")
    assert not result["complete"]
    assert "next page" in result["reason"]
    assert catalogue.year_coverage(result)["uncovered"] is None
    assert len(c.calls) == 2


@pytest.mark.parametrize("prefix", [
    '<h1>Verify you are human</h1>',
    '<H1> Verify <span>you are</span> HUMAN </H1>',
])
def test_http200_human_challenge_with_empty_table_is_refused(prefix, capsys):
    c = client_for(Resp(table(row(date="1700"), row("invent002", "6NUM/002", "1704"))),
                   Resp(prefix + table()))
    assert touraine.run(args(), c) == 1
    output = capsys.readouterr()
    assert "pagination=incomplete" in output.err and "refused" in output.err
    assert "uncovered=" not in output.err
    assert len(c.calls) == 2 and c.retries == 0


@pytest.mark.parametrize("place", ["Elsewhere", "", "-", "Exampleville; Elsewhere",
                                    "Exampleville / Elsewhere", "Exampleville, Elsewhere",
                                    "Exampleville<br>Elsewhere", "Exampleville et Elsewhere",
                                    "Example-ville", "Éxampleville"])
def test_missing_mixed_or_ambiguous_row_scope_withholds_gaps(catalogue, place):
    result = coverage_input(catalogue, row(date="1700"),
                      row("invent002", "6NUM/002", "1704", place=place, title="Exampleville"))
    assert len(result["rows"]) == 2
    assert catalogue.year_coverage(result) == {"complete": False, "span": "", "uncovered": None}


def test_query_and_title_cannot_supply_missing_place_scope(catalogue):
    result = coverage_input(catalogue, row(date="1700", place="", title="Exampleville"),
                      row("invent002", "6NUM/002", "1704", place="", title="Exampleville"))
    assert catalogue.year_coverage(result)["uncovered"] is None


def test_scope_ambiguity_is_not_a_successful_commune_catalogue(capsys):
    c = client_for(Resp(table(row(date="1700"), row("invent002", "6NUM/002", "1704", place="Elsewhere"))),
                   Resp(table()))
    assert touraine.run(args(), c) == 1
    output = capsys.readouterr()
    assert "coverage=incomplete" in output.err and "uncovered=" not in output.err


def test_only_normalized_exact_row_places_establish_scope(catalogue):
    result = coverage_input(catalogue, row(date="1700", place=" New  Éxampleville "),
                      row("invent002", "6NUM/002", "1704", place="new E&#769;xampleville"))
    assert catalogue.year_coverage(result)["uncovered"] == "1701-1703"


def test_header_only_table_is_not_terminal_evidence(catalogue):
    c = client_for(Resp(table(row())), Resp(table('<tr><th>Cote</th><th>Date</th></tr>')))
    assert not catalogue.collect(c, "Exampleville")["complete"]


@pytest.mark.parametrize("place", [
    "Exampleville; Elsewhere", "Exampleville<br>Elsewhere",
    '<a>Exampleville</a><a>Elsewhere</a>',
    'Exampleville<span class="label">Lieu</span>Elsewhere',
])
def test_identical_ambiguous_place_cells_are_not_single_place_evidence(catalogue, place):
    result = coverage_input(catalogue, row(date="1700", place=place),
                      row("invent002", "6NUM/002", "1704", place=place))
    assert len(result["rows"]) == 2
    assert catalogue.year_coverage(result)["uncovered"] is None


def test_non_next_rel_token_cannot_prove_terminal_page(catalogue):
    c = client_for(Resp(table(row())), Resp(table() + '<a rel="nextish nofollow">link</a>'))
    assert not catalogue.collect(c, "Exampleville")["complete"]


def test_http200_challenge_never_retries_real_client(monkeypatch, capsys):
    calls = []
    class Session:
        headers = {}
        def get(self, url, **kw):
            calls.append(kw)
            return Resp('<h1>Verify you are human</h1>' + table())
    c = Client(delay=0, retries=4, session=Session())
    assert touraine.run(args(), c) == 1
    assert len(calls) == 1 and calls[0]["allow_redirects"] is False and c.retries == 0
    assert "uncovered=" not in capsys.readouterr().err


@pytest.mark.parametrize("prefix", [
    "",
    '<title>Access denied</title>',
    '<h1>Vérifiez que vous êtes humain</h1>',
    '<div class="g-recaptcha" data-sitekey="invented"></div>',
])
def test_unverified_terminal_cli_keeps_rows_and_reports_unknown(prefix, capsys):
    c = client_for(Resp(table(row(date="1700"), row("invent002", "6NUM/002", "1704"))),
                   Resp(prefix + table()))
    assert touraine.run(args(), c) == 1
    output = capsys.readouterr()
    assert "completeness=UNKNOWN" in output.err
    assert "no captured positive terminal" in output.err
    assert "uncovered=" not in output.err and "span=" not in output.err
    records = list(csv.DictReader(io.StringIO(output.out), delimiter="\t"))
    assert len(records) == 2
    assert [r["source_row"] for r in records] == ["1", "2"]
    assert all(r["source_url"] and r["record_url"] for r in records)
    assert len(c.calls) == 2 and c.retries == 0
