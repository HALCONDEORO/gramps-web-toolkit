import datetime, os, sys
sys.path.insert(0, os.path.dirname(__file__))
import pytest
import gwt_books as gb

D = datetime.date


def marks(*pairs):
    return [(n, D.fromisoformat(d), '') for n, d in pairs]


def test_interpolates_between_the_two_landmarks_around_the_target():
    m = marks((100, '1900-01-01'), (200, '1910-01-01'))
    image, _ = gb.suggest(m, D(1905, 1, 1))
    assert 140 <= image <= 160


def test_never_suggests_a_landmark_image_or_leaves_the_window():
    m = marks((100, '1900-01-01'), (102, '1900-12-31'))
    image, _ = gb.suggest(m, D(1900, 12, 30))
    assert image == 101
    m = marks((100, '1900-01-01'), (101, '1900-12-31'))
    assert gb.suggest(m, D(1900, 6, 1))[0] is None


def test_target_equal_to_a_landmark_is_found():
    m = marks((100, '1900-01-01'), (200, '1910-01-01'))
    image, why = gb.suggest(m, D(1910, 1, 1))
    assert image == 200 and 'already' in why


def test_no_landmarks_opens_the_middle_and_needs_the_size():
    assert gb.suggest([], D(1926, 1, 1), 400)[0] == 200
    assert gb.suggest([], D(1926, 1, 1))[0] is None


def test_all_earlier_looks_later_and_all_later_looks_earlier():
    assert gb.suggest(marks((100, '1900-01-01')), D(1950, 1, 1), 400)[0] > 100
    assert gb.suggest(marks((100, '1950-01-01')), D(1900, 1, 1), 400)[0] < 100


def test_partial_dates_and_bad_dates():
    assert gb.parse_date('1926') == D(1926, 1, 1)
    assert gb.parse_date('1926-03') == D(1926, 3, 1)
    with pytest.raises(SystemExit):
        gb.parse_date('March 1926')


def test_log_roundtrip_and_next_prints_the_window(tmp_path, capsys):
    log = str(tmp_path / 'b.tsv')
    for img, d in ((100, '1900-01-01'), (200, '1910-01-01')):
        assert gb.main(['--log', log, 'add', '--book', 'B1', '--image', str(img), '--date', d]) == 0
    assert gb.main(['--log', log, 'add', '--book', 'B2', '--image', '5', '--date', '1800']) == 0
    capsys.readouterr()
    assert gb.main(['--log', log, 'next', '--book', 'B1', '--target', '1905-01-01']) == 0
    out = capsys.readouterr().out
    assert 'open image' in out and '99 images left between 100 and 200' in out
    assert len(gb.landmarks(gb.read_log(log), 'B1')) == 2


def test_a_reread_image_keeps_its_latest_date(tmp_path):
    log = str(tmp_path / 'b.tsv')
    gb.main(['--log', log, 'add', '--book', 'B', '--image', '7', '--date', '1900'])
    gb.main(['--log', log, 'add', '--book', 'B', '--image', '7', '--date', '1901'])
    assert gb.landmarks(gb.read_log(log), 'B') == [(7, D(1901, 1, 1), '')]


def test_a_tab_or_line_break_in_book_or_note_is_refused_and_the_log_stays_readable(tmp_path):
    log = str(tmp_path / 'b.tsv')
    for flag, bad in (('--book', 'a\tb'), ('--book', 'a\nb'), ('--note', 'x\ty'), ('--note', 'x\r\ny')):
        args = ['--log', log, 'add', '--book', 'B', '--image', '1', '--date', '1900', '--note', '']
        args[args.index(flag) + 1] = bad
        with pytest.raises(SystemExit):
            gb.main(args)
    assert not os.path.exists(log) or gb.read_log(log) == []
    with pytest.raises(SystemExit):
        gb.main(['--log', log, 'add', '--book', '  ', '--image', '1', '--date', '1900'])


def test_a_note_with_spaces_and_accents_roundtrips(tmp_path):
    log = str(tmp_path / 'b.tsv')
    gb.main(['--log', log, 'add', '--book', 'Libro 1', '--image', '3', '--date', '1900', '--note', 'bautismos, año 1900'])
    assert gb.landmarks(gb.read_log(log), 'Libro 1') == [(3, D(1900, 1, 1), 'bautismos, año 1900')]


@pytest.mark.parametrize('bad', ['0', '-4', 'x', '1.5'])
def test_image_and_total_must_be_positive_whole_numbers(bad, tmp_path):
    log = str(tmp_path / 'b.tsv')
    with pytest.raises(SystemExit):
        gb.main(['--log', log, 'add', '--book', 'B', '--image', bad, '--date', '1900'])
    with pytest.raises(SystemExit):
        gb.main(['--log', log, 'next', '--book', 'B', '--target', '1900', '--total', bad])


def test_adjacent_images_say_review_both_not_that_the_date_is_missing():
    _, why = gb.suggest(marks((100, '1900-01-01'), (101, '1900-12-31')), D(1900, 6, 1))
    assert 'review both' in why and 'not in' not in why
