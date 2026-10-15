"""wait_for_ci judges a commit by the runs and tags that contain it, never by the newest run."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import wait_for_ci as w  # noqa: E402

# Linear history a < b < c < d; "contains(x, s)" = s is at or before x.
ORDER = "abcd"


def contains(commit, sha):
    return ORDER.index(sha) <= ORDER.index(commit)


def run(sha, status="completed", conclusion="success", wf="CI", n=0):
    return {"headSha": sha, "status": status, "conclusion": conclusion, "workflowName": wf,
            "url": f"u/{wf}/{sha}{n}"}


class EvaluateTests(unittest.TestCase):
    def test_success_on_later_commit_counts_even_when_newest_run_is_cancelled(self):
        runs = [run("d", conclusion="cancelled"), run("c"), run("b", conclusion="cancelled")]
        state = w.evaluate(runs, ["b"], contains, None)
        self.assertEqual(state["CI"]["done"]["url"], "u/CI/c0")

    def test_success_on_older_commit_does_not_count(self):
        state = w.evaluate([run("a")], ["b"], contains, ["CI"])
        self.assertIsNone(state["CI"]["done"])

    def test_failure_reported_but_cancelled_ignored(self):
        runs = [run("d", conclusion="cancelled"), run("c", conclusion="failure")]
        state = w.evaluate(runs, ["b"], contains, None)
        self.assertIsNone(state["CI"]["done"])
        self.assertEqual(state["CI"]["failed"]["url"], "u/CI/c0")

    def test_active_run_keeps_waiting(self):
        runs = [run("d", status="queued", conclusion=""), run("c", conclusion="failure")]
        state = w.evaluate(runs, ["b"], contains, None)
        self.assertEqual(state["CI"]["active"], 1)

    def test_every_workflow_must_pass(self):
        runs = [run("c", wf="CI"), run("c", wf="Release", conclusion="failure")]
        state = w.evaluate(runs, ["b"], contains, None)
        self.assertTrue(state["CI"]["done"])
        self.assertIsNone(state["Release"]["done"])

    def test_workflow_given_by_file_matches_via_listing_tag(self):
        r = run("c", wf="Baby Bear macOS release"); r["_wf"] = "macos-release.yml"
        state = w.evaluate([r], ["b"], contains, ["macos-release.yml"])
        self.assertTrue(state["macos-release.yml"]["done"])


if __name__ == "__main__":
    unittest.main()
