"""Guard: plugin code that polls for Stop (``is_interrupted``) must also honour yield (``consume_yield``).

Root cause it prevents: run_in_user_terminal waited up to 10 min on the owner's terminal while the owner typed
in the chat. Core delivers a mid-turn chat message only after the running tool returns, and asks long tools to
return early with ``tools.interrupt.request_yield``. The plugin's wait loops checked Stop but never yield, so
the message sat unseen until the owner pressed Esc (self-heal agent-d01a5a5c1d). Any plugin tool that waits in
a loop long enough to check Stop waits long enough to need yield too.

Run: ``python -m pytest tests/test_yield_contract_guard.py`` or ``python tests/test_yield_contract_guard.py``.
"""
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Spelled in halves so this file does not trip its own scan.
STOP = re.compile(r"\bis_" + r"interrupted\b")
YIELD = re.compile(r"\bconsume_" + r"yield\b")
SCAN_PREFIX = ("plugins/", "tools/")


def _is_test(path: str) -> bool:
    name = Path(path).name
    return name.startswith(("test_", "tests_")) or name == "conftest.py" or "/tests/" in path


def _code(text: str) -> str:
    """Drop comment lines so prose that mentions the API is not counted."""
    return "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))


def violations(root: Path, files: list[str]) -> list[str]:
    bad = []
    for rel in files:
        if not rel.endswith(".py") or not rel.startswith(SCAN_PREFIX) or _is_test(rel):
            continue
        try:
            code = _code((root / rel).read_text(errors="replace"))
        except OSError:
            continue
        if STOP.search(code) and not YIELD.search(code):
            bad.append(rel)
    return bad


def _tracked() -> list[str]:
    return subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True,
                          check=True).stdout.splitlines()


class YieldContractGuard(unittest.TestCase):
    def test_every_stop_polling_plugin_also_yields(self):
        bad = violations(ROOT, _tracked())
        self.assertEqual(bad, [], "these files poll is_" "interrupted() but never consume_" "yield(): a chat "
                         "message waits behind their tool until it ends. Check tools.interrupt.consume_" "yield"
                         "(threading.current_thread().ident) in the same wait loop and return early with the "
                         "work left running (see plugins/user-terminal _yield_requested).")

    def test_guard_flags_a_stop_only_wait_loop(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "plugins" / "x").mkdir(parents=True)
            (root / "plugins" / "x" / "__init__.py").write_text(
                "from tools.interrupt import is_" "interrupted\nwhile not is_" "interrupted():\n    pass\n")
            (root / "plugins" / "y").mkdir()
            (root / "plugins" / "y" / "__init__.py").write_text(
                "from tools.interrupt import is_" "interrupted, consume_" "yield\n")
            (root / "plugins" / "x" / "tests_x.py").write_text("is_" "interrupted()\n")
            (root / "plugins" / "z.py").write_text("# is_" "interrupted is mentioned only in prose\n")
            files = ["plugins/x/__init__.py", "plugins/y/__init__.py", "plugins/x/tests_x.py", "plugins/z.py"]
            self.assertEqual(violations(root, files), ["plugins/x/__init__.py"])

    def test_user_terminal_is_in_scope(self):
        # The plugin that caused this guard must stay covered: it polls Stop, so the scan reads it.
        self.assertIn("plugins/user-terminal/__init__.py", _tracked())
        code = _code((ROOT / "plugins/user-terminal/__init__.py").read_text())
        self.assertTrue(STOP.search(code) and YIELD.search(code))


if __name__ == "__main__":
    unittest.main()
