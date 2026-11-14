"""merged_since selects PRs by merge time and pages until GitHub has no more.

Why (self-heal agent-017301de5e): a sweep used `gh pr list --state merged --limit 30`, which returns the newest-
*created* PRs, so a long-lived PR merged inside the window (#2344) was never checked.
Run: python3 -m pytest -q -p no:cacheprovider tests/test_merged_since.py
"""

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "merged_since.py"

# Fake `gh api -X GET search/issues -f q=... -f per_page=N -f page=P`: serves the PRs in state order, N per page.
FAKE_GH = textwrap.dedent(
    """\
    #!/usr/bin/env python3
    import json, os, sys
    state = json.load(open(os.environ["FAKE_GH_STATE"]))
    args = sys.argv[1:]
    with open(os.environ["FAKE_GH_CALLS"], "a") as f:
        f.write(json.dumps(args) + "\\n")
    fields = dict(args[i + 1].split("=", 1) for i, a in enumerate(args) if a in ("-f", "-F"))
    assert "search/issues" in args, args
    q = fields["q"]
    assert "is:merged" in q and "merged:>=" in q and "repo:o/r" in q, q
    per, page = int(fields.get("per_page", 30)), int(fields.get("page", 1))
    items = state["items"]
    print(json.dumps({"total_count": len(items), "incomplete_results": False,
                      "items": items[(page - 1) * per: page * per]}))
    """
)


def pr(n, created, merged):
    return {"number": n, "title": f"PR {n}", "created_at": created, "html_url": f"https://example.com/pull/{n}",
            "user": {"login": "you"}, "pull_request": {"merged_at": merged}}


class MergedSinceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        gh = d / "gh"
        gh.write_text(FAKE_GH)
        gh.chmod(0o755)
        self.state, self.calls = d / "state.json", d / "calls.jsonl"
        self.env = {**os.environ, "PATH": f"{d}{os.pathsep}{os.environ['PATH']}",
                    "FAKE_GH_STATE": str(self.state), "FAKE_GH_CALLS": str(self.calls)}

    def tearDown(self):
        self.tmp.cleanup()

    def test_long_lived_pr_merged_last_is_found_past_the_first_page(self):
        # 30 PRs created and merged inside the window, then #2344: created weeks earlier, merged last.
        items = [pr(3000 + i, f"2026-11-13T1{i // 10}:{i % 10}0:00Z", f"2026-11-13T15:{i:02d}:00Z")
                 for i in range(30)]
        items.append(pr(2344, "2026-10-20T00:00:00Z", "2026-11-13T16:00:00Z"))
        self.state.write_text(json.dumps({"items": items}))
        p = subprocess.run([sys.executable, str(SCRIPT), "--repo", "o/r", "--since", "2026-11-13T15:00:00Z",
                            "--per-page", "30"], capture_output=True, text=True, env=self.env, timeout=60)
        self.assertEqual(p.returncode, 0, p.stderr)
        out = json.loads(p.stdout)
        numbers = [x["number"] for x in out["prs"]]
        self.assertIn(2344, numbers)
        self.assertEqual(len(numbers), 31)
        self.assertEqual(out["window_end"], "2026-11-13T16:00:00Z")  # newest merge seen, not query time
        calls = [json.loads(line) for line in self.calls.read_text().splitlines()]
        self.assertGreaterEqual(len(calls), 2)

    def test_a_failed_page_fails_the_sweep(self):
        self.state.write_text("not json")
        p = subprocess.run([sys.executable, str(SCRIPT), "--repo", "o/r", "--since", "2026-11-13T15:00:00Z"],
                           capture_output=True, text=True, env=self.env, timeout=60)
        self.assertNotEqual(p.returncode, 0)
        self.assertEqual(p.stdout.strip(), "")


def load_merged_since():
    import importlib.util
    spec = importlib.util.spec_from_file_location("merged_regression", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_incomplete_results_refused(monkeypatch):
    import pytest
    from types import SimpleNamespace

    module = load_merged_since()
    monkeypatch.setattr(module.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(
        returncode=0, stdout=json.dumps({
            "total_count": 0, "items": [], "incomplete_results": True})))
    with pytest.raises(RuntimeError, match="incomplete_results"):
        module.page("q", 100, 1)


def test_cap_excess_refused(monkeypatch, capsys):
    module = load_merged_since()
    monkeypatch.setattr(module, "page", lambda *args: (1001, []))
    assert module.main(["--repo", "o/r", "--since", "2026-11-13T15:00:00Z"]) == 3
    assert capsys.readouterr().out == ""


def test_base_filter_reaches_query(monkeypatch):
    module = load_merged_since()
    queries = []

    def page(query, *args):
        queries.append(query)
        return 0, []

    monkeypatch.setattr(module, "page", page)
    assert module.main(["--repo", "o/r", "--since", "2026-11-13T15:00:00Z",
                        "--base", "state"]) == 0
    assert " base:state" in queries[0]


if __name__ == "__main__":
    unittest.main()
