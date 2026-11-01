"""Invented local transcriptions only; no archive, tree or network access."""
import csv
import importlib
import importlib.util
import io
import json

import pytest


@pytest.fixture
def leads():
    assert importlib.util.find_spec('gwt_fetch.leads') is not None, 'local collateral-lead extraction is missing'
    return importlib.import_module('gwt_fetch.leads')


def block(body, ref='SYN-A/001'):
    return f'ref: {ref}\ndate: 1719-05-09\n{body}'


def test_marriage_preserves_two_witnesses_and_explicit_deceased_parent(leads):
    text = block("mariage de Jean Alfa fils de feu Jean Alfa ... presence de Paul Beta et Luc Gamma amis de l'epoux")
    rows = leads.extract(text)
    assert [(r['name'], r['role']) for r in rows] == [
        ('Jean Alfa', 'deceased_parent'), ('Paul Beta', 'witness'), ('Luc Gamma', 'witness')]
    assert all(r['ref'] == 'SYN-A/001' and r['date'] == '1719-05-09' for r in rows)
    assert all(r['literal'] in text for r in rows)
    assert rows[1]['declared_relation'] == "amis de l'epoux"
    assert set(rows[0]) == set(leads.COLUMNS)


@pytest.mark.parametrize('body,names,role,relation', [
    ('parrain Paul Beta, marraine Anne Delta', ['Paul Beta', 'Anne Delta'], 'godparent', None),
    ("frere de l'epouse Marc Epsilon", ['Marc Epsilon'], 'sibling', "frere de l'epouse"),
    ('témoins Paul Beta et Luc Gamma', ['Paul Beta', 'Luc Gamma'], 'witness', None),
    ('présents Paul Beta et Luc Gamma', ['Paul Beta', 'Luc Gamma'], 'witness', None),
    ('amis Paul Beta et Luc Gamma', ['Paul Beta', 'Luc Gamma'], 'witness', None),
    ('oncle Marc Epsilon', ['Marc Epsilon'], 'other', 'oncle'),
    ('cousin Marc Epsilon', ['Marc Epsilon'], 'other', 'cousin'),
    ('voisin Marc Epsilon', ['Marc Epsilon'], 'other', None),
    ('veuve de Paul Beta', ['Paul Beta'], 'spouse', 'veuve de'),
    ('veuf de Anne Delta', ['Anne Delta'], 'spouse', 'veuf de'),
    ('fille de feue Anne Delta', ['Anne Delta'], 'deceased_parent', 'fille de feue'),
    ('fils de Paul Beta', ['Paul Beta'], 'parent', 'fils de'),
    ('grand-mere Anne Delta', ['Anne Delta'], 'grandparent', 'grand-mere'),
    ('feu Paul Beta', ['Paul Beta'], 'other', 'feu'),
])
def test_fixed_patterns_and_conservative_fallback(leads, body, names, role, relation):
    rows = leads.extract(block(body))
    assert [r['name'] for r in rows] == names
    assert all(r['role'] == role for r in rows)
    assert all(r['confidence'] == ('low' if role == 'other' else 'high') for r in rows)
    if relation:
        assert rows[0]['declared_relation'] == relation
    assert all(r['literal'] in body and r['name'] in r['literal'] for r in rows)


@pytest.mark.parametrize('body', ['parrain Paul Beta (?)', 'parrain Paul (?) Beta'])
def test_uncertainty_is_retained(leads, body):
    row, = leads.extract(block(body))
    assert '(?)' in row['name']
    assert row['confidence'] == 'low'
    assert '(?)' in row['literal']


def test_subject_exclusion_is_occurrence_and_act_scoped(leads):
    text = block('mariage de Jean Alfa fils de feu Jean Alfa et de Anne Delta; parrain Paul Beta')
    text += '\n---\n' + block('temoin Anne Delta', 'SYN-B/002')
    rows = leads.extract(text, subject='Jean Alfa')
    assert [r['name'] for r in rows] == ['Paul Beta', 'Anne Delta']
    # Without a matching subject declaration, no unrelated parent is removed.
    assert leads.extract(block('fils de Paul Beta'), subject='Jean Alfa')[0]['name'] == 'Paul Beta'


def test_same_named_witness_is_not_removed_as_subject_parent(leads):
    rows = leads.extract(block('mariage de Jean Alfa fils de Paul Beta; temoin Paul Beta'), subject='Jean Alfa')
    assert [(r['name'], r['role']) for r in rows] == [('Paul Beta', 'witness')]


def test_multiple_acts_stay_separate_and_group_counts_mentions(leads):
    rows = leads.extract(block('temoin Paul Beta') + '\n---\n' + block('temoin Paul Beta', 'SYN-B/002'))
    assert len(rows) == 2
    assert [r['ref'] for r in rows] == ['SYN-A/001', 'SYN-B/002']
    grouped = leads.group_names(rows)
    assert grouped == [{'name': 'Paul Beta', 'count': 2}]


@pytest.mark.parametrize('text', ['', '  ', 'date: 1719-05-09\nparrain Paul Beta',
    'ref: SYN-A/001\nparrain Paul Beta', block('parrain Paul Beta').replace('1719-05-09', '1719-02-30'),
    block('parrain Paul Beta') + '\n---\ndate: 1719-05-09\nparrain Luc Gamma'])
def test_invalid_blocks_are_rejected_atomically(leads, text):
    with pytest.raises(ValueError):
        leads.extract(text)


def test_no_named_people_is_successful(leads):
    assert leads.extract(block('aucun nom lisible')) == []


def test_cli_local_only_and_round_trip(leads, tmp_path, capsys, monkeypatch):
    # Any accidental network setup fails even before a request could be made.
    import socket
    monkeypatch.setattr(socket, 'socket', lambda *a, **kw: pytest.fail('network used'))
    cli = importlib.import_module('gwt_fetch.cli')
    source = tmp_path / 'input.txt'
    source.write_text(block('parrain Paul Beta'), encoding='utf-8')
    assert cli.main(['leads', 'extract', '--in', str(source), '--format', 'json']) == 0
    rows = json.loads(capsys.readouterr().out)
    assert rows[0]['name'] == 'Paul Beta'
    assert cli.main(['leads', 'extract', '--in', str(source), '--format', 'tsv']) == 0
    tsv = capsys.readouterr().out
    assert list(csv.DictReader(io.StringIO(tsv), delimiter='\t')) == rows
    path = tmp_path / 'leads.tsv'
    path.write_text(tsv, encoding='utf-8')
    catalogue = tmp_path / 'names.txt'
    catalogue.write_text('Béta|Betta\n', encoding='utf-8')
    assert cli.main(['leads', 'match', '--leads', str(path), '--catalogue-names', str(catalogue)]) == 0
    matched = list(csv.DictReader(io.StringIO(capsys.readouterr().out), delimiter='\t'))
    assert matched[0]['match'] == 'posible coincidencia'
    assert cli.main(['leads', 'match', '--leads', str(path)]) == 0
    assert list(csv.DictReader(io.StringIO(capsys.readouterr().out), delimiter='\t'))[0]['match'] == ''
    source.write_text('', encoding='utf-8')
    assert cli.main(['leads', 'extract', '--in', str(source)]) == 2
    assert capsys.readouterr().out == ''


@pytest.mark.parametrize('prefix', ['=', '+', '-', '@'])
def test_all_tsv_cells_escape_formulas_without_changing_json_literal(leads, prefix):
    rows = leads.extract(block(f'parrain {prefix}Paul Beta', ref=prefix+'SYN-A/001'))
    out = io.StringIO()
    leads.write_rows(rows, leads.COLUMNS, 'tsv', out)
    row, = list(csv.DictReader(io.StringIO(out.getvalue()), delimiter='\t'))
    assert row['name'] == "'" + prefix + 'Paul Beta'
    assert row['ref'].startswith("'" + prefix)
    assert rows[0]['literal'] == f'parrain {prefix}Paul Beta'


def test_matching_is_surname_only_and_variants_explicit(leads):
    rows = leads.extract(block('temoins Paul Betta et Beta Gamma et Luc Betamax'))
    matched = leads.match_names(rows, ['Béta|Betta'])
    assert [r['match'] for r in matched] == ['posible coincidencia', '', '']
    assert all('match' not in row for row in rows)


def test_cli_group_and_missing_files(leads, tmp_path, capsys):
    cli = importlib.import_module('gwt_fetch.cli')
    path = tmp_path / 'input.txt'
    path.write_text(block('temoin Paul Beta') + '\n---\n' + block('temoin Paul Beta'), encoding='utf-8')
    assert cli.main(['leads', 'extract', '--in', str(path), '--group-by', 'name', '--format', 'json']) == 0
    assert json.loads(capsys.readouterr().out) == [{'name': 'Paul Beta', 'count': 2}]
    assert cli.main(['leads', 'extract', '--in', str(tmp_path / 'missing')]) == 2
    assert capsys.readouterr().out == ''


def test_literal_tsv_round_trip_preserves_local_text_verbatim(leads):
    body = 'parrain Paul\tBeta (?)'
    rows = leads.extract(block(body))
    out = io.StringIO()
    leads.write_rows(rows, leads.COLUMNS, 'tsv', out)
    row, = list(csv.DictReader(io.StringIO(out.getvalue()), delimiter='\t'))
    assert row['literal'] == body
    assert row['name'] == 'Paul\tBeta (?)'


def test_markers_are_whole_words(leads):
    row, = leads.extract(block('reparrain Paul Beta'))
    assert (row['role'], row['confidence']) == ('other', 'low')


def test_unknown_name_does_not_inherit_previous_role(leads):
    rows = leads.extract(block('parrain Paul Beta; voisin Luc Gamma'))
    assert [(r['role'], r['confidence']) for r in rows] == [('godparent', 'high'), ('other', 'low')]


def test_uncertainty_is_local_to_its_mention(leads):
    rows = leads.extract(block('temoins Paul Beta (?) et Luc Gamma'))
    assert [r['confidence'] for r in rows] == ['low', 'high']


def test_subject_without_event_intro_still_excludes_explicit_parents(leads):
    rows = leads.extract(block('Jean Alfa fils de Paul Beta et de Anne Delta; parrain Luc Gamma'), subject='Jean Alfa')
    assert [r['name'] for r in rows] == ['Luc Gamma']


def test_cli_rejects_bad_catalogue_and_malformed_tsv_before_output(leads, tmp_path, capsys):
    cli = importlib.import_module('gwt_fetch.cli')
    path = tmp_path / 'leads.tsv'
    path.write_text('name\tcount\nPaul Beta\t2\n', encoding='utf-8')
    assert cli.main(['leads', 'match', '--leads', str(path)]) == 2
    assert capsys.readouterr().out == ''
    path.write_text('\t'.join(leads.COLUMNS) + '\nSYN-A/001\t1719-05-09\n', encoding='utf-8')
    assert cli.main(['leads', 'match', '--leads', str(path)]) == 2
    assert capsys.readouterr().out == ''
    path.write_text('\t'.join(leads.COLUMNS) + '\n', encoding='utf-8')
    assert cli.main(['leads', 'match', '--leads', str(path), '--catalogue-names', str(tmp_path / 'absent')]) == 2
    assert capsys.readouterr().out == ''


def test_literal_is_not_html_and_is_not_decoded(leads):
    body = 'parrain <synthetic> &amp; Paul Beta'
    rows = leads.extract(block(body))
    out = io.StringIO()
    leads.write_rows(rows, leads.COLUMNS, 'tsv', out)
    row, = list(csv.DictReader(io.StringIO(out.getvalue()), delimiter='\t'))
    assert row['literal'] == rows[0]['literal'] == body


def test_literal_keeps_transcription_line_endings_in_api_and_cli(leads, tmp_path, capsys):
    body = 'parrain\r\nPaul Beta'
    text = block(body).replace('ref: SYN-A/001\n', 'ref: SYN-A/001\r\n')
    assert leads.extract(text)[0]['literal'] == body
    path = tmp_path / 'input.txt'
    path.write_bytes(text.encode('utf-8'))
    cli = importlib.import_module('gwt_fetch.cli')
    assert cli.main(['leads', 'extract', '--in', str(path), '--format', 'json']) == 0
    assert json.loads(capsys.readouterr().out)[0]['literal'] == body


@pytest.mark.parametrize('body,names', [
    ('ne fut pas parrain Paul Beta', ['Paul Beta']),
    ("Paul Beta n'est pas parrain", ['Paul Beta']),
    ('sans parrain Paul Beta', ['Paul Beta']),
    ('parrain Paul Beta non présent', ['Paul Beta']),
    ('parrain probable Paul Beta', ['Paul Beta']),
    ('parrain Paul Beta probablement', ['Paul Beta']),
    ('parrain peut-être Paul Beta', ['Paul Beta']),
    ('parrain Paul Beta ?', ['Paul Beta']),
    ('parrain Paul Beta ou Luc Gamma', ['Paul Beta', 'Luc Gamma']),
    ('parrain Paul Beta ou bien Luc Gamma', ['Paul Beta', 'Luc Gamma']),
    ('parrain soit Paul Beta soit Luc Gamma', ['Paul Beta', 'Luc Gamma']),
    ('temoins Paul Beta et Luc Gamma ne furent pas presents', ['Paul Beta', 'Luc Gamma']),
])
def test_negation_uncertainty_and_alternatives_are_never_affirmed(leads, body, names):
    rows = leads.extract(block(body))
    assert [r['name'] for r in rows] == names
    assert all((r['role'], r['confidence']) == ('other', 'low') for r in rows)
    # The whole clause is kept so the reviewer sees the negation/doubt/alternative.
    assert all(r['literal'] == body for r in rows)


def test_doubt_is_scoped_to_its_clause_and_literal_stays_verbatim(leads):
    rows = leads.extract(block('parrain probable Paul Beta; marraine Anne Delta. temoin Luc Gamma'))
    assert [(r['name'], r['role'], r['confidence']) for r in rows] == [
        ('Paul Beta', 'other', 'low'), ('Anne Delta', 'godparent', 'high'), ('Luc Gamma', 'witness', 'high')]
    assert rows[0]['literal'] == 'parrain probable Paul Beta'
    assert rows[1]['literal'] == 'marraine Anne Delta'


def test_uncertainty_mark_alone_keeps_its_role_but_low(leads):
    row, = leads.extract(block('parrain Paul Beta (?)'))
    assert (row['role'], row['confidence']) == ('godparent', 'low')


def test_wrapped_subject_parent_clause_is_still_excluded(leads):
    rows = leads.extract(block('Jean Alfa\nfils de Paul Beta\net de Anne Delta; temoin Luc Gamma'), subject='Jean Alfa')
    assert [r['name'] for r in rows] == ['Luc Gamma']


@pytest.mark.parametrize('body', [
    "Jean Alfa n'est pas fils de Paul Beta et de Anne Delta",
    'Jean Alfa fils probable de Paul Beta et de Anne Delta',
    'Jean Alfa fils de Paul Beta ou Anne Delta',
    'Jean Alfa fils de Paul Beta et de Anne Delta probablement',
])
def test_subject_keeps_doubtful_parent_mentions(leads, body):
    rows = leads.extract(block(body + '; temoin Luc Gamma'), subject='Jean Alfa')
    assert [(r['name'], r['role'], r['confidence']) for r in rows] == [
        ('Paul Beta', 'other', 'low'), ('Anne Delta', 'other', 'low'),
        ('Luc Gamma', 'witness', 'high')]
    assert all(r['literal'] == body for r in rows[:2])


def test_doubt_after_name_before_stop_withdraws_role():
    from gwt_fetch.leads import extract
    rows = extract("ref: SYN-A/001\ndate: 1800-01-01\nparrain Paul Beta probablement; témoin Luc Gamma.")
    paul = [r for r in rows if r['name'] == 'Paul Beta'][0]
    assert paul['role'] == 'other' and paul['confidence'] == 'low'
    assert paul['literal'] == 'parrain Paul Beta probablement'


def test_elided_negative_without_other_marker_withdraws_role():
    from gwt_fetch.leads import extract
    rows = extract("ref: SYN-A/001\ndate: 1800-01-01\nn'était parrain Paul Beta.")
    paul = [r for r in rows if r['name'] == 'Paul Beta'][0]
    assert paul['role'] == 'other' and paul['confidence'] == 'low'
    assert paul['literal'] == "n'était parrain Paul Beta"
