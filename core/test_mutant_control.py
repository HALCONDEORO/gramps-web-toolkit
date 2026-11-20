"""Run with pytest; fixtures are disposable repositories, not real genealogy data."""
import importlib.util
from pathlib import Path
import sys

spec = importlib.util.spec_from_file_location("runner", Path(__file__).resolve().parents[1] / ".github" / "scripts" / "run_tests.py")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def setup_repo(tmp_path, monkeypatch, test):
    pkg = tmp_path / "probe"
    pkg.mkdir()
    (pkg / "value.py").write_text("VALUE = 1\n")
    (pkg / "test_value.py").write_text(test)
    (pkg / "mutants.txt").write_text("value.py | VALUE = 1 | VALUE = 1 | no-op control\n")
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    monkeypatch.setattr(runner, "PYTEST", [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"])
    return pkg


def test_noop_survives_when_test_imports_sibling(tmp_path, monkeypatch, capsys):
    setup_repo(tmp_path, monkeypatch, "import sys\nfrom pathlib import Path\nsys.path.insert(0, str(Path(__file__).parents[1] / 'sibling'))\nfrom value import VALUE\nfrom helper import EXPECTED\ndef test_value():\n    assert VALUE == EXPECTED\n")
    sibling = tmp_path / "sibling"
    sibling.mkdir()
    (sibling / "helper.py").write_text("EXPECTED = 1\n")
    assert runner.run_mutants(["probe"]) == 1
    out = capsys.readouterr().out
    assert "SURVIVED" in out and "1 mutants, 0 killed" in out


def test_broken_unmutated_suite_never_counts_kill(tmp_path, monkeypatch, capsys):
    setup_repo(tmp_path, monkeypatch, "def test_value():\n    assert False\n")
    assert runner.run_mutants(["probe"]) == 1
    out = capsys.readouterr().out
    assert "unmutated copy" in out and "0 mutants, 0 killed" in out


def test_real_mutation_is_killed_after_control(tmp_path, monkeypatch, capsys):
    pkg = setup_repo(tmp_path, monkeypatch, "from value import VALUE\ndef test_value():\n    assert VALUE == 1\n")
    (pkg / "mutants.txt").write_text("value.py | VALUE = 1 | VALUE = 2 | wrong value\n")
    assert runner.run_mutants(["probe"]) == 0
    assert "1 mutants, 1 killed" in capsys.readouterr().out
