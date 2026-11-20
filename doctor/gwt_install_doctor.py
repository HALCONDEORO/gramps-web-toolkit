#!/usr/bin/env python3
"""Offline, read-only install diagnostic for this toolkit.

Static analysis only: pyproject.toml is parsed with tomllib when available
(Python 3.11+; on 3.10 the result is reported as UNKNOWN), module files are
parsed with ast (never imported or executed), and external tools are looked up
with shutil.which. No network, no subprocess, no credentials, no writes.

Usage: gwt_install_doctor.py [--root DIR] [--json] [--strict]
Exit code: 0 ok, 1 errors found (or warnings with --strict), 2 fatal (unusable
root/pyproject, or TOML parser unavailable: distinguish via ``fatal_code`` /
``status`` in --json), 3 internal doctor failure (never a verdict).

Levels: "error" (definite fault), "warning", "unknown" (valid input that cannot
be verified statically, e.g. ``mod:obj.attr``, conditionally or dynamically
bound names, builds that rely on backend auto-detection). "unknown" never
makes the result fail. Files larger than MAX_BYTES, and inputs that exhaust the
parser (RecursionError/MemoryError), are reported as unusable, never as OK.
Every module file read is resolved and must lie inside --root (symlinks that
leave the root are ignored).
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import shutil
import sys
from pathlib import Path

try:  # tomllib is stdlib only on Python >= 3.11; the project requires >= 3.11
    import tomllib
except ImportError:  # pragma: no cover - depends on interpreter version
    tomllib = None

PARSER_UNAVAILABLE = "toml-parser-unavailable"
MAX_BYTES = 5 * 1024 * 1024

# Optional external executables and which feature wants them (informational).
OPTIONAL_TOOLS = {
    "node": "spanish-poller (Node.js helper)",
    "git": "version detection (hatch-vcs) when building",
    "tesseract": "OCR benchmarking (hand-run, not packaged)",
}

# import name -> extra that is expected to provide it
KNOWN_EXTRA_IMPORTS = {"playwright": "mh"}

_EXTRAS_RE = re.compile(r"^\[\s*(?:[A-Za-z0-9._-]+(?:\s*,\s*[A-Za-z0-9._-]+)*)?\s*\]$")


# ------------------------------------------------------------ safe helpers
def _text(v) -> str:
    """Always-JSON-safe text for any TOML value (datetime, float, table...)."""
    s = v if isinstance(v, str) else str(v)
    return s if len(s) <= 200 else s[:200] + "..."


def _table(parent, key: str) -> dict:
    v = parent.get(key) if isinstance(parent, dict) else None
    return v if isinstance(v, dict) else {}


def _strlist(v) -> list[str]:
    return [x for x in v if isinstance(x, str)] if isinstance(v, list) else []


def _read_limited(path: Path) -> str:
    if path.stat().st_size > MAX_BYTES:
        raise OSError(f"file larger than {MAX_BYTES} bytes")
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------- loading
def load_pyproject(root: Path) -> tuple[dict | None, str | None]:
    path = inside(root, "pyproject.toml")
    if path is None or not path.is_file():
        return None, "pyproject.toml not found"
    if tomllib is None:
        return None, (f"{PARSER_UNAVAILABLE}: pyproject.toml present but this Python "
                      f"({sys.version_info.major}.{sys.version_info.minor}) has no tomllib (needs 3.11+); "
                      "result is UNKNOWN, not a packaging fault. Re-run with Python 3.11+.")
    try:
        return tomllib.loads(_read_limited(path)), None
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        return None, f"pyproject.toml unreadable: {exc}"
    except (RecursionError, MemoryError) as exc:
        return None, f"pyproject.toml unusable: {type(exc).__name__} while parsing"


def _backend_opaque(data: dict) -> bool:
    backend = _table(data, "build-system").get("build-backend")
    return isinstance(backend, str) and not backend.startswith("hatchling")


def wheel_config(data: dict) -> dict:
    wheel = data
    for key in ("tool", "hatch", "build", "targets", "wheel"):
        wheel = _table(wheel, key)
    packages = _strlist(wheel.get("packages"))
    force = {k: v for k, v in _table(wheel, "force-include").items()
             if isinstance(k, str) and isinstance(v, str)}
    return {
        "packages": packages,
        "force_include": force,
        "dev_dirs": _strlist(wheel.get("dev-mode-dirs")),
        # wheel contents cannot be derived from config (auto-detection or a
        # non-hatchling backend): unreachable modules are UNKNOWN, not errors
        "opaque": _backend_opaque(data) or not (packages or force),
    }


def _valid_module(name) -> list[str] | None:
    """Dotted name whose every component is an identifier, else None."""
    if not isinstance(name, str):
        return None
    parts = name.split(".")
    return parts if all(p.isidentifier() for p in parts) else None


def parse_entry(spec) -> tuple[str, str] | None:
    """'mod:obj.attr [extras]' -> (mod, 'obj.attr'); None when malformed."""
    if not isinstance(spec, str):
        return None
    mod, sep, func = spec.partition(":")
    if "[" in func:
        func, bracket = func.split("[", 1)
        if not _EXTRAS_RE.match("[" + bracket.strip()):
            return None
    mod, func = mod.strip(), func.strip()
    if not sep or _valid_module(mod) is None or _valid_module(func) is None:
        return None
    return mod, func


# ------------------------------------------------------------ path helpers
def inside(root: Path, rel: str) -> Path | None:
    """Resolve rel under root; None if it escapes root (no parent reads)."""
    if Path(rel).is_absolute():
        return None
    try:
        target = (root / rel).resolve()
        target.relative_to(root.resolve())
    except (ValueError, OSError):
        return None
    return target


def _contained_file(root: Path, cand: Path) -> Path | None:
    """cand resolved (symlinks followed) if it is a regular file inside root."""
    try:
        real = cand.resolve()
        real.relative_to(root.resolve())
        return real if real.is_file() else None
    except (ValueError, OSError):
        return None


def module_file_in(base: Path, parts: list[str], root: Path | None = None) -> Path | None:
    """Find base/a/b.py or base/a/b/__init__.py for dotted parts, inside root."""
    if not parts or not all(isinstance(p, str) and p.isidentifier() for p in parts):
        return None
    root = root if root is not None else base
    leaf = base.joinpath(*parts)
    for cand in (leaf.with_name(leaf.name + ".py"), leaf / "__init__.py"):
        found = _contained_file(root, cand)
        if found:
            return found
    return None


def _force_include_hit(root: Path, src: str, dest: str, module: str, parts: list[str]) -> Path | None:
    spath = inside(root, src)
    if spath is None:
        return None
    if dest in (module + ".py", "/".join(parts) + ".py"):
        return _contained_file(root, spath)
    dparts = [p for p in dest.strip("/").split("/") if p not in ("", ".")]
    if dparts and len(parts) > len(dparts) and parts[:len(dparts)] == dparts and spath.is_dir():
        return module_file_in(spath, parts[len(dparts):], root)
    return None


def resolve_wheel(root: Path, cfg: dict, module: str) -> Path | None:
    """File that the built wheel would ship for this module, if any."""
    parts = _valid_module(module)
    if parts is None:
        return None
    for pkg in cfg["packages"]:
        pdir = inside(root, pkg)
        if pdir is not None and pdir.name == parts[0] and pdir.is_dir():
            found = module_file_in(pdir.parent, parts, root)
            if found:
                return found
    for src, dest in cfg["force_include"].items():
        hit = _force_include_hit(root, src, dest, module, parts)
        if hit:
            return hit
    return None


def resolve_dev(root: Path, cfg: dict, module: str) -> Path | None:
    """File an editable (dev-mode-dirs) install would expose."""
    parts = _valid_module(module)
    if parts is None:
        return None
    for d in cfg["dev_dirs"]:
        base = inside(root, d)
        if base is not None and base.is_dir():
            found = module_file_in(base, parts, root)
            if found:
                return found
    return None


# ------------------------------------------------------------ AST analysis
def parse_source(path: Path) -> tuple[ast.Module | None, str | None]:
    try:
        return ast.parse(_read_limited(path), filename=str(path)), None
    except (OSError, UnicodeDecodeError) as exc:
        return None, f"unreadable: {exc}"
    except (SyntaxError, ValueError) as exc:
        return None, f"syntax error: {exc}"
    except (RecursionError, MemoryError) as exc:
        return None, f"unusable: {type(exc).__name__} while parsing (module too complex)"


def _target_names(t) -> set[str]:
    if isinstance(t, ast.Name):
        return {t.id}
    if isinstance(t, ast.Starred):
        return _target_names(t.value)
    if isinstance(t, (ast.Tuple, ast.List)):
        return set().union(*(_target_names(e) for e in t.elts)) if t.elts else set()
    return set()


def _binds_name(node: ast.stmt, name: str) -> bool:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return node.name == name
    if isinstance(node, ast.Assign):
        return any(name in _target_names(t) for t in node.targets)
    if isinstance(node, ast.AnnAssign):  # a bare annotation does not bind
        return node.value is not None and name in _target_names(node.target)
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        return any((a.asname or a.name.split(".")[0]) == name for a in node.names)
    if isinstance(node, (ast.For, ast.AsyncFor)):
        return name in _target_names(node.target)
    if isinstance(node, (ast.With, ast.AsyncWith)):
        return any(i.optional_vars is not None and name in _target_names(i.optional_vars)
                   for i in node.items)
    return False


def _child_bodies(node: ast.stmt) -> list[list[ast.stmt]]:
    bodies = [getattr(node, a) for a in ("body", "orelse", "finalbody")
              if isinstance(getattr(node, a, None), list)]
    bodies.extend(h.body for h in getattr(node, "handlers", []))
    return bodies


def _scan_binding(tree: ast.Module, name: str) -> str | None:
    """'yes' if bound at top level, 'cond' if only in nested blocks, else None."""
    found = None
    stack = [(tree.body, False)]
    while stack:
        stmts, nested = stack.pop()
        for node in stmts:
            if _binds_name(node, name):
                if not nested:
                    return "yes"
                found = "cond"
            stack.extend((b, True) for b in _child_bodies(node)
                         if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)))
    return found


def _dynamic_module(tree: ast.Module) -> bool:
    """Star imports or a module __getattr__ can supply names we cannot see."""
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and any(a.name == "*" for a in node.names):
            return True
    return _scan_binding(tree, "__getattr__") == "yes"


def name_status(tree: ast.Module, name: str) -> str:
    """'yes' | 'cond' (conditionally bound) | 'dynamic' (unverifiable) | 'no'."""
    found = _scan_binding(tree, name)
    if found:
        return found
    return "dynamic" if _dynamic_module(tree) else "no"


def defines_name(tree: ast.Module, name: str) -> bool:
    return name_status(tree, name) == "yes"


def imported_roots(tree: ast.Module) -> set[str]:
    """Top-level names of absolute imports anywhere in the module."""
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.add(node.module.split(".")[0])
    return found


# ----------------------------------------------------------------- checks
def finding(level: str, code: str, msg: str, **extra) -> dict:
    return {"level": level, "code": code, "message": msg, **extra}


def check_script(root: Path, cfg: dict, name: str, spec) -> tuple[dict, set[str]]:
    """Return (report entry, third-party import roots seen in its module)."""
    entry = {"script": name, "target": _text(spec), "status": "ok", "issues": []}
    parsed = parse_entry(spec)
    if parsed is None:
        entry["status"] = "error"
        entry["issues"].append(finding("error", "bad-entry",
                                       "entry point must be a string 'module:function' "
                                       "(dotted identifiers, optional [extras])"))
        return entry, set()
    module, func = parsed
    wheel, dev = resolve_wheel(root, cfg, module), resolve_dev(root, cfg, module)
    src = wheel or dev
    if src is None:
        level, code = ("unknown", "module-unverifiable") if cfg.get("opaque") else ("error", "module-unreachable")
        entry["status"] = level
        entry["issues"].append(finding(level, code,
                                       f"module {module} not found via wheel packages, force-include or dev-mode-dirs"))
        return entry, set()
    entry["module_file"] = _rel(root, src)
    entry["in_wheel"] = wheel is not None
    return _inspect_module(root, entry, src, func, wheel is not None)


def _func_findings(root: Path, tree: ast.Module, src: Path, func: str) -> list[dict]:
    head = func.split(".")[0]
    status = name_status(tree, head)
    where = _rel(root, src)
    if status == "no":
        return [finding("error", "function-missing", f"{head} not statically defined in {where}")]
    if status == "cond":
        return [finding("unknown", "function-conditional", f"{head} is only bound conditionally in {where}; cannot verify")]
    if status == "dynamic":
        return [finding("unknown", "function-dynamic", f"{head} not found in {where}, which uses star imports or __getattr__; cannot verify")]
    if "." in func:
        return [finding("unknown", "attribute-unverified", f"{func}: attribute path is not verified statically")]
    return []


def _inspect_module(root: Path, entry: dict, src: Path, func: str, in_wheel: bool) -> tuple[dict, set[str]]:
    issues = entry["issues"]
    if not in_wheel:
        issues.append(finding("warning", "dev-only", "reachable only in editable installs; not shipped in the wheel"))
    tree, err = parse_source(src)
    if tree is None:
        issues.append(finding("error", "module-unparsable", err or "unparsable"))
    else:
        issues.extend(_func_findings(root, tree, src, func))
    entry["status"] = _worst(issues)
    return entry, (imported_roots(tree) if tree else set())


def _rel(root: Path, p: Path) -> str:
    try:
        return p.relative_to(root.resolve()).as_posix()
    except ValueError:
        return p.as_posix()


def _worst(issues: list[dict]) -> str:
    levels = {i["level"] for i in issues}
    for lvl in ("error", "warning", "unknown"):
        if lvl in levels:
            return lvl
    return "ok"


# --------------------------------------------------------- shape validation
_SHAPES = (
    (("project",), "table"),
    (("project", "scripts"), "strtable"),
    (("project", "dependencies"), "strlist"),
    (("project", "license-files"), "strlist"),
    (("project", "optional-dependencies"), "table"),
    (("build-system",), "table"),
    (("tool", "hatch", "build", "targets", "wheel", "packages"), "strlist"),
    (("tool", "hatch", "build", "targets", "wheel", "dev-mode-dirs"), "strlist"),
    (("tool", "hatch", "build", "targets", "wheel", "force-include"), "strtable"),
)


def _kind_problem(label: str, v, kind: str) -> str | None:
    if kind in ("table", "strtable"):
        if not isinstance(v, dict):
            return f"{label} must be a table"
        if kind == "strtable" and not all(isinstance(x, str) for x in v.values()):
            return f"{label} values must all be strings"
        return None
    if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
        return f"{label} must be a list of strings"
    return None


def _shape_problem(data: dict, path: tuple, kind: str) -> str | None:
    cur = data
    for i, key in enumerate(path):
        if not isinstance(cur, dict) or key not in cur:
            return None
        cur = cur[key]
        if i < len(path) - 1 and not isinstance(cur, dict):
            return f"{'.'.join(path[:i + 1])} must be a table"
    return _kind_problem(".".join(path), cur, kind)


def check_shapes(data: dict) -> list[dict]:
    """Explicit errors for wrong TOML types (never an AttributeError/TypeError)."""
    out = []
    for path, kind in _SHAPES:
        msg = _shape_problem(data, path, kind)
        if msg:
            out.append(finding("error", "bad-shape", msg, path=".".join(path)))
    for extra, reqs in _table(_table(data, "project"), "optional-dependencies").items():
        msg = _kind_problem(f"project.optional-dependencies.{extra}", reqs, "strlist")
        if msg:
            out.append(finding("error", "bad-shape", msg, path=f"project.optional-dependencies.{extra}"))
    return out


# --------------------------------------------------------------- packaging
def check_packaging(root: Path, cfg: dict, data: dict) -> list[dict]:
    out: list[dict] = []
    for pkg in cfg["packages"]:
        p = inside(root, pkg)
        if p is None or not p.is_dir():
            out.append(finding("error", "package-missing",
                               f"wheel package {pkg} is not a directory inside the root", path=pkg))
    for src, dest in cfg["force_include"].items():
        p = inside(root, src)
        if p is None or not p.exists():
            out.append(finding("error", "force-include-missing",
                               f"{src} -> {dest} source missing or outside the root", path=src))
    for d in cfg["dev_dirs"]:
        p = inside(root, d)
        if p is None or not p.is_dir():
            out.append(finding("warning", "dev-dir-missing", f"dev-mode dir {d} missing", path=d))
    out.extend(_check_project_files(root, data))
    return out


def _project_file_names(project: dict) -> list[str]:
    names = []
    for key in ("readme", "license"):
        v = project.get(key)
        if isinstance(v, dict):
            v = v.get("file")
        if isinstance(v, str) and (key == "readme" or isinstance(project.get(key), dict)):
            names.append(v)
    names.extend(_strlist(project.get("license-files")))
    return names


def _check_project_files(root: Path, data: dict) -> list[dict]:
    out: list[dict] = []
    for n in _project_file_names(_table(data, "project")):
        if any(c in n for c in "*?["):
            out.append(finding("unknown", "project-file-pattern", f"{n} is a glob pattern; not verified", path=n))
            continue
        p = inside(root, n)
        if p is None or not p.is_file():
            out.append(finding("warning", "project-file-missing", f"{n} referenced by [project] is missing", path=n))
    return out


def check_extras(project: dict, seen_imports: set[str]) -> list[dict]:
    """Optional extras: installation state is never probed, so it is 'unknown'."""
    result = []
    for extra, reqs in sorted(_table(project, "optional-dependencies").items()):
        reqs = reqs if isinstance(reqs, list) else []
        pkgs = [_req_name(r) for r in reqs if isinstance(r, str)]
        used = sorted(p for p in pkgs if p in seen_imports)
        result.append({
            "extra": extra,
            "requirements": [_text(r) for r in reqs],
            "installed": "unknown",
            "imported_by_scripts": used,
            "note": "not probed (no imports/subprocess); verify with `pip show`",
        })
    return result


def _req_name(req: str) -> str:
    name = req
    for ch in " ;[<>=!~(@":
        name = name.split(ch)[0]
    return name.strip().replace("-", "_").lower()


def check_unlisted_imports(project: dict, seen: set[str], local: set[str]) -> list[dict]:
    """Known-optional third-party imports that no declared extra provides."""
    declared = {_req_name(r) for r in _strlist(project.get("dependencies"))}
    for reqs in _table(project, "optional-dependencies").values():
        declared.update(_req_name(r) for r in _strlist(reqs))
    out = []
    for mod, extra in sorted(KNOWN_EXTRA_IMPORTS.items()):
        if mod in seen and mod not in declared and mod not in local:
            out.append(finding("warning", "undeclared-optional", f"{mod} is imported but not declared (expected extra '{extra}')"))
    return out


def _which_safe(name: str) -> str | None:
    """PATH lookup only for bare names (no probing of arbitrary paths)."""
    if not name or any(c in name for c in "/\\\0"):
        return None
    return shutil.which(name)


def check_tools(scripts: dict) -> dict:
    tools = {t: {"path": shutil.which(t), "used_for": why} for t, why in OPTIONAL_TOOLS.items()}
    for t in tools.values():
        t["status"] = "found" if t["path"] else "missing"
    names = [n for n in sorted(scripts) if _which_safe_name(n)]
    on_path = {n: _which_safe(n) for n in names}
    return {
        "optional_tools": tools,
        "console_scripts_on_path": {n: p for n, p in on_path.items() if p},
        "console_scripts_not_on_path": [n for n, p in on_path.items() if not p],
        "console_scripts_skipped": [n for n in sorted(scripts) if not _which_safe_name(n)],
        "note": "PATH lookup is informational; missing scripts just mean the package is not installed here",
    }


def _which_safe_name(name) -> bool:
    return isinstance(name, str) and bool(name) and not any(c in name for c in "/\\\0")


# ----------------------------------------------------------------- driver
def _fatal_report(root: Path, err: str | None) -> dict:
    unknown = bool(err) and err.startswith(PARSER_UNAVAILABLE)
    return {"root": str(root), "ok": False, "fatal": err, "scripts": [], "packaging": [],
            "fatal_code": PARSER_UNAVAILABLE if unknown else "pyproject-unusable",
            "status": "unknown" if unknown else "error",
            "extras": [], "tools": {},
            "summary": {"scripts": 0, "errors": 1, "warnings": 0, "unknown": 0, "unknown_extras": 0}}


def diagnose(root: Path) -> dict:
    root = root.resolve()
    data, err = load_pyproject(root)
    if data is None:
        return _fatal_report(root, err)
    cfg = wheel_config(data)
    project = _table(data, "project")
    scripts_cfg = _table(project, "scripts")
    reports, seen = [], set()
    for name in sorted(scripts_cfg):
        rep, imports = check_script(root, cfg, name, scripts_cfg[name])
        reports.append(rep)
        seen |= imports
    packaging = check_shapes(data) + check_packaging(root, cfg, data)
    local = {Path(d).name for d in cfg["packages"]}
    packaging += check_unlisted_imports(project, seen, local)
    return _assemble(root, reports, packaging, check_extras(project, seen), check_tools(scripts_cfg))


def _assemble(root: Path, reports: list[dict], packaging: list[dict], extras: list[dict], tools: dict) -> dict:
    all_issues = [i for r in reports for i in r["issues"]] + packaging
    count = {lvl: sum(1 for i in all_issues if i["level"] == lvl) for lvl in ("error", "warning", "unknown")}
    ok = count["error"] == 0
    status = "error" if not ok else "unknown" if count["unknown"] else "ok"
    return {"root": str(root), "ok": ok, "status": status, "scripts": reports, "packaging": packaging,
            "extras": extras, "tools": tools,
            "summary": {"scripts": len(reports), "errors": count["error"], "warnings": count["warning"],
                        "unknown": count["unknown"],
                        "unknown_extras": sum(1 for e in extras if e["installed"] == "unknown")}}


def render_human(rep: dict) -> str:
    if rep.get("fatal"):
        return f"install-doctor: FATAL {rep['fatal']} (root {rep['root']})"
    s = rep["summary"]
    lines = [f"install-doctor: {s['scripts']} console scripts, {s['errors']} errors, "
             f"{s['warnings']} warnings, {s.get('unknown', 0)} unverifiable, "
             f"{s['unknown_extras']} extras UNKNOWN"]
    for r in rep["scripts"]:
        for i in r["issues"]:
            lines.append(f"  [{i['level']}] {r['script']}: {i['message']}")
    for i in rep["packaging"]:
        lines.append(f"  [{i['level']}] packaging: {i['message']}")
    for e in rep["extras"]:
        lines.append(f"  [unknown] extra '{e['extra']}' ({', '.join(map(str, e['requirements']))}): install state not probed")
    for n, t in rep["tools"].get("optional_tools", {}).items():
        lines.append(f"  [tool] {n}: {t['path'] or 'not found'}")
    lines.append("result: " + ("OK" if rep["ok"] else "PROBLEMS FOUND"))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="gwt-install-doctor", description="Offline read-only install diagnostic.")
    ap.add_argument("--root", default=".", help="toolkit checkout containing pyproject.toml")
    ap.add_argument("--json", action="store_true", help="emit JSON instead of the human summary")
    ap.add_argument("--strict", action="store_true", help="treat warnings as failures")
    args = ap.parse_args(argv)
    try:
        rep = diagnose(Path(args.root))
        out = json.dumps(rep, indent=2, sort_keys=True, allow_nan=False) if args.json else render_human(rep)
    except Exception as exc:  # doctor bug: distinct from "problems found" (1)
        print(f"install-doctor: internal error: {type(exc).__name__}: {_text(exc)}", file=sys.stderr)
        return 3
    print(out)
    if rep.get("fatal"):
        return 2
    if not rep["ok"] or (args.strict and rep["summary"]["warnings"]):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
