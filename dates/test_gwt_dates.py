import datetime, os, sys
sys.path.insert(0, os.path.dirname(__file__))
import pytest
import gwt_dates as gd

D = datetime.date


@pytest.mark.parametrize('text,want', [
    ('1 vendemiaire an I', D(1792, 9, 22)),
    ('9 thermidor an II', D(1794, 7, 27)),
    ('18 Brumaire an VIII', D(1799, 11, 9)),
    ('14 germinal an 13', D(1805, 4, 4)),
    ('1er vendémiaire an XII', D(1803, 9, 24)),
    ('6 jour complementaire an III', D(1795, 9, 22)),
    ('5 jours complémentaires an II', D(1794, 9, 21)),
    ('10 nivose an XIV', D(1805, 12, 31)),
])
def test_known_days(text, want):
    assert gd.parse_republican(text) == want


def test_every_day_of_the_calendar_round_trips_and_years_have_the_right_length():
    day = D(1792, 9, 22)
    seen = 0
    while day <= gd.LAST_DAY:
        assert gd.to_gregorian(*gd.to_republican(day)) == day
        day += datetime.timedelta(days=1)
        seen += 1
    assert seen == sum(gd.year_length(y) for y in range(1, 14)) + 100  # an XIV ends on day 100
    assert [y for y in range(1, 15) if gd.year_length(y) == 366] == [3, 7, 11]


def test_the_sixth_complementary_day_exists_only_in_a_sextile_year():
    assert gd.to_gregorian(6, 13, 3) == D(1795, 9, 22)
    with pytest.raises(ValueError):
        gd.to_gregorian(6, 13, 4)


@pytest.mark.parametrize('day,month,year', [(0, 1, 1), (31, 1, 1), (6, 13, 1), (1, 1, 0), (1, 1, 15), (11, 4, 14)])
def test_impossible_or_out_of_range_dates_are_refused(day, month, year):
    with pytest.raises(ValueError):
        gd.to_gregorian(day, month, year)


def test_the_calendar_has_no_day_before_or_after_its_span():
    for bad in (D(1792, 9, 21), D(1806, 1, 1)):
        with pytest.raises(ValueError):
            gd.to_republican(bad)


@pytest.mark.parametrize('text,want', [
    ('30 7bre 1812', D(1812, 9, 30)), ('1 8bre 1700', D(1700, 10, 1)), ('2 9bre 1790', D(1790, 11, 2)),
    ('25 Xbre 1801', D(1801, 12, 25)), ('3 mars 1805', D(1805, 3, 3)), ('3 de marzo de 1805', D(1805, 3, 3)),
    ('12 août 1809', D(1809, 8, 12)),
])
def test_written_dates_and_old_style_months(text, want):
    assert gd.parse_gregorian_text(text) == want


@pytest.mark.parametrize('text', ['', '30 7bre', 'x 7bre 1812', '30 smarch 1812', '31 4 1812'])
def test_unreadable_written_dates_are_refused(text):
    with pytest.raises(ValueError):
        gd.parse_gregorian_text(text)


def test_a_day_that_does_not_exist_is_refused():
    with pytest.raises(ValueError):
        gd.parse_gregorian_text('31 septembre 1812')


def test_note_has_the_form_gwt_check_reads_back():
    assert gd.note(D(1805, 4, 4)) == '14 germinal an 13 = 04/04/1805'


def test_cli_prints_one_line_and_exits_2_on_a_bad_date(capsys):
    assert gd.main(['r2g', '14 germinal an XIII']) == 0
    assert capsys.readouterr().out == '1805-04-04\n'
    assert gd.main(['g2r', '1805-04-04']) == 0
    assert capsys.readouterr().out == '14 germinal an XIII\n'
    assert gd.main(['r2g', '31 germinal an XIII']) == 2
    assert 'gwt-dates:' in capsys.readouterr().err
    assert gd.main(['g2r', '1900-01-01']) == 2
    assert gd.main(['r2g', '14 germinal an XIII', '--as-note']) == 0
    assert capsys.readouterr().out == '14 germinal an 13 = 04/04/1805\n'
