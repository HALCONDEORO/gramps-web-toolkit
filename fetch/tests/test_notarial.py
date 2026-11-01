"""Local invented catalogue only; sockets and output writes are forbidden."""
import copy
import json
import socket
import subprocess
import sys
from pathlib import Path

import pytest

FIXTURE = Path(__file__).parent / 'data' / 'notarial_catalogue.json'


def catalogue():
    return json.loads(FIXTURE.read_text())


def plan(data=None, locality='Villaejemplo', start=1800, end=1806):
    from gwt_fetch.notarial import plan
    return plan(catalogue() if data is None else data, locality, start, end)


def test_inclusive_clipped_chain_with_gap_overlap_and_office_provenance():
    result = plan()
    assert result['period'] == [1800, 1806]
    assert result['catalogue_coverage'] == '1800-1803,1805-1806'
    assert result['catalogue_gaps'] == '1804'
    assert result['overlaps'] == [{'period': [1802, 1802], 'entries': ['CAT-A', 'CAT-B']}]
    assert [(r['id'], r['search_period']) for r in result['chain']] == [
        ('CAT-A', [1800, 1802]), ('CAT-B', [1802, 1803]),
        ('CAT-C', [1805, 1806]), ('CAT-U', None)]
    assert [r['office'] for r in result['chain'][:2]] == ['OfficeAlfa', 'OfficeBeta']
    assert result['unknown_coverage'] == ['CAT-U']
    for row in result['chain']:
        original = next(r for r in catalogue()['entries'] if r['id'] == row['id'])
        assert row['metadata'] == original
        assert row['status'] == 'candidate'
    assert 'not evidence of a contract, marriage or parentage' in result['warning']


def test_single_year_boundary_counts_and_never_extends_evidence():
    assert plan(start=1802, end=1802)['catalogue_coverage'] == '1802'
    assert plan(start=1797, end=1798)['catalogue_coverage'] == '1798'
    assert plan(start=1810, end=1811)['catalogue_gaps'] == '1811'


def test_uncertain_or_partial_dates_never_fill_a_gap():
    data = catalogue()
    data['entries'] = [data['entries'][-1]]
    assert plan(data)['catalogue_gaps'] == '1800-1806'
    for start, end in [(None, None), (1800, None), (None, 1806)]:
        row = data['entries'][0]
        row.update(start=start, end=end, certainty='exact')
        result = plan(data)
        assert result['catalogue_coverage'] == ''
        assert result['unknown_coverage'] == ['CAT-U']
        assert result['chain'][0]['search_period'] is None


def test_place_normalization_is_whole_place_not_substring_and_no_match_is_gap():
    assert plan(locality='  VÍLLAEJEMPLO  ')['catalogue_coverage'] == plan()['catalogue_coverage']
    result = plan(locality='Villa')
    assert result['chain'] == []
    assert result['catalogue_gaps'] == '1800-1806'


def test_exact_outside_period_excluded_and_input_not_mutated_or_reordered():
    data = catalogue()
    before = copy.deepcopy(data)
    result = plan(data, start=1811, end=1812)
    assert [r['id'] for r in result['chain']] == ['CAT-U']
    assert data == before
    data['entries'].reverse()
    assert plan(data) == plan()


@pytest.mark.parametrize('field,value', [('start', True), ('end', '1805'), ('start', 0),
    ('end', 10000), ('start', 1807), ('office', ''), ('cote', None),
    ('archive_ref', ' '), ('certainty', 'about'), ('locality', []), ('id', '')])
def test_malformed_metadata_refused_instead_of_silent_coverage(field, value):
    data = catalogue()
    data['entries'][0][field] = value
    with pytest.raises(ValueError):
        plan(data)


@pytest.mark.parametrize('start,end', [(1806, 1800), (True, 1800), (0, 1800), (1800, 10000)])
def test_invalid_query_period_refused(start, end):
    with pytest.raises(ValueError):
        plan(start=start, end=end)


@pytest.mark.parametrize('data', [[], {}, {'format': 'wrong', 'entries': []},
    {'format': 'gwt-notarial-catalogue/1', 'entries': [None]}])
def test_wrong_catalogue_schema_refused(data):
    with pytest.raises(ValueError):
        plan(data)


def test_duplicate_id_refused_even_when_offices_differ():
    data = catalogue()
    data['entries'][1]['id'] = 'CAT-A'
    with pytest.raises(ValueError):
        plan(data)


def test_cli_reads_only_local_json_without_client_or_socket(monkeypatch, tmp_path, capsys):
    from gwt_fetch.cli import main
    def forbidden(*args, **kwargs):
        pytest.fail('offline planner attempted network or output write')
    monkeypatch.setattr(socket, 'socket', forbidden)
    monkeypatch.setattr(Path, 'write_text', forbidden)
    before = FIXTURE.read_bytes()
    monkeypatch.chdir(tmp_path)
    assert main(['notarial-plan', str(FIXTURE.resolve()), '--locality', 'Villaejemplo',
                 '--from', '1800', '--to', '1806']) == 0
    result = json.loads(capsys.readouterr().out)
    assert result == plan()
    assert list(tmp_path.iterdir()) == []
    assert FIXTURE.read_bytes() == before


def test_cli_subprocess_works_without_requests():
    cli = Path(__file__).resolve().parents[1] / 'gwt-fetch'
    p = subprocess.run([sys.executable, str(cli), 'notarial-plan', str(FIXTURE.resolve()),
        '--locality', 'Villaejemplo', '--from', '1802', '--to', '1802'], capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    assert json.loads(p.stdout)['catalogue_coverage'] == '1802'


def test_cli_bad_json_or_missing_file_has_no_partial_output(tmp_path, capsys):
    from gwt_fetch.cli import main
    path = tmp_path / 'bad.json'
    for body in (None, '{', '[]'):
        if body is not None:
            path.write_text(body)
        assert main(['notarial-plan', str(path), '--locality', 'Villaejemplo',
                     '--from', '1800', '--to', '1806']) == 2
        captured = capsys.readouterr()
        assert captured.out == ''
        assert 'gwt-fetch:' in captured.err


def test_help_describes_local_candidate_only_planner(capsys):
    from gwt_fetch.cli import main
    with pytest.raises(SystemExit) as exc:
        main(['notarial-plan', '--help'])
    assert exc.value.code == 0
    help_text = capsys.readouterr().out
    for text in ('local JSON', 'inclusive', 'candidate', '--locality', '--from', '--to'):
        assert text in help_text


def test_overlap_segments_keep_the_exact_active_catalogue_ids():
    data = catalogue()
    a = data['entries'][0]
    b = data['entries'][1]
    a.update(start=1800, end=1804)
    b.update(start=1801, end=1803)
    c = data['entries'][2]
    c.update(start=1802, end=1804)
    assert plan(data)['overlaps'] == [
        {'period': [1801, 1801], 'entries': ['CAT-A', 'CAT-B']},
        {'period': [1802, 1803], 'entries': ['CAT-A', 'CAT-B', 'CAT-C']},
        {'period': [1804, 1804], 'entries': ['CAT-A', 'CAT-C']}]


def test_offline_cli_never_imports_network_client(monkeypatch, capsys):
    import builtins
    from gwt_fetch.cli import main
    real_import = builtins.__import__
    def guarded_import(name, *args, **kwargs):
        if name in ('net', 'gwt_fetch.net', 'requests'):
            pytest.fail('offline command tried to import a network dependency')
        return real_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, '__import__', guarded_import)
    assert main(['notarial-plan', str(FIXTURE), '--locality', 'Villaejemplo',
                 '--from', '1800', '--to', '1806']) == 0
    assert json.loads(capsys.readouterr().out)['unknown_coverage'] == ['CAT-U']


@pytest.mark.parametrize('start,end', [('1806', '1800'), ('0', '1806'), ('1800', '10000')])
def test_cli_invalid_period_has_error_without_partial_plan(start, end, capsys):
    from gwt_fetch.cli import main
    assert main(['notarial-plan', str(FIXTURE), '--locality', 'Villaejemplo',
                 '--from', start, '--to', end]) == 2
    captured = capsys.readouterr()
    assert captured.out == ''
    assert 'gwt-fetch:' in captured.err


def test_empty_catalogue_and_blank_query_are_not_fabricated_evidence():
    data = {'format': 'gwt-notarial-catalogue/1', 'entries': []}
    result = plan(data)
    assert result['chain'] == []
    assert result['catalogue_gaps'] == '1800-1806'
    with pytest.raises(ValueError):
        plan(locality=' ')


def test_place_helper_must_not_erase_distinct_non_ascii_locality_suffixes():
    data = catalogue()
    data['entries'][0]['locality'] = 'Villaejemplo東'
    data['entries'] = [data['entries'][0]]
    assert plan(data, locality='Villaejemplo西')['chain'] == []
    assert plan(data, locality='Villaejemplo東')['catalogue_coverage'] == '1800-1802'


@pytest.mark.parametrize('raw', [
    '{"locality":"Villaejemplo","locality":"Elsewhere"}',
    '{"start":1800,"start":1806}',
    '{"certainty":"uncertain","certainty":"exact"}',
    '{"entries":[{"provenance":"R1","provenance":"R2"}]}',
    '{"start":NaN}', '{"start":Infinity}', '{"start":-Infinity}',
])
def test_strict_json_before_any_output(raw, tmp_path, capsys):
    from gwt_fetch.cli import main
    p = tmp_path / 'bad.json'
    p.write_text(raw)
    assert main(['notarial-plan', str(p), '--locality', 'Villaejemplo',
                 '--from', '1800', '--to', '1806']) == 2
    result = capsys.readouterr()
    assert result.out == ''
    assert 'duplicate' in result.err or 'non-JSON' in result.err
