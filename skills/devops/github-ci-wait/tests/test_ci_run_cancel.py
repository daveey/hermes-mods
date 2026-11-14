"""ci_run_cancel cancels a run only when the same call reads it still active AND on the attempt you diagnosed.

Why (self-heal agent-017301de5e): a hand `gh run cancel` whose only check was "status is queued" stopped attempt 3 of
a run its owner card had re-queued minutes earlier; the canceller had diagnosed attempt 2.
Run: python3 -m pytest -q -p no:cacheprovider tests/test_ci_run_cancel.py
"""

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "ci_run_cancel.py"

FAKE_GH = textwrap.dedent(
    """\
    #!/usr/bin/env python3
    import json, os, sys
    state_path = os.environ["FAKE_GH_STATE"]
    state = json.load(open(state_path))
    args = sys.argv[1:]
    with open(os.environ["FAKE_GH_CALLS"], "a") as f:
        f.write(json.dumps(args) + "\\n")
    if args[:1] != ["api"]:
        sys.exit(9)
    post = "POST" in args
    path = [a for a in args[1:] if a.startswith("repos/")][0]
    if state.get("fail_read") and not post:
        sys.stderr.write("HTTP 502\\n"); sys.exit(1)
    if post:
        state["cancels"] = state.get("cancels", 0) + 1
        if state.get("cancel_completes", True):
            state["run"]["status"] = "completed"; state["run"]["conclusion"] = "cancelled"
        json.dump(state, open(state_path, "w"))
        print("{}")
        sys.exit(0)
    print(json.dumps(state["run"]))
    """
)


class CiRunCancelTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        gh = d / "gh"
        gh.write_text(FAKE_GH)
        gh.chmod(0o755)
        self.state = d / "state.json"
        self.calls = d / "calls.jsonl"
        self.env = {**os.environ, "PATH": f"{d}{os.pathsep}{os.environ['PATH']}",
                    "FAKE_GH_STATE": str(self.state), "FAKE_GH_CALLS": str(self.calls)}

    def tearDown(self):
        self.tmp.cleanup()

    def run_cancel(self, run, *extra, expect=2, **state):
        self.state.write_text(json.dumps({"run": {"id": 9, "html_url": "https://example.com/r/9", **run}, **state}))
        p = subprocess.run([sys.executable, str(SCRIPT), "--run-id", "9", "--gh-repo", "o/r",
                            "--expect-attempt", str(expect), *extra],
                           capture_output=True, text=True, env=self.env, timeout=60)
        return p, json.loads(self.state.read_text())

    def posts(self):
        if not self.calls.exists():
            return []
        calls = [json.loads(line) for line in self.calls.read_text().splitlines()]
        return [c for c in calls if "POST" in c]

    def test_newer_attempt_is_refused_without_a_cancel_call(self):
        p, st = self.run_cancel({"status": "queued", "run_attempt": 3}, expect=2)
        self.assertEqual(p.returncode, 3, p.stdout + p.stderr)
        self.assertEqual(self.posts(), [])
        self.assertIn("attempt=3", p.stdout)
        self.assertIn("status=queued", p.stdout)

    def test_matching_attempt_cancels_exactly_once(self):
        p, st = self.run_cancel({"status": "queued", "run_attempt": 2}, "--reason", "stranded", expect=2)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        posts = self.posts()
        self.assertEqual(len(posts), 1)
        self.assertTrue(posts[0][-1].endswith("repos/o/r/actions/runs/9/cancel"), posts)
        self.assertIn("conclusion=cancelled", p.stdout)

    def test_completed_run_is_refused(self):
        p, _ = self.run_cancel({"status": "completed", "conclusion": "success", "run_attempt": 1}, expect=1)
        self.assertEqual(p.returncode, 3)
        self.assertEqual(self.posts(), [])

    def test_failed_read_cancels_nothing(self):
        p, _ = self.run_cancel({"status": "queued", "run_attempt": 1}, expect=1, fail_read=True)
        self.assertEqual(p.returncode, 2)
        self.assertEqual(self.posts(), [])

    def test_force_uses_force_cancel_endpoint(self):
        p, _ = self.run_cancel({"status": "queued", "run_attempt": 1}, "--force", expect=1)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertTrue(self.posts()[0][-1].endswith("/runs/9/force-cancel"))

    def test_request_that_does_not_settle_is_reported_not_done(self):
        p, _ = self.run_cancel({"status": "queued", "run_attempt": 1}, "--settle-seconds", "0", expect=1,
                               cancel_completes=False)
        self.assertEqual(p.returncode, 4, p.stdout + p.stderr)
        self.assertEqual(len(self.posts()), 1)
        self.assertIn("--force", p.stdout)


# In-process tests control the clock and successive reads without waiting on GitHub.
def load_cancel():
    import importlib.util
    spec = importlib.util.spec_from_file_location("cancel_regression", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def cancel_args():
    return ["--run-id", "9", "--gh-repo", "o/r", "--expect-attempt", "2"]


def test_postread_attempt_moves(monkeypatch):
    module = load_cancel()
    reads = iter([
        {"status": "queued", "attempt": 2, "conclusion": None, "url": ""},
        {"status": "completed", "attempt": 3, "conclusion": "cancelled", "url": ""},
    ])
    monkeypatch.setattr(module, "read_run", lambda *args: next(reads))
    monkeypatch.setattr(module, "gh", lambda *args: "{}")
    assert module.main(cancel_args()) == 4


def test_polling_wait_actually_retries(monkeypatch):
    module = load_cancel()
    queued = {"status": "queued", "attempt": 2, "conclusion": None, "url": ""}
    reads = iter([queued, queued, {**queued, "status": "completed"}])
    sleeps = []
    monkeypatch.setattr(module, "read_run", lambda *args: next(reads))
    monkeypatch.setattr(module, "gh", lambda *args: "{}")
    monkeypatch.setattr(module.time, "monotonic", lambda: 10**12)
    monkeypatch.setattr(module.time, "sleep", sleeps.append)
    assert module.main(cancel_args() + ["--settle-seconds", "10"]) == 0
    assert sleeps == [3]


def test_post_error_is_reported(monkeypatch, capsys):
    module = load_cancel()
    monkeypatch.setattr(module, "read_run", lambda *args: {
        "status": "queued", "attempt": 2, "conclusion": None, "url": ""})

    def fail(*args):
        raise module.GhError("HTTP 502")

    monkeypatch.setattr(module, "gh", fail)
    assert module.main(cancel_args()) == 2
    assert "cancel request failed" in capsys.readouterr().err


def test_readback_error_is_reported(monkeypatch, capsys):
    module = load_cancel()
    calls = []

    def read(*args):
        calls.append(args)
        if len(calls) > 1:
            raise module.GhError("HTTP 502")
        return {"status": "queued", "attempt": 2, "conclusion": None, "url": ""}

    monkeypatch.setattr(module, "read_run", read)
    monkeypatch.setattr(module, "gh", lambda *args: "{}")
    assert module.main(cancel_args()) == 2
    assert "read-back failed" in capsys.readouterr().err


if __name__ == "__main__":
    unittest.main()
