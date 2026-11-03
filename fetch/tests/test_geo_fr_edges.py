"""Extra offline edge tests for geo_fr. Injected fake client, invented communes, no network."""
import pytest
from gwt_fetch import geo_fr


def row(name='Exemple-sur-Loire', code='37001', dep='37', xy=None):
    return {'nom': name, 'code': code, 'codeDepartement': dep,
            'centre': {'type': 'Point', 'coordinates': xy if xy is not None else [0.8, 47.4]}}


class Client:
    def __init__(self, data):
        self.data = data
        self.calls = []

    def json(self, url, **kw):
        self.calls.append((url, kw))
        return self.data


def test_exact_next_to_other_is_ambiguous():
    c = Client([row(name='Exemple', code='37001'), row(name='Exemple-les-Bois', code='41001', dep='41')])
    got = geo_fr.geocode(c, 'Exemple')
    assert [r['status'] for r in got] == ['ambiguous', 'ambiguous']
    assert [r['exact_name'] for r in got] == [True, False]
    assert all(r['requires_human_review'] and r['candidate_count'] == 2 for r in got)


def test_two_exact_names_are_ambiguous():
    got = geo_fr.geocode(Client([row(), row(code='41001', dep='41')]), 'Exemple-sur-Loire')
    assert {r['status'] for r in got} == {'ambiguous'}


def test_exact_next_to_other_beyond_limit_is_truncated_review():
    c = Client([row(name='Exemple', code='37001'), row(name='Autre', code='41001', dep='41')])
    got = geo_fr.geocode(c, 'Exemple', limit=1)
    assert len(got) == 1 and got[0]['status'] == 'truncated-review' and got[0]['candidate_count'] == 2


@pytest.mark.parametrize('xy', [[0.8, 90.000001], [0.8, -90.000001], [180.000001, 47.4], [-180.000001, 47.4],
                                [0.8], [0.8, 47.4, 1], [None, 47.4], [0.8, '47']])
def test_invalid_coordinate_bounds_and_shape(xy):
    with pytest.raises(ValueError):
        geo_fr.geocode(Client([row(xy=xy)]), 'X')


def test_bounds_are_inclusive():
    got = geo_fr.geocode(Client([row(xy=[180, 90]), row(code='41001', dep='41', xy=[-180, -90])]), 'X')
    assert [(r['lng'], r['lat']) for r in got] == [('180.000000', '90.000000'), ('-180.000000', '-90.000000')]


def test_missing_or_wrong_centre_rejected():
    for centre in (None, {'type': 'Polygon', 'coordinates': [0.8, 47.4]}, {'type': 'Point'}):
        bad = row()
        bad['centre'] = centre
        with pytest.raises(ValueError):
            geo_fr.geocode(Client([bad]), 'X')


@pytest.mark.parametrize('dep,code', [('2A', '2A004'), ('2B', '2B033')])
def test_corsica_codes_accepted(dep, code):
    c = Client([row(name='Exemple-en-Corse', code=code, dep=dep)])
    got = geo_fr.geocode(c, 'Exemple-en-Corse', department=dep, code=code)
    assert got[0]['insee_code'] == code and got[0]['department'] == dep
    assert got[0]['status'] == 'matched' and got[0]['source'] == geo_fr.API + '/' + code
    assert c.calls[0][1]['params']['codeDepartement'] == dep


def test_corsica_filter_excludes_other_department():
    c = Client([row(name='Exemple', code='2A004', dep='2A'), row(name='Exemple', code='2B033', dep='2B')])
    got = geo_fr.geocode(c, 'Exemple', department='2B')
    assert [r['insee_code'] for r in got] == ['2B033']


@pytest.mark.parametrize('bad', [row(dep='2C'), row(dep='2a'), row(code='2A04'), row(code='2C004')])
def test_corsica_lookalikes_rejected(bad):
    with pytest.raises(ValueError):
        geo_fr.geocode(Client([bad]), 'X')


@pytest.mark.parametrize('name', ['Exemple\tLoire', 'Exemple\nLoire', 'Exemple\x00', '\x1bExemple', 'Ex\ud800'])
def test_control_chars_with_valid_code_rejected(name):
    # code, department and coordinates are valid, so only the name check can raise.
    with pytest.raises(ValueError, match='name'):
        geo_fr.geocode(Client([row(name=name)]), 'X')


def test_matched_single_candidate_flags_and_count():
    got = geo_fr.geocode(Client([row()]), 'exemple sur loire')
    assert len(got) == 1
    r = got[0]
    assert r['status'] == 'matched' and r['exact_name'] is True and r['candidate_count'] == 1
    assert r['requires_human_review'] is True and r['current_commune_only'] is True


def test_single_inexact_candidate_is_ambiguous_not_matched():
    got = geo_fr.geocode(Client([row(name='Exemple-les-Bois')]), 'Exemple')
    assert got[0]['status'] == 'ambiguous' and got[0]['exact_name'] is False
    assert got[0]['candidate_count'] == 1 and got[0]['requires_human_review'] is True


def test_unmatched_empty_list_returns_empty():
    assert geo_fr.geocode(Client([]), 'Nulle-part') == []


def test_candidate_count_is_post_filter_and_pre_truncation():
    c = Client([row(name='A', code='37001'), row(name='B', code='37002'), row(name='C', code='37003'),
                row(name='D', code='41001', dep='41')])
    got = geo_fr.geocode(c, 'A', department='37', limit=2)
    assert [r['insee_code'] for r in got] == ['37001', '37002']
    assert {r['candidate_count'] for r in got} == {3}
    assert {r['status'] for r in got} == {'truncated-review'}
    assert all(r['requires_human_review'] and r['current_commune_only'] for r in got)


def test_limit_equal_to_count_is_not_truncated():
    c = Client([row(name='A', code='37001'), row(name='B', code='37002')])
    got = geo_fr.geocode(c, 'A', limit=2)
    assert {r['status'] for r in got} == {'ambiguous'} and {r['candidate_count'] for r in got} == {2}


def _args(places, department=None, insee_code=None, limit=10):
    import types
    return types.SimpleNamespace(places=places, department=department, insee_code=insee_code, limit=limit)


def test_run_empty_client_stderr_exact_and_exit_1(capsys):
    rc = geo_fr.run(_args(['Nulle-part']), Client([]))
    out, err = capsys.readouterr()
    assert rc == 1
    assert out == '\t'.join(geo_fr.COLUMNS) + '\n'
    assert err == "gwt-fetch: no current French commune candidate for 'Nulle-part'\n"


def test_run_partial_empty_reports_only_missing_query(capsys):
    class Two(Client):
        def json(self, url, **kw):
            return [row()] if kw['params']['nom'] == 'Exemple-sur-Loire' else []
    rc = geo_fr.run(_args(['Exemple-sur-Loire', "L'Absent"]), Two([]))
    out, err = capsys.readouterr()
    assert rc == 0
    assert err == 'gwt-fetch: no current French commune candidate for "L\'Absent"\n'
    lines = out.splitlines()
    assert len(lines) == 2 and lines[1].split('\t')[:5] == ['Exemple-sur-Loire', 'matched', 'Exemple-sur-Loire',
                                                           '37001', '37']


def test_run_matched_has_empty_stderr(capsys):
    assert geo_fr.run(_args(['Exemple-sur-Loire']), Client([row()])) == 0
    assert capsys.readouterr().err == ''


def test_control_chars_in_query_rejected_without_network():
    c = Client([row()])
    with pytest.raises(ValueError):
        geo_fr.geocode(c, 'Exemple\tLoire')
    assert not c.calls
