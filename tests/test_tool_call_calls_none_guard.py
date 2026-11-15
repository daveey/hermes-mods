"""Guard: never iterate ``args.get("calls", [])`` in plugin code.

Root cause (self-heal plugin-e90850b9da): ship-only-guard's execute_code scan resolves only literal keyword
values, so ``tool_call(calls=[{... p.name ...}])`` reached ``check_tool`` as ``{"calls": None}``. The ``.get``
default covers a missing key, not a present None, so the fail-closed pre_tool_call hook raised
``'NoneType' object is not iterable`` and blocked the agent's call. Write ``args.get("calls") or []`` and
check the type before iterating.

Run: ``python -m unittest tests/test_tool_call_calls_none_guard.py``.
"""
import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# .get("calls", []) / .get('calls', ()): a default that does not cover a present None.
PATTERN = re.compile(r"""\.get\(\s*["']calls["']\s*,\s*(\[\s*\]|\(\s*\))\s*\)""")


def _is_test(rel):
    parts = rel.split("/")
    return "tests" in parts[:-1] or parts[-1].startswith(("test_", "tests_")) or parts[-1] == "conftest.py"


def offenders():
    out = subprocess.run(["git", "ls-files", "*.py"], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    hits = []
    for rel in out.splitlines():
        if _is_test(rel) or rel.startswith("core/patches/"):
            continue
        path = ROOT / rel
        if not path.is_file():
            continue
        for n, line in enumerate(path.read_text(errors="replace").splitlines(), 1):
            if PATTERN.search(line):
                hits.append(f"{rel}:{n}: {line.strip()}")
    return hits


class CallsNoneGuard(unittest.TestCase):
    def test_pattern_matches_the_incident_shape(self):
        self.assertTrue(PATTERN.search('for call in args.get("calls", []):'))
        self.assertTrue(PATTERN.search("args.get('calls', ())"))
        self.assertFalse(PATTERN.search('args.get("calls") or []'))

    def test_no_default_only_calls_iteration(self):
        hits = offenders()
        self.assertEqual(hits, [], 'use args.get("calls") or [] and type-check before iterating:\n' + "\n".join(hits))


if __name__ == "__main__":
    unittest.main()
