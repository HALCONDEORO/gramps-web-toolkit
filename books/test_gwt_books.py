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
    assert 'open image' in out and 'remaining zone: image 101 to image 199 (99 images)' in out
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


@pytest.mark.parametrize('bad', ['1900\n', '1900\t', ' 1900', '1900-01-01\n', '1900-1', '19000', 'x'])
def test_a_date_with_anything_around_it_is_refused_and_nothing_is_written(bad, tmp_path):
    log = str(tmp_path / 'b.tsv')
    with pytest.raises(SystemExit):
        gb.main(['--log', log, 'add', '--book', 'B', '--image', '1', '--date', bad])
    assert not os.path.exists(log)


def test_total_below_a_marked_image_is_refused(tmp_path):
    log = str(tmp_path / 'b.tsv')
    for img, d in ((3, '1900'), (200, '1950')):
        gb.main(['--log', log, 'add', '--book', 'B', '--image', str(img), '--date', d])
    with pytest.raises(SystemExit):
        gb.main(['--log', log, 'next', '--book', 'B', '--target', '1960', '--total', '10'])
    assert gb.main(['--log', log, 'next', '--book', 'B', '--target', '1960', '--total', '400']) == 0


def test_book_label_edge_spaces_are_dropped_in_every_command(tmp_path, capsys):
    log = str(tmp_path / 'b.tsv')
    gb.main(['--log', log, 'add', '--book', ' B ', '--image', '1', '--date', '1900'])
    gb.main(['--log', log, 'add', '--book', 'B', '--image', '9', '--date', '1910'])
    capsys.readouterr()
    assert gb.main(['--log', log, 'next', '--book', ' B ', '--target', '1905']) == 0
    assert 'open image' in capsys.readouterr().out
    gb.main(['--log', log, 'show', '--book', ' B '])
    assert capsys.readouterr().out.count('1900') >= 1


def test_one_sided_landmarks_print_the_remaining_zone_to_the_end_or_the_start(tmp_path, capsys):
    log = str(tmp_path / 'b.tsv')
    gb.main(['--log', log, 'add', '--book', 'B', '--image', '100', '--date', '1900'])
    capsys.readouterr()
    gb.main(['--log', log, 'next', '--book', 'B', '--target', '1950', '--total', '400'])
    assert 'remaining zone: image 101 to image 400 (300 images)' in capsys.readouterr().out
    assert gb.main(['--log', log, 'next', '--book', 'B', '--target', '1950']) == 1
    assert 'remaining zone: image 101 to the end of the book' in capsys.readouterr().out
    gb.main(['--log', log, 'next', '--book', 'B', '--target', '1800'])
    assert 'remaining zone: image 1 to image 99 (99 images)' in capsys.readouterr().out


def test_status_summarises_and_refuses_a_total_below_a_mark(tmp_path, capsys):
    log = str(tmp_path / 'b.tsv')
    assert gb.main(['--log', log, 'status', '--book', 'B']) == 1
    assert 'no landmarks yet' in capsys.readouterr().out
    for img, d in ((100, '1900'), (200, '1910')):
        gb.main(['--log', log, 'add', '--book', 'B', '--image', str(img), '--date', d])
    capsys.readouterr()
    assert gb.main(['--log', log, 'status', '--book', 'B', '--target', '1905']) == 0
    out = capsys.readouterr().out
    assert '2 landmarks, images 100 to 200' in out and 'next open image' in out and 'remaining zone' in out
    with pytest.raises(SystemExit):
        gb.main(['--log', log, 'status', '--book', 'B', '--total', '50'])


def test_export_writes_csv_with_quoting_and_to_a_file(tmp_path, capsys):
    log = str(tmp_path / 'b.tsv')
    gb.main(['--log', log, 'add', '--book', 'B', '--image', '5', '--date', '1900-02', '--note', 'a, "b"'])
    capsys.readouterr()
    gb.main(['--log', log, 'export', '--book', 'B'])
    assert capsys.readouterr().out.splitlines() == ['book,image,date,note', 'B,5,1900-02-01,"a, ""b"""']
    out = str(tmp_path / 'o.csv')
    gb.main(['--log', log, 'export', '--book', 'B', '--out', out])
    assert open(out, encoding='utf-8').read().splitlines()[1].startswith('B,5,')


def _two_books(tmp_path):
    log = str(tmp_path / 'b.tsv')
    gb.main(['--log', log, 'add', '--book', 'A', '--image', '1', '--date', '1900'])
    gb.main(['--log', log, 'add', '--book', 'B', '--image', '2', '--date', '1901'])
    return log, open(log, encoding='utf-8').read()


def test_export_refuses_the_log_itself_by_path_symlink_and_hard_link(tmp_path):
    log, before = _two_books(tmp_path)
    sym, hard = str(tmp_path / 'sym.tsv'), str(tmp_path / 'hard.tsv')
    os.symlink(log, sym)
    os.link(log, hard)
    for out in (log, os.path.join(str(tmp_path), '.', 'b.tsv'), sym, hard):
        with pytest.raises(SystemExit):
            gb.main(['--log', log, 'export', '--book', 'A', '--out', out])
        assert open(log, encoding='utf-8').read() == before


def test_export_to_a_new_file_leaves_no_temporary_file(tmp_path):
    log, _ = _two_books(tmp_path)
    out = str(tmp_path / 'a.csv')
    assert gb.main(['--log', log, 'export', '--book', 'A', '--out', out]) == 0
    assert sorted(os.listdir(tmp_path)) == ['a.csv', 'b.tsv']
    assert open(out, encoding='utf-8').read().splitlines()[1] == 'A,1,1900-01-01,'


def test_an_empty_zone_is_said_plainly_never_as_the_end_of_the_book_or_an_inverted_range():
    assert 'none' in gb.zone_line(gb.zone(marks((1, '1900-01-01')), D(1800, 1, 1), 10))
    z = gb.zone(marks((100, '1900-01-01'), (101, '1900-12-31')), D(1900, 6, 1))
    assert z == (101, 100, 0) and 'none' in gb.zone_line(z) and '101 to 100' not in gb.zone_line(z)
    z = gb.zone(marks((10, '1900-01-01')), D(1950, 1, 1), 10)
    assert 'none' in gb.zone_line(z)
    assert 'to the end of the book' in gb.zone_line(gb.zone(marks((10, '1900-01-01')), D(1950, 1, 1)))
