"""wait_for_ci.py --run-id/--expect: an exact-run wait never passes on an empty, missing or skipped manifest.

A hand-written waiter for five dispatched validate.yml runs filtered `gh run list` by a guessed createdAt cutoff,
matched nothing, and exited 0 printing `{}` while all five were still queued (self-heal agent-829ab754ac). These
tests run the real script as a subprocess with a fake `gh` first on PATH (the path an agent's background job takes).
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "wait_for_ci.py"

# Fake gh: `gh run view ID --json ... [-R slug]` prints RUNS[ID][n] where n is how many times ID was viewed before
# (the last entry repeats), or exits 1 with gh's own not-found text. Every call is appended to CALLS.
FAKE_GH = r'''#!/usr/bin/env python3
import json, os, sys
a = sys.argv[1:]
d = os.environ["FAKE_GH_DIR"]
with open(os.path.join(d, "calls"), "a") as f:
    f.write(json.dumps(a) + "\n")
if a[:2] != ["run", "view"]:
    sys.exit(f"fake gh: unexpected {a}")
rid = a[2]
runs = json.load(open(os.path.join(d, "runs.json")))
if rid not in runs:
    sys.stderr.write(f"could not find any workflow run with ID {rid}\n")
    sys.exit(1)
seen_p = os.path.join(d, "seen-" + rid)
n = int(open(seen_p).read()) if os.path.exists(seen_p) else 0
open(seen_p, "w").write(str(n + 1))
seq = runs[rid]
print(json.dumps(seq[min(n, len(seq) - 1)]))
'''


def job(name, conclusion="success", status="completed"):
    return {"name": name, "status": status, "conclusion": conclusion if status == "completed" else None,
            "url": f"https://github.com/o/r/actions/runs/1/job/{name}"}


def run(rid, jobs, status="completed", conclusion="success", head="a" * 40, branch="w12"):
    return {"databaseId": int(rid), "status": status, "conclusion": conclusion if status == "completed" else "",
            "headSha": head, "headBranch": branch, "workflowName": "validate", "event": "workflow_dispatch",
            "url": f"https://github.com/o/r/actions/runs/{rid}", "jobs": jobs}


class ExactRunWaitTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="wfc-")
        gh = Path(self.dir) / "gh"
        gh.write_text(FAKE_GH)
        gh.chmod(0o755)
        self.runs = {}

    def wait(self, *args):
        (Path(self.dir) / "runs.json").write_text(json.dumps(self.runs))
        env = dict(os.environ, PATH=f"{self.dir}:{os.environ.get('PATH', '')}", FAKE_GH_DIR=self.dir)
        p = subprocess.run([sys.executable, "-B", str(SCRIPT), "--gh-repo", "o/r", "--interval", "0", *args],
                           capture_output=True, text=True, env=env, cwd=self.dir, timeout=60)
        return p.returncode, p.stdout + p.stderr

    def calls(self):
        f = Path(self.dir) / "calls"
        return [json.loads(x) for x in f.read_text().splitlines()] if f.exists() else []

    # ── the incident: nothing to watch is a failure, never "done" ──
    def test_empty_manifest_exits_1(self):
        rc, out = self.wait("--run-id", "")
        self.assertEqual(rc, 1, out)
        self.assertIn("no runs to wait on", out)
        self.assertEqual(self.calls(), [])

    def test_run_mode_with_no_ids_at_all_exits_1(self):
        rc, out = self.wait("--once", "--timeout-min", "0")
        self.assertEqual(rc, 1, out)
        self.assertIn("no runs to wait on", out)

    def test_missing_run_id_exits_1(self):
        self.runs = {"41967431576": [run("41967431576", [job("ios")])]}
        rc, out = self.wait("--run-id", "41967431576", "--run-id", "41967453708")
        self.assertEqual(rc, 1, out)
        self.assertIn("41967453708", out)
        self.assertIn("not found", out)

    def test_unexpected_skip_exits_1(self):
        # The run succeeded, but the job the experiment exists for never ran: a skip is not a pass.
        self.runs = {"11": [run("11", [job("plan"), job("native-guard", "skipped")])]}
        rc, out = self.wait("--expect", "11:native-guard=success")
        self.assertEqual(rc, 1, out)
        self.assertIn("native-guard", out)
        self.assertIn("skipped", out)

    def test_expected_job_missing_from_a_finished_run_exits_1(self):
        self.runs = {"11": [run("11", [job("plan")])]}
        rc, out = self.wait("--expect", "11:native-guard=success")
        self.assertEqual(rc, 1, out)
        self.assertIn("native-guard", out)
        self.assertIn("no job", out)

    def test_bad_expect_syntax_exits_1(self):
        rc, out = self.wait("--expect", "11:native-guard=green")
        self.assertEqual(rc, 1, out)
        self.assertIn("--expect", out)

    def test_mixing_shas_and_run_ids_is_refused(self):
        rc, out = self.wait("a" * 40, "--run-id", "11")
        self.assertEqual(rc, 1, out)

    # ── passes only on the full manifest ──
    def test_all_met_exits_0_and_names_every_run(self):
        self.runs = {
            "11": [run("11", [job("plan"), job("native-guard")])],
            "12": [run("12", [job("plan"), job("native-guard", "failure")], conclusion="failure", branch="w12-red")],
            "13": [run("13", [job("plan"), job("native-guard", "skipped")], branch="w12-rt")],
        }
        rc, out = self.wait("--expect", "11:native-guard=success", "--expect", "12:native-guard=failure",
                            "--expect", "13:native-guard=skipped")
        self.assertEqual(rc, 0, out)
        for rid in ("11", "12", "13"):
            self.assertIn(f"/runs/{rid}", out)
        self.assertIn("native-guard=failure", out)

    def test_bare_run_id_must_conclude_success(self):
        self.runs = {"11": [run("11", [job("plan")])], "12": [run("12", [job("plan", "failure")], conclusion="failure")]}
        rc, out = self.wait("--run-id", "11")
        self.assertEqual(rc, 0, out)
        rc, out = self.wait("--run-id", "11", "--run-id", "12")
        self.assertEqual(rc, 1, out)
        self.assertIn("/runs/12", out)

    def test_queued_under_once_exits_2(self):
        self.runs = {"11": [run("11", [job("plan", status="queued")], status="queued")],
                     "12": [run("12", [job("plan")])]}
        rc, out = self.wait("--run-id", "11", "--run-id", "12", "--once")
        self.assertEqual(rc, 2, out)
        self.assertIn("not yet", out)
        self.assertIn("11", out)

    def test_wrong_finished_job_is_final_even_while_its_run_is_active(self):
        self.runs = {"11": [run("11", [job("macos", "failure"), job("ios", status="queued")], status="in_progress")]}
        rc, out = self.wait("--expect", "11:macos=success", "--once")
        self.assertEqual(rc, 1, out)
        self.assertIn("macos", out)

    def test_waits_until_queued_runs_complete(self):
        self.runs = {"11": [run("11", [job("plan", status="queued")], status="queued"),
                            run("11", [job("plan", status="in_progress")], status="in_progress"),
                            run("11", [job("plan")])]}
        rc, out = self.wait("--expect", "11:plan=success", "--timeout-min", "1")
        self.assertEqual(rc, 0, out)
        self.assertEqual(sum(1 for c in self.calls() if c[:3] == ["run", "view", "11"]), 3)

    def test_gh_calls_name_the_repo(self):
        self.runs = {"11": [run("11", [job("plan")])]}
        self.wait("--run-id", "11")
        self.assertTrue(self.calls())
        self.assertTrue(all(c[-2:] == ["-R", "o/r"] for c in self.calls()), self.calls())


if __name__ == "__main__":
    unittest.main()
