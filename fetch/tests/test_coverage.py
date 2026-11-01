import pytest

from gwt_fetch import coverage as cov
from gwt_fetch.cli import main


@pytest.mark.parametrize("expr,total,want", [
    ("1-3,7", 10, {1, 2, 3, 7}),
    ("8-", 10, {8, 9, 10}),
    ("4", 10, {4}),
    (" 2 , 2-3 ", 10, {2, 3}),
    ("", 10, set()),
])
def test_views_are_read_as_one_based_numbers_and_ranges(expr, total, want):
    assert cov.parse_views(expr, total) == want


@pytest.mark.parametrize("expr", ["0", "11", "3-1", "-3", "a", "1-b", "1-11", "5--6"])
def test_a_view_outside_the_register_or_unreadable_is_refused(expr):
    with pytest.raises(ValueError):
        cov.parse_views(expr, 10)


def test_ranges_are_joined():
    assert cov.ranges([1, 2, 3, 7, 9, 10]) == "1-3,7,9-10"
    assert cov.ranges([5]) == "5"
    assert cov.ranges([]) == ""


def test_a_complete_sweep_says_so():
    lines, ok = cov.report(4, {1, 2, 3, 4})
    assert ok and lines == ["read 4 of 4 views", "every view was read"]


def test_missing_views_are_listed():
    lines, ok = cov.report(10, {1, 2, 3, 4, 5, 9, 10})
    assert not ok and lines == ["read 7 of 10 views", "missing 3: 6-8"]


@pytest.mark.parametrize("read,name", [({1, 3, 5, 7, 9}, "even"), ({2, 4, 6, 8, 10}, "odd")])
def test_a_sweep_of_alternate_views_is_called_out(read, name):
    lines, ok = cov.report(10, read)
    assert not ok and any(f"every {name} view is missing" in ln and "double page" in ln for ln in lines)


def test_a_gap_that_is_not_a_pattern_gets_no_warning():
    lines, _ = cov.report(10, {1, 2, 3, 4, 6, 7, 8, 10})
    assert not any(ln.startswith("warning") for ln in lines)


def test_two_missing_views_are_not_enough_to_call_a_pattern():
    lines, _ = cov.report(4, {1, 3})
    assert not any(ln.startswith("warning") for ln in lines)


def test_a_download_folder_counts_by_file_name(tmp_path):
    for name in ("0001_p1.jpg", "0002_p2.jpg", "0004_p4.jpg"):
        (tmp_path / name).write_bytes(b"x")
    (tmp_path / "0003_p3.jpg").write_bytes(b"")  # an empty file is not a read view
    (tmp_path / "manifest.tsv").write_text("x")
    (tmp_path / "notes_0009.txt").write_text("x")
    assert cov.views_in_folder(str(tmp_path), 5) == {1, 2, 4}


def test_a_file_outside_the_register_is_refused(tmp_path):
    (tmp_path / "0099_p.jpg").write_bytes(b"x")
    with pytest.raises(ValueError):
        cov.views_in_folder(str(tmp_path), 5)
    with pytest.raises(ValueError):
        cov.views_in_folder(str(tmp_path / "nothing"), 5)


def test_the_command_exits_0_1_or_2(capsys, tmp_path):
    assert main(["coverage", "4", "--read", "1-4"]) == 0
    assert main(["coverage", "4", "--read", "1-3"]) == 1
    assert "missing 1: 4" in capsys.readouterr().out
    assert main(["coverage", "4", "--read", "9"]) == 2
    assert main(["coverage", "4"]) == 2
    assert main(["coverage", "0", "--read", "1"]) == 2
    assert main(["coverage", "100001", "--read", "1"]) == 2
    (tmp_path / "0001_a.jpg").write_bytes(b"x")
    assert main(["coverage", "2", "--read", "2", "--dir", str(tmp_path)]) == 0


def test_a_partial_download_is_not_a_view_that_was_read(tmp_path):
    (tmp_path / "0001_p1.jpg.part").write_bytes(b"half")
    (tmp_path / "0002_p2.jpg").write_bytes(b"whole")
    assert cov.views_in_folder(str(tmp_path), 2) == {2}
    assert main(["coverage", "2", "--dir", str(tmp_path)]) == 1
