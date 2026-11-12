"""Guard: a plugin declaring ``dashboard_reloads_siblings: true`` in plugin.yaml really reloads its siblings.

hermes-sync restarts the always-on dashboard when a plugin's code arrives after the dashboard started, because the
dashboard mounts ``dashboard/plugin_api.py`` once. For a plugin with this declaration it restarts only for changes
under ``dashboard/``: the declaration says plugin_api.py loads every sibling module by file path and reloads it when
the file's mtime changes, so a sibling-only commit is live without a restart (state docs/plugin-services.md).

That claim is checked here statically for ``dashboard/plugin_api.py``: no ``import``/``from`` of a sibling module,
and every function that path-loads a module (``spec_from_file_location``) consults an mtime. A loader that caches a
module for the process lifetime (``if name not in sys.modules: ...`` with no mtime) must drop the declaration.

Run: ``python -m pytest tests/test_dashboard_reload_declaration.py``.
"""
import ast
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KEY = re.compile(r"^dashboard_reloads_siblings:[ \t]*([^\s#]+)[ \t]*(?:#.*)?$", re.M)


def declared(plugin_dir: Path) -> bool:
    """The same literal-``true`` reading hermes-sync's dashboard_reloads_siblings() uses."""
    try:
        m = KEY.search((plugin_dir / "plugin.yaml").read_text())
    except OSError:
        return False
    return bool(m) and m.group(1).strip("\"'") == "true"


def offenders(source: str, siblings: set) -> list:
    """Why ``source`` (a plugin_api.py) could keep a stale sibling after a pull; empty when it cannot."""
    tree = ast.parse(source)
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out += [f"line {node.lineno}: import {a.name}" for a in node.names if a.name.split(".")[0] in siblings]
        elif isinstance(node, ast.ImportFrom) and (node.level or (node.module or "").split(".")[0] in siblings):
            out.append(f"line {node.lineno}: from {'.' * node.level}{node.module or ''} import ...")
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = ast.get_source_segment(source, fn) or ""
        if "spec_from_file_location" in body and not re.search(r"\bst_mtime(_ns)?\b", body):
            out.append(f"line {fn.lineno}: {fn.name}() path-loads a module without an mtime check")
    # Module-level path loads run once per process; only the stateless loader bootstrap (a helper that itself
    # reloads by mtime, e.g. coach's module_cache) is allowed there.
    for node in tree.body:
        seg = ast.get_source_segment(source, node) or ""
        if "spec_from_file_location" in seg and not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                                                                      ast.ClassDef)):
            if "module_cache" not in seg:
                out.append(f"line {node.lineno}: module-level path load of a sibling (loaded once)")
    return out


class DashboardReloadDeclaration(unittest.TestCase):
    def test_declared_plugins_reload_their_siblings(self):
        checked = 0
        for plugin in sorted((ROOT / "plugins").iterdir()):
            if not plugin.is_dir() or not declared(plugin):
                continue
            api = plugin / "dashboard" / "plugin_api.py"
            self.assertTrue(api.is_file(), f"{plugin.name}: declares dashboard_reloads_siblings without "
                                           "dashboard/plugin_api.py")
            siblings = {p.stem for p in plugin.glob("*.py")} | {p.name for p in plugin.iterdir() if p.is_dir()}
            siblings -= {"dashboard", "tests", "docs", "__pycache__"}
            bad = offenders(api.read_text(), siblings)
            self.assertEqual(bad, [], f"{plugin.name}: dashboard_reloads_siblings: true, but plugin_api.py can keep "
                                      "a stale sibling; reload it by mtime or drop the declaration")
            checked += 1
        self.assertGreater(checked, 0, "no plugin declares dashboard_reloads_siblings (expected coach et al.)")

    def test_checker_catches_a_process_lifetime_cache(self):
        # runners' _fleet() shape: loaded once, never reloaded.
        once = ("def _fleet():\n    if name not in sys.modules:\n"
                "        spec = importlib.util.spec_from_file_location(name, path)\n"
                "        spec.loader.exec_module(importlib.util.module_from_spec(spec))\n")
        self.assertTrue(offenders(once, {"fleet"}))
        self.assertTrue(offenders("import fleet\n", {"fleet"}))
        self.assertTrue(offenders("from . import fleet\n", {"fleet"}))
        self.assertTrue(offenders("spec = importlib.util.spec_from_file_location('f', HERE / 'fleet.py')\n",
                                  {"fleet"}))
        fresh = ("def _load(name):\n    mtime = path.stat().st_mtime_ns\n"
                 "    spec = importlib.util.spec_from_file_location(name, path)\n")
        self.assertEqual(offenders(fresh, {"fleet"}), [])

    def test_only_literal_true_declares(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            for text, want in (("dashboard_reloads_siblings: true  # why\n", True),
                               ("dashboard_reloads_siblings: yes\n", False),
                               ("dashboard_reload_siblings: true\n", False)):
                (p / "plugin.yaml").write_text("name: x\n" + text)
                self.assertIs(declared(p), want, text)


if __name__ == "__main__":
    unittest.main()
