"""Guard: a row-returning ``PRAGMA journal_mode=...`` must have its result read.

Root cause (throughput store, hermes-hub): ``conn.execute("PRAGMA journal_mode=WAL")`` returns a row. Left unread,
the statement stays active until CPython frees the cursor; if anything holds it a moment longer, the next COMMIT
on that connection fails with ``cannot commit transaction - SQL statements in progress``. Reading the result
(``.fetchone()``) finishes the statement at once.

Run: ``python -m pytest tests/test_sqlite_pragma_result_guard.py`` or ``python tests/test_sqlite_pragma_result_guard.py``.
"""
import ast
import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PRAGMA = re.compile(r"^\s*pragma\s+(?:\w+\.)?journal_mode\s*=", re.I)
EXCLUDE_PREFIX = ("core/patches/",)


def _tracked_py():
    out = subprocess.run(["git", "ls-files", "*.py"], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    return [f for f in out.splitlines() if not f.startswith(EXCLUDE_PREFIX) and not _is_test(f)]


def _is_test(rel):
    """Test fixtures open throwaway dbs; the guard covers the code that runs in a gateway."""
    parts = rel.split("/")
    return ("tests" in parts[:-1] or parts[-1].startswith(("test_", "tests_", "fake_"))
            or parts[-1] == "conftest.py")


def offenders(source):
    """Line numbers of ``<x>.execute("PRAGMA journal_mode=...")`` used as a bare statement (result discarded)."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    hits = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)):
            continue
        call = node.value
        if not (isinstance(call.func, ast.Attribute) and call.func.attr == "execute" and call.args):
            continue
        arg = call.args[0]
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str) and PRAGMA.match(arg.value):
            hits.append(node.lineno)
    return hits


class PragmaResultGuard(unittest.TestCase):
    def test_detector(self):
        self.assertEqual(offenders('c.execute("PRAGMA journal_mode=WAL")\n'), [1])
        self.assertEqual(offenders("db.execute('pragma main.journal_mode = wal')\n"), [1])
        self.assertEqual(offenders('c.execute("PRAGMA journal_mode=WAL").fetchone()\n'), [])
        self.assertEqual(offenders('mode = c.execute("PRAGMA journal_mode=WAL")\n'), [])
        self.assertEqual(offenders('c.execute("PRAGMA journal_mode")\n'), [])
        self.assertEqual(offenders('c.execute("PRAGMA synchronous=FULL")\n'), [])

    def test_no_unread_journal_mode_pragma(self):
        bad = []
        for rel in _tracked_py():
            path = ROOT / rel
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            if "journal_mode" not in text.lower():
                continue
            bad += [f"{rel}:{ln}" for ln in offenders(text)]
        self.assertEqual(bad, [], "read the result: .execute('PRAGMA journal_mode=WAL').fetchone() "
                                  "(an unread row leaves the statement active and the next COMMIT fails)")


if __name__ == "__main__":
    unittest.main()
