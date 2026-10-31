import csv, io, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pytest
import gwt_acts as A

DUMP = {"objects": {
    "people": [
        {"handle": "a", "gramps_id": "I1", "primary_name": {"first_name": "Rosa Maria", "surname_list": [{"surname": "Marlot"}]},
         "parent_family_list": [], "event_ref_list": [{"ref": "e1"}]},
        {"handle": "b", "gramps_id": "I2", "primary_name": {"first_name": "Jean", "surname_list": [{"surname": "Duval"}]},
         "parent_family_list": [], "event_ref_list": [{"ref": "e2"}]}],
    "events": [{"handle": "e1", "type": "Birth", "date": {"dateval": [9, 3, 1771, False]}, "place": "p1"},
               {"handle": "e2", "type": "Birth", "date": {"dateval": [0, 0, 1750, False]}, "place": "p1"}],
    "places": [{"handle": "p1", "name": {"value": "Saint-Test"}}], "families": []}}

BASE = dict(card="c1", type="nacimiento", given="Rosa Maria", surname="Marlot", date="9 mars 1771", date_iso="1771-03-09",
            republican="", father="", mother="", state="verificado-zoom", cote="6NUM0/000/001", ark="abc123def456",
            url="https://example.org/ark:/1/abc123def456/uuid", filiation="fille de A et de B", zoom="crop p12 full/max",
            place="Saint-Test", notes="")


def card(**kw):
    return {**BASE, **kw}


def verdict(c, dump='tree'):
    return A.check([c], DUMP if dump == 'tree' else dump)[0]


def test_a_complete_card_is_ok_without_a_tree():
    assert A.check([card()])[0] == ('c1', 'ok', '', False)


@pytest.mark.parametrize('field', ['cote', 'ark', 'url', 'filiation', 'date_iso'])
def test_a_card_missing_a_required_field_is_rejected(field):
    r = verdict(card(**{field: ''}), None)
    assert r[1] == 'rejected' and f'missing {field}' in r[2] and r[3]


def test_state_must_be_known_and_zoom_is_required_for_verificado_zoom():
    assert 'state must be' in verdict(card(state='seguro'), None)[2]
    assert 'needs a zoom' in verdict(card(zoom=''), None)[2]
    assert verdict(card(zoom='', state='dudoso'), None)[1] == 'ok'


def test_verificado_zoom_refuses_a_name_marked_doubtful():
    assert 'marked (?)' in verdict(card(surname='Mar(?)lot'), None)[2]
    assert verdict(card(surname='Mar(?)lot', state='dudoso'), None)[1] == 'ok'


def test_a_republican_date_must_give_the_card_date():
    ok = card(republican='14 germinal an XIII', date_iso='1805-04-04')
    assert verdict(ok, None)[1] == 'ok'
    bad = verdict(card(republican='14 germinal an XIII', date_iso='1805-04-05'), None)
    assert 'gives 1805-04-04' in bad[2]
    assert 'cannot read' in verdict(card(republican='14 germinal an 1.3'), None)[2]


def test_an_acte_delivre_must_be_dated_bef():
    assert 'bef' in verdict(card(notes='Acte délivré le 12 mars 1810'), None)[2]
    assert verdict(card(notes='Acte délivré le 12 mars 1810', date_iso='bef 1810-03-12'), None)[1] == 'ok'


def test_parents_of_a_card_that_is_not_verificado_zoom_are_marked_read_only():
    r = verdict(card(father='Pierre', state='dudoso'), None)
    assert 'read-only' in r[2]
    assert 'read-only' not in verdict(card(father='Pierre'), None)[2]


def test_a_person_already_in_the_tree_is_found_with_a_spelling_note():
    r = verdict(card(surname='Marllot'))
    assert r[1].startswith('already as I1') or r[1].startswith('possible duplicate (?) I1')
    assert 'spelling' in r[2] and 'marlot' in r[2]


def test_an_unknown_person_is_new():
    assert verdict(card(given='Paul', surname='Lemaire', date_iso='1700-01-01', date='1700', place='X'))[1] == 'nouveau'


def test_a_different_day_for_the_same_person_is_a_conflict_and_nothing_is_replaced():
    r = verdict(card(date_iso='1771-03-26'))
    assert r[3] and 'CONFLICT I1 Birth: the act says 1771-03-26, the tree says 1771-03-09' in r[2]
    assert DUMP['objects']['events'][0]['date']['dateval'] == [9, 3, 1771, False]


def test_the_same_day_is_no_conflict_and_a_vaguer_tree_date_holding_the_day_is_none():
    assert not verdict(card())[3]
    d = json.loads(json.dumps(DUMP))
    d['objects']['events'][0]['date']['dateval'] = [0, 3, 1771, False]
    assert not verdict(card(), d)[3]


def test_output_cells_neutralise_formulas_and_tabs():
    assert A.cell('=cmd|x\ty\nz') == "'=cmd|x y z"


def test_cli_prints_a_tsv_and_exit_codes(tmp_path, capsys):
    cards = tmp_path / 'c.tsv'
    dump = tmp_path / 'd.json'
    dump.write_text(json.dumps(DUMP))

    def write(*rows):
        buf = io.StringIO()
        w = csv.DictWriter(buf, list(BASE), delimiter='\t', lineterminator='\n')
        w.writeheader()
        w.writerows(rows)
        cards.write_text(buf.getvalue())
    write(card(given='Paul', surname='Lemaire', date_iso='1700-01-01', place='X'))
    assert A.main(['check', str(cards), '--dump', str(dump)]) == 0
    assert capsys.readouterr().out.splitlines()[0] == 'card\tverdict\tdetail'
    write(card(cote=''))
    assert A.main(['check', str(cards)]) == 1
    write(card(date_iso='1771-03-26'))
    assert A.main(['check', str(cards), '--dump', str(dump)]) == 1
    assert A.main(['check', str(tmp_path / 'missing.tsv')]) == 2


def test_an_exact_match_is_told_apart_from_a_possible_one():
    assert verdict(card())[1] == 'already as I1'
    r = verdict(card(given='Paul'))
    assert r[1] == 'possible duplicate (?) I1'


@pytest.mark.parametrize('bad', ['1771-99-99', 'anything', '1771-02-30', 'bef', 'x 1771-03-09', '17710309'])
def test_a_date_iso_that_is_not_a_date_is_rejected(bad):
    r = verdict(card(date_iso=bad), None)
    assert r[1] == 'rejected' and 'not a date' in r[2]


@pytest.mark.parametrize('ok', ['1771', '1771-03', '1771-03-09', 'bef 1810-03-12', 'aft 1771', 'abt 1771-03'])
def test_real_dates_with_or_without_a_qualifier_pass(ok):
    assert verdict(card(date_iso=ok), None)[1] == 'ok'


@pytest.mark.parametrize('field', ['given', 'surname', 'type'])
def test_given_surname_and_type_are_required(field):
    assert f'missing {field}' in verdict(card(**{field: ''}), None)[2]


def test_a_baptism_is_compared_with_a_baptism_not_with_a_birth():
    r = verdict(card(type='bautismo', date_iso='1771-03-12'))
    assert not r[3]


def test_a_bound_is_kept_and_a_tree_date_inside_it_is_no_conflict():
    assert not verdict(card(date_iso='bef 1810-03-12', date='acte delivre'))[3]
    r = verdict(card(date_iso='bef 1771-03-01', date='acte delivre'))
    assert r[3] and 'at most 1771-03-01' in r[2]


def test_a_name_and_place_without_a_year_is_not_already_in_the_tree():
    d = json.loads(json.dumps(DUMP))
    d['objects']['events'][0]['date'] = {}
    r = verdict(card(date_iso='1900-01-01'), d)
    assert r[1] == 'possible duplicate (?) I1' and 'not enough' in r[2]
    assert verdict(card())[1] == 'already as I1'


def test_a_bound_is_never_enough_to_say_it_is_already_in_the_tree():
    assert verdict(card(date_iso='bef 1771-03-09', date='acte delivre'))[1].startswith('possible duplicate (?)')
