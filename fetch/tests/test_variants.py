"""Invented spellings only; no requests, services or tree integration."""
import json
import builtins
import re
import socket

import pytest

from gwt_fetch.cli import main, parser
from gwt_fetch.names import fold


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("variants attempted network access")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)


def invoke(capsys, surname, *flags):
    assert main(["variants", "--surname", surname, *flags]) == 0
    out = capsys.readouterr()
    assert "search suggestions" in out.err
    assert "identity" in out.err and "merge" in out.err
    return out.out.splitlines(), out.err


def config(tmp_path, value):
    path = tmp_path / "variants.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    return str(path)


def test_config_is_opt_in_sorted_unique_and_keeps_original(tmp_path, capsys):
    path = config(tmp_path, {"Alfa": ["Alpha", "Alfha", "Alpha", "", "  "],
                             "Beta": ["Beta", "Bheta"]})
    lines, err = invoke(capsys, "Alfa", "--file", path)
    assert lines == ["Alfa", "Alfha", "Alpha"]
    assert "warning" in err and "duplicate" in err and "empty" in err
    assert invoke(capsys, "Beta", "--file", path)[0] == ["Beta", "Bheta"]
    assert invoke(capsys, "Besa", "--file", path)[0] == ["Besa"]
    assert invoke(capsys, "Alfa")[0] == ["Alfa"]


def test_config_original_is_not_replaced_or_folded(tmp_path, capsys):
    path = config(tmp_path, {"Álfa": ["Alfa", "Alpha"]})
    assert invoke(capsys, "Álfa", "--file", path)[0] == ["Alfa", "Alpha", "Álfa"]
    assert invoke(capsys, "álfa", "--file", path)[0] == ["álfa"]


@pytest.mark.parametrize("surname", ["D'Alfa", r"A.*+?[](){}^$|\Z", "$(false)`false`"])
def test_names_and_metacharacters_are_literal(surname, capsys):
    assert invoke(capsys, surname)[0] == [surname]
    lines, _ = invoke(capsys, surname, "--regex")
    pattern = re.compile(lines[0])
    assert pattern.fullmatch(fold(surname))
    assert not pattern.fullmatch("anything")
    assert not pattern.fullmatch(fold(surname) + "x")


def test_regex_escapes_each_config_alternative_and_folds_accents(tmp_path, capsys):
    path = config(tmp_path, {"Álfa": ["Al.fa", "Al|fa", "Al[fa]", "D'Alfa"]})
    lines, _ = invoke(capsys, "Álfa", "--file", path, "--regex")
    assert len(lines) == 1 and lines[0].startswith("(?i)")
    pattern = re.compile(lines[0])
    for name in ["ALFA", "Àlfa", "A\u0301lfa", "Al.fa", "Al|fa", "Al[fa]", "D'Alfa"]:
        assert pattern.fullmatch(fold(name))
    for other in ["Alxfa", "Al", "fa", "Alf", "prefixalfa"]:
        assert not pattern.fullmatch(other)


def test_besa_rules_are_explicit_single_edits(capsys):
    assert invoke(capsys, "Besa")[0] == ["Besa"]
    got = invoke(capsys, "Besa", "--rules")[0]
    assert {"Besa", "Beza", "Bessa"} <= set(got)
    assert got == sorted(set(got))
    assert "Bbeza" not in got and "Bezza" not in got


@pytest.mark.parametrize("surname,want", [
    ("Cafa", "Safa"), ("Safa", "Cafa"), ("Zafa", "Safa"),
    ("Phala", "Fala"), ("Fala", "Phala"), ("Yla", "Ila"),
    ("Ila", "Yla"), ("Balla", "Bala"), ("Bala", "Balla"),
    ("PHALA", "FALA"), ("phala", "fala"),
])
def test_documented_rule_directions(surname, want, capsys):
    assert want in invoke(capsys, surname, "--rules")[0]


def test_rules_do_not_recurse_or_expand_config_values(tmp_path, capsys):
    path = config(tmp_path, {"Besa": ["Phyla"]})
    got = invoke(capsys, "Besa", "--rules", "--file", path)[0]
    assert "Phyla" in got and "Fyla" not in got and "Phila" not in got
    got = invoke(capsys, "Besa" * 32, "--rules")[0]
    assert len(got) <= 1 + 6 * 128
    assert "Beza" * 32 not in got


@pytest.mark.parametrize("surname", ["", "  ", "\t", "Al\nfa", "Al\tfa", "Al\x00fa", "A" * 129])
def test_invalid_surnames_exit_two_without_output(surname, capsys):
    assert main(["variants", "--surname", surname]) == 2
    out = capsys.readouterr()
    assert out.out == "" and "gwt-fetch:" in out.err


@pytest.mark.parametrize("value", [[], {"Alfa": "Alpha"}, {"Alfa": [1]}, {"": ["Alfa"]}])
def test_bad_config_shape_exits_two(value, tmp_path, capsys):
    assert main(["variants", "--surname", "Alfa", "--file", config(tmp_path, value)]) == 2
    out = capsys.readouterr()
    assert not out.out and "gwt-fetch:" in out.err


@pytest.mark.parametrize("body", ['{"Alfa":', '{"Alfa": [], "Alfa": ["Alpha"]}', 'not JSON'])
def test_unreadable_or_ambiguous_json_is_refused(body, tmp_path, capsys):
    path = tmp_path / "bad.json"
    path.write_text(body)
    assert main(["variants", "--surname", "Alfa", "--file", str(path)]) == 2
    assert not capsys.readouterr().out


def test_missing_file_exits_two(tmp_path, capsys):
    assert main(["variants", "--surname", "Alfa", "--file", str(tmp_path / "absent")]) == 2
    assert not capsys.readouterr().out


def test_offline_dispatch_never_imports_http_client(monkeypatch, capsys):
    original_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name in {"net", "gwt_fetch.net", "requests"}:
            pytest.fail("offline variants imported the HTTP client")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    assert invoke(capsys, "Alfa")[0] == ["Alfa"]


def test_shipped_example_and_empty_lists(tmp_path, capsys):
    from pathlib import Path

    example = Path(__file__).resolve().parents[1] / "variants.example.json"
    assert json.loads(example.read_text()) == {"Alfa": ["Alfa", "Alpha", "Alfha"],
                                             "Beta": ["Beta", "Bheta"]}
    assert invoke(capsys, "Alfa", "--file", str(example))[0] == ["Alfa", "Alfha", "Alpha"]
    lines, err = invoke(capsys, "Alfa", "--file", config(tmp_path, {"Alfa": []}))
    assert lines == ["Alfa"] and "warning" in err and "empty" in err


def test_large_file_is_refused(tmp_path, capsys):
    path = tmp_path / "large.json"
    path.write_text(" " * (1024 * 1024 + 1))
    assert main(["variants", "--surname", "Alfa", "--file", str(path)]) == 2
    out = capsys.readouterr()
    assert not out.out and "1 MiB" in out.err


def test_help_states_scope_and_regex_contract(capsys):
    with pytest.raises(SystemExit) as exc:
        parser().parse_args(["variants", "--help"])
    assert exc.value.code == 0
    text = capsys.readouterr().out.lower()
    for word in ["offline", "single", "128", "fold", "case", "identity", "merge"]:
        assert word in text


def test_config_path_normalized_before_open(tmp_path, monkeypatch):
    from gwt_fetch import variants
    root = tmp_path / 'nested'
    root.mkdir()
    target = tmp_path / 'variants.json'
    target.write_text('{"Alfa":["Alpha"]}')
    observed = []
    original = builtins.open
    def checked(path, *args, **kwargs):
        observed.append(str(path))
        return original(path, *args, **kwargs)
    monkeypatch.setattr(builtins, 'open', checked)
    assert variants.load(str(root / '..' / target.name)) == {'Alfa': ['Alpha']}
    assert observed == [str(target.resolve())]
