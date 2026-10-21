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


class ResolveRepoTests(unittest.TestCase):
    """--repo is a checkout path; an owner/name slug used to die with a git traceback."""

    def setUp(self):
        import os
        import subprocess
        import tempfile
        self.home = Path(tempfile.mkdtemp())
        self.old_home = os.environ.get("HOME")
        os.environ["HOME"] = str(self.home)
        repo = self.home / "code" / "proj"
        repo.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", "git@github.com:me/proj.git"], check=True)
        self.repo = repo

    def tearDown(self):
        import os
        import shutil
        if self.old_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = self.old_home
        shutil.rmtree(self.home, ignore_errors=True)

    def test_checkout_path_is_used_as_is(self):
        self.assertEqual(w.resolve_repo(self.repo), self.repo)

    def test_owner_name_slug_maps_to_matching_checkout(self):
        self.assertEqual(w.resolve_repo(Path("me/proj")), self.repo)

    def test_slug_for_another_owner_fails_clearly(self):
        with self.assertRaises(SystemExit) as e:
            w.resolve_repo(Path("someone/proj"))
        self.assertIn("not a local checkout", str(e.exception))
