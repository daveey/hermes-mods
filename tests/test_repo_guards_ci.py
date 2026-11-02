"""The repo-wide guard tests in tests/ must run in CI for any change, including a plugin-only one.

A plugins/coach-only push once landed a bare SQLite mode=ro open on main: only per-plugin workflows (paths:
filtered) ran, and none of them ran tests/. This pins .github/workflows/repo-guards.yml to run tests/ on every
push to main and every PR, with no paths filter that could skip it.
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WF = ROOT / ".github" / "workflows" / "repo-guards.yml"


def _section(text, key):
    """Lines of the top-level ``on:`` block."""
    lines = text.splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.rstrip() == key + ":")
    out = []
    for ln in lines[start + 1:]:
        if ln and not ln.startswith((" ", "\t")):
            break
        out.append(ln)
    return out


class RepoGuardsWorkflow(unittest.TestCase):
    def setUp(self):
        self.assertTrue(WF.is_file(), f"missing {WF.relative_to(ROOT)}")
        self.text = WF.read_text()

    def test_triggers_on_every_push_and_pr(self):
        on = _section(self.text, "on")
        self.assertTrue(any(ln.strip() == "pull_request:" for ln in on), on)
        self.assertTrue(any(ln.strip() == "push:" for ln in on), on)
        self.assertTrue(any(re.fullmatch(r"\s*branches:\s*\[main\]", ln) for ln in on), on)
        self.assertFalse([ln for ln in on if re.match(r"\s*(paths|paths-ignore):", ln)],
                         "repo-guards must not be path-filtered: tests/ scans every plugin")

    def test_runs_whole_tests_dir_on_two_pythons(self):
        self.assertIn("-m unittest discover -v -s tests", self.text)
        self.assertRegex(self.text, r"python: \['3\.11', '3\.14'\]")

    def test_plugin_only_diff_selects_guard(self):
        """Dry run of GitHub's push filter for a plugins/coach-only diff: no paths filter => the job runs,
        and its discover over tests/ collects the sqlite mode=ro guard."""
        on = _section(self.text, "on")
        filtered = any(re.match(r"\s*paths:", ln) for ln in on)
        changed = ["plugins/coach/origin_authority.py"]
        self.assertTrue(not filtered and changed, "plugins/coach-only push would skip repo-guards")
        loader = unittest.TestLoader()
        names = {t.id() for s in loader.discover(str(ROOT / "tests"), top_level_dir=str(ROOT / "tests"))
                 for c in s for t in (c if isinstance(c, unittest.TestSuite) else [c])}
        self.assertIn("test_sqlite_ro_guard.SqliteRoGuard.test_no_bare_ro_open", names)


if __name__ == "__main__":
    unittest.main()
