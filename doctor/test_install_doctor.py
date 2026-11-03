"""Synthetic tempdir tests for gwt_install_doctor (no network, no subprocess)."""
import ast
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gwt_install_doctor as d  # noqa: E402

PYPROJECT = """
[project]
name = "x"
readme = "README.md"
dependencies = ["requests>=2"]
[project.optional-dependencies]
mh = ["playwright>=1.40"]
[project.scripts]
t-pkg = "tpkg.cli:main"
t-one = "tone:main"
t-dev = "tdev:main"
t-gone = "nothere:main"
t-nofn = "tone:absent"
t-bad = "nocolon"
[tool.hatch.build.targets.wheel]
packages = ["a/tpkg"]
dev-mode-dirs = ["a", "b", "missingdir"]
[tool.hatch.build.targets.wheel.force-include]
"b/tone.py" = "tone.py"
"b/ghost.py" = "ghost.py"
"""


def build(root: Path) -> None:
    (root / "a" / "tpkg").mkdir(parents=True)
    (root / "b").mkdir()
    (root / "pyproject.toml").write_text(PYPROJECT)
    (root / "a" / "tpkg" / "__init__.py").write_text("")
    (root / "a" / "tpkg" / "cli.py").write_text("def main():\n    pass\n")
    (root / "b" / "tone.py").write_text("import playwright\ndef main():\n    pass\n")
    (root / "a" / "tdev.py").write_text("main = lambda: 0\n")


@unittest.skipUnless(
    getattr(d, "tomllib", None) is not None,
    "tomllib unavailable (Python < 3.11): real TOML diagnosis is not supported; "
    "the doctor reports UNKNOWN instead (covered by NoParserTests)",
)
class DoctorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        build(self.root)
        self.rep = d.diagnose(self.root)
        self.by = {s["script"]: s for s in self.rep["scripts"]}

    def tearDown(self):
        self.tmp.cleanup()

    def codes(self, script):
        return {i["code"] for i in self.by[script]["issues"]}

    def test_ok_scripts(self):
        self.assertEqual(self.by["t-pkg"]["status"], "ok")
        self.assertEqual(self.by["t-one"]["status"], "ok")

    def test_dev_only_warning(self):
        self.assertEqual(self.by["t-dev"]["status"], "warning")
        self.assertIn("dev-only", self.codes("t-dev"))

    def test_unreachable_missing_function_bad_entry(self):
        self.assertIn("module-unreachable", self.codes("t-gone"))
        self.assertIn("function-missing", self.codes("t-nofn"))
        self.assertIn("bad-entry", self.codes("t-bad"))

    def test_packaging_findings(self):
        codes = {p["code"] for p in self.rep["packaging"]}
        self.assertIn("force-include-missing", codes)
        self.assertIn("dev-dir-missing", codes)
        self.assertIn("project-file-missing", codes)

    def test_extras_unknown(self):
        extra = self.rep["extras"][0]
        self.assertEqual(extra["installed"], "unknown")
        self.assertEqual(extra["imported_by_scripts"], ["playwright"])

    def test_not_ok_and_summary(self):
        self.assertFalse(self.rep["ok"])
        self.assertEqual(self.rep["summary"]["unknown_extras"], 1)

    def test_never_executes_code(self):
        (self.root / "b" / "tone.py").write_text("raise SystemExit(9)\ndef main(): pass\n")
        # Select by script name: report order is sorted, so indexing is fragile.
        # The module would exit if executed; static AST analysis must not run it.
        rep = d.diagnose(self.root)
        by = {s["script"]: s for s in rep["scripts"]}
        self.assertEqual(by["t-one"]["status"], "ok")
        self.assertEqual(by["t-one"]["issues"], [])
        # t-nofn also targets tone.py but asks for a missing function: still an error.
        self.assertIn("function-missing", {i["code"] for i in by["t-nofn"]["issues"]})

    def test_syntax_error(self):
        (self.root / "b" / "tone.py").write_text("def (:\n")
        rep = d.diagnose(self.root)
        self.assertIn("module-unparsable", {i["code"] for s in rep["scripts"] for i in s["issues"]})

    def test_main_json_and_human(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = d.main(["--root", str(self.root), "--json"])
        self.assertEqual(code, 1)
        self.assertIn("tools", json.loads(buf.getvalue()))
        buf = io.StringIO()
        with redirect_stdout(buf):
            d.main(["--root", str(self.root)])
        self.assertIn("UNKNOWN", buf.getvalue())



class NoParserTests(unittest.TestCase):
    """Run on every Python version (including 3.10, which has no tomllib)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        build(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def test_path_escape_rejected(self):
        self.assertIsNone(d.inside(self.root, "../x"))
        self.assertIsNone(d.inside(self.root, "/etc/passwd"))

    def test_missing_pyproject(self):
        with tempfile.TemporaryDirectory() as t:
            rep = d.diagnose(Path(t))
        self.assertIn("fatal", rep)
        self.assertIn("FATAL", d.render_human(rep))

    def test_main_fatal_exit(self):
        with tempfile.TemporaryDirectory() as t, redirect_stdout(io.StringIO()):
            self.assertEqual(d.main(["--root", t]), 2)

    def test_req_name(self):
        self.assertEqual(d._req_name("Play-Wright[x]>=1; python_version>'3'"), "play_wright")

    def test_inside_absolute_and_relative(self):
        self.assertIsNone(d.inside(self.root, str(self.root / "b")))
        self.assertEqual(d.inside(self.root, "b"), (self.root / "b").resolve())

    def test_parse_entry(self):
        self.assertEqual(d.parse_entry(" a.b : main "), ("a.b", "main"))
        self.assertIsNone(d.parse_entry("tone:"))
        self.assertIsNone(d.parse_entry(":main"))
        self.assertIsNone(d.parse_entry("nocolon"))

    def test_defines_name(self):
        tree = ast.parse("async def a(): pass\nclass C: pass\nimport os.path as o\nimport sys\nx: int = 1\n")
        for name in ("a", "C", "o", "sys", "x"):
            self.assertTrue(d.defines_name(tree, name), name)
        self.assertFalse(d.defines_name(tree, "os"))
        self.assertFalse(d.defines_name(tree, "missing"))

    def test_imported_roots(self):
        tree = ast.parse("import a.b\nfrom c.d import e\nfrom . import f\nfrom .rel import g\n")
        self.assertEqual(d.imported_roots(tree), {"a", "c"})

    def test_req_name_url_marker(self):
        self.assertEqual(d._req_name("Pkg-X@https://example.invalid/x.whl"), "pkg_x")

    def test_worst(self):
        w, e = {"level": "warning"}, {"level": "error"}
        self.assertEqual(d._worst([w, e]), "error")
        self.assertEqual(d._worst([w]), "warning")
        self.assertEqual(d._worst([]), "ok")

    def test_check_extras_lists_only_imported(self):
        project = {"optional-dependencies": {"mh": ["playwright>=1", "other"]}}
        extra = d.check_extras(project, {"playwright"})[0]
        self.assertEqual(extra["imported_by_scripts"], ["playwright"])

    def test_check_unlisted_imports(self):
        def codes(project, local=frozenset()):
            return [f["code"] for f in d.check_unlisted_imports(project, {"playwright"}, set(local))]
        self.assertEqual(codes({}), ["undeclared-optional"])
        self.assertEqual(codes({"dependencies": ["playwright"]}), [])
        self.assertEqual(codes({"optional-dependencies": {"mh": ["playwright"]}}), [])
        self.assertEqual(codes({}, {"playwright"}), [])

    def test_symlink_leaving_root_ignored(self):
        outside = self.root.parent / (self.root.name + "-evil.py")
        outside.write_text("def main(): pass\n")
        try:
            (self.root / "m.py").symlink_to(outside)
            self.assertIsNone(d.module_file_in(self.root, ["m"]))
        finally:
            outside.unlink()

    def test_dynamic_names_are_unverifiable(self):
        self.assertEqual(d.name_status(ast.parse("from o import *\n"), "main"), "dynamic")
        self.assertEqual(d.name_status(ast.parse("def __getattr__(n): return 1\n"), "main"), "dynamic")
        self.assertEqual(d.name_status(ast.parse("import os\n"), "main"), "no")

    def test_shape_problems(self):
        self.assertEqual([p["code"] for p in d.check_shapes({"project": {"scripts": {"a": 1}}})], ["bad-shape"])
        self.assertTrue(d.check_shapes({"tool": 1}))
        self.assertEqual(d.check_shapes({"project": {"scripts": {"a": "m:f"}}}), [])

    def test_toml_parser_unavailable(self):
        saved = d.tomllib
        d.tomllib = None  # simulate Python 3.10
        try:
            rep = d.diagnose(self.root)
            with redirect_stdout(io.StringIO()) as buf:
                code = d.main(["--root", str(self.root)])
        finally:
            d.tomllib = saved
        self.assertEqual(code, 2)
        self.assertEqual(rep["fatal_code"], "toml-parser-unavailable")
        self.assertEqual(rep["status"], "unknown")
        self.assertFalse(rep["ok"])
        self.assertIn("UNKNOWN", rep["fatal"])
        self.assertNotIn("No module named", rep["fatal"])
        self.assertIn("FATAL", buf.getvalue())


def _mini(root: Path, pyproject: str, files: dict | None = None) -> None:
    (root / "pyproject.toml").write_text(pyproject)
    for rel, text in (files or {}).items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)


class EntryParsingTests(unittest.TestCase):
    def test_rejects_non_identifier_components(self):
        for bad in ("/tmp/x:f", ".:f", "..:f", "a..b:f", "a/b:f", "a\\b:f", "a\0b:f",
                    "m:", "m:a..b", "m:a:b", "m:f [", 5, None, ["a:b"]):
            self.assertIsNone(d.parse_entry(bad), repr(bad))

    def test_accepts_attr_and_extras(self):
        self.assertEqual(d.parse_entry("m.n:obj.attr"), ("m.n", "obj.attr"))
        self.assertEqual(d.parse_entry("m:main [x, y]"), ("m", "main"))

    def test_module_file_in_rejects_bad_parts(self):
        with tempfile.TemporaryDirectory() as t:
            base = Path(t)
            self.assertIsNone(d.module_file_in(base, ["/etc/passwd"]))
            self.assertIsNone(d.module_file_in(base, ["", ""]))
            self.assertIsNone(d.module_file_in(base, []))


@unittest.skipUnless(getattr(d, "tomllib", None) is not None, "needs tomllib")
class RobustnessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.root = self.base / "root"
        self.root.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def run_doctor(self, pyproject, files=None):
        _mini(self.root, pyproject, files)
        return d.diagnose(self.root)

    def test_absolute_module_outside_root_never_read(self):
        secret = self.base / "secret.py"
        secret.write_text("import leaked_marker\ndef main(): pass\n")
        spec = str(self.base / "secret") + ":main"
        seen = []
        orig = d.parse_source
        d.parse_source = lambda p: (seen.append(p), orig(p))[1]
        try:
            rep = self.run_doctor(
                f'[project.scripts]\nx = "{spec}"\n[tool.hatch.build.targets.wheel]\n'
                'dev-mode-dirs = ["."]\n')
        finally:
            d.parse_source = orig
        self.assertEqual(seen, [])
        self.assertEqual(rep["scripts"][0]["status"], "error")
        self.assertNotIn("leaked_marker", json.dumps(rep))

    def test_dot_module_no_parent_read(self):
        (self.base / "root.py").write_text("def main(): pass\n")
        rep = self.run_doctor('[project.scripts]\nx = ".:main"\n[tool.hatch.build.targets.wheel]\n'
                              'dev-mode-dirs = ["."]\n')
        self.assertEqual({i["code"] for i in rep["scripts"][0]["issues"]}, {"bad-entry"})

    def test_symlinked_module_outside_root_ignored(self):
        outside = self.base / "evil.py"
        outside.write_text("def main(): pass\n")
        (self.root / "m.py").symlink_to(outside)
        rep = self.run_doctor('[project.scripts]\nx = "m:main"\n[tool.hatch.build.targets.wheel]\n'
                              'dev-mode-dirs = ["."]\npackages = ["."]\n')
        self.assertNotIn("module_file", rep["scripts"][0])
        self.assertEqual(rep["scripts"][0]["status"], "error")

    def test_wrong_shapes_give_errors_not_crashes(self):
        for toml in ('tool = 1\n', 'project = "x"\n', '[project]\nscripts = ["a"]\n',
                     '[project]\noptional-dependencies = []\n',
                     '[tool.hatch.build.targets.wheel]\npackages = 5\ndev-mode-dirs = 5\n',
                     '[tool.hatch.build.targets.wheel]\nforce-include = ["a"]\n',
                     '[tool.hatch.build.targets.wheel]\npackages = "core"\n',
                     '[project.optional-dependencies]\ne = [1]\n'):
            with self.subTest(toml=toml):
                (self.root / "pyproject.toml").write_text(toml)
                rep = d.diagnose(self.root)
                self.assertTrue(d.render_human(rep))
                json.dumps(rep, allow_nan=False)
                if "tool = 1" not in toml:
                    self.assertIn("bad-shape", {p["code"] for p in rep["packaging"]})

    def test_datetime_and_nan_json_safe(self):
        rep = self.run_doctor('[project.scripts]\nx = 1979-05-27\n'
                              '[project.optional-dependencies]\ne = [1979-05-27, nan]\n')
        json.dumps(rep, allow_nan=False)
        self.assertIn("bad-entry", {i["code"] for i in rep["scripts"][0]["issues"]})
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = d.main(["--root", str(self.root), "--json"])
        self.assertEqual(code, 1)
        json.loads(buf.getvalue())

    def test_recursion_unusable_not_ok(self):
        rep = self.run_doctor('a = ' + '[' * 100000 + ']' * 100000 + '\n')
        self.assertFalse(rep["ok"])
        self.assertEqual(rep["fatal_code"], "pyproject-unusable")

    def test_ast_recursion_is_unusable(self):
        src = "x = " + "+".join(["1"] * 100000) + "\ndef main(): pass\n"
        rep = self.run_doctor('[project.scripts]\nx = "m:main"\n[tool.hatch.build.targets.wheel]\n'
                              'dev-mode-dirs = ["."]\n', {"m.py": src})
        self.assertNotEqual(rep["scripts"][0]["status"], "ok")
        self.assertIn("module-unparsable", {i["code"] for i in rep["scripts"][0]["issues"]})

    def test_dynamic_targets_are_unknown_not_error(self):
        cases = {
            "obj.attr": "class obj: attr = 1\n",
            "main [x]": "def main(): pass\n",
            "main": "try:\n    from x import main\nexcept ImportError:\n    main = None\n",
            "main ": "import sys\nif sys.platform:\n    def main(): pass\n",
            "main  ": "from other import *\n",
            "main   ": "def __getattr__(n): return 1\n",
        }
        for target, src in cases.items():
            with self.subTest(target=target):
                rep = self.run_doctor(
                    f'[project.scripts]\nx = "m:{target}"\n[tool.hatch.build.targets.wheel]\n'
                    'dev-mode-dirs = ["."]\n', {"m.py": src})
                st = rep["scripts"][0]["status"]
                if target == "main [x]":
                    self.assertEqual(st, "warning")  # dev-only warning only
                else:
                    self.assertIn(st, ("unknown", "warning"))
                self.assertNotIn("error", {x["level"] for x in rep["scripts"][0]["issues"]})
                self.assertTrue(rep["ok"] or rep["summary"]["errors"] == 0)

    def test_bare_annotation_is_not_defined(self):
        tree = ast.parse("main: int\n")
        self.assertEqual(d.name_status(tree, "main"), "no")

    def test_namespace_package_and_dir_force_include(self):
        rep = self.run_doctor(
            '[project.scripts]\na = "ns.mod:main"\nb = "fi.sub:main"\n'
            '[tool.hatch.build.targets.wheel]\npackages = ["src/ns"]\n'
            '[tool.hatch.build.targets.wheel.force-include]\n"extra" = "fi"\n',
            {"src/ns/mod.py": "def main(): pass\n", "extra/sub.py": "def main(): pass\n"})
        self.assertEqual({s["script"]: s["status"] for s in rep["scripts"]}, {"a": "ok", "b": "ok"})
        self.assertEqual(rep["packaging"], [])

    def test_main_internal_error_exit_3(self):
        orig = d.diagnose
        d.diagnose = lambda root: (_ for _ in ()).throw(RuntimeError("boom"))
        try:
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.assertEqual(d.main(["--root", str(self.root)]), 3)
        finally:
            d.diagnose = orig


if __name__ == "__main__":
    unittest.main()
