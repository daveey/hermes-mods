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


class GhRepoTests(unittest.TestCase):
    """Every gh call names the checkout's origin repo. A bare `gh run list` in a fork checkout with an `upstream`
    remote listed the upstream repo's runs, so the fork's commit was never found and every wait ended "stalled"."""

    def setUp(self):
        import subprocess
        import tempfile
        self.repo = Path(tempfile.mkdtemp()) / "fork"
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        self.git = lambda *a: subprocess.run(["git", "-C", str(self.repo), *a], check=True)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.repo.parent, ignore_errors=True)

    def test_fork_checkout_uses_origin_not_upstream(self):
        self.git("remote", "add", "upstream", "git@github.com:other/project.git")
        self.git("remote", "add", "origin", "git@github.com:me/fork.git")
        self.assertEqual(w.gh_repo(self.repo), "me/fork")

    def test_https_and_ssh_origin_urls(self):
        self.git("remote", "add", "origin", "https://github.com/me/fork.git")
        self.assertEqual(w.gh_repo(self.repo), "me/fork")
        self.git("remote", "set-url", "origin", "https://github.com/me/fork")
        self.assertEqual(w.gh_repo(self.repo), "me/fork")
        self.git("remote", "set-url", "origin", "ssh://git@github.com/me/fork.git")
        self.assertEqual(w.gh_repo(self.repo), "me/fork")

    def test_no_or_non_github_origin_leaves_gh_default(self):
        self.assertIsNone(w.gh_repo(self.repo))
        self.git("remote", "add", "origin", "git@gitlab.example.com:me/fork.git")
        self.assertIsNone(w.gh_repo(self.repo))
        self.assertEqual(w.gh_cmd(None, "run", "list"), ["gh", "run", "list"])

    def test_list_runs_and_dispatch_name_the_repo(self):
        from unittest import mock
        calls = []

        def fake(cmd, **kw):
            calls.append(cmd)
            import subprocess
            return subprocess.CompletedProcess(cmd, 0, "[]", "")
        with mock.patch.object(w.subprocess, "run", side_effect=fake):
            w.list_runs(self.repo, "main", ["ci.yaml"], "me/fork")
        self.assertEqual(calls[0][:3], ["gh", "run", "list"])
        self.assertIn(["-R", "me/fork"], [calls[0][i:i + 2] for i in range(len(calls[0]) - 1)])
        self.assertEqual(w.gh_cmd("me/fork", "workflow", "run", "ci.yaml", "--ref", "main"),
                         ["gh", "workflow", "run", "ci.yaml", "--ref", "main", "-R", "me/fork"])


class ContainsCacheTests(unittest.TestCase):
    """A run listed between a push and the next fetch must count once its commit is fetched."""

    def setUp(self):
        import subprocess
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.origin, self.local = root / "origin", root / "local"

        def g(repo, *args):
            return subprocess.run(["git", "-C", str(repo), "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null",
                                   *args], check=True, capture_output=True, text=True).stdout.strip()
        self.g = g
        subprocess.run(["git", "init", "-q", str(self.origin)], check=True)
        g(self.origin, "config", "user.email", "you@example.com")
        g(self.origin, "config", "user.name", "you")
        g(self.origin, "commit", "-q", "--allow-empty", "-m", "a")
        self.a = g(self.origin, "rev-parse", "HEAD")
        subprocess.run(["git", "clone", "-q", str(self.origin), str(self.local)], check=True, capture_output=True)
        g(self.origin, "commit", "-q", "--allow-empty", "-m", "b")
        self.b = g(self.origin, "rev-parse", "HEAD")  # pushed, not fetched by `local` yet

    def tearDown(self):
        self.tmp.cleanup()

    def test_unfetched_run_head_counts_after_the_fetch(self):
        contains = w.make_contains(self.local)
        self.assertFalse(contains(self.b, self.a))  # b is unknown here yet
        self.g(self.local, "fetch", "-q", "origin")
        self.assertTrue(contains(self.b, self.a))

    def test_definite_answers_are_cached(self):
        from unittest import mock
        self.g(self.local, "fetch", "-q", "origin")
        contains = w.make_contains(self.local)
        real, calls = w.subprocess.run, []

        def counting(cmd, **kw):
            calls.append(cmd)
            return real(cmd, **kw)
        with mock.patch.object(w.subprocess, "run", side_effect=counting):
            for _ in range(2):
                self.assertTrue(contains(self.b, self.a))
                self.assertFalse(contains(self.a, self.b))
        self.assertEqual(len(calls), 2)


class BoundedCallTests(unittest.TestCase):
    """A hung or failing gh/git call must neither hold a wait past its deadline nor crash it."""

    def test_gh_and_git_calls_are_bounded(self):
        from unittest import mock
        seen = []

        def fake(cmd, **kw):
            seen.append(kw.get("timeout"))
            raise w.subprocess.TimeoutExpired(cmd, kw.get("timeout") or 0)
        with mock.patch.object(w.subprocess, "run", side_effect=fake):
            with self.assertRaises(RuntimeError):
                w.list_runs(Path("."), "main", ["ci.yml"], "o/r")
            with self.assertRaises(RuntimeError):
                w.git(Path("."), "fetch", "-q", "origin", "main")
        self.assertTrue(seen and all(seen), seen)

    def _main(self, listings, *extra):
        from unittest import mock

        def fake_git(repo, *args):
            return "c" * 40 if args[0] == "rev-parse" else ""
        with mock.patch.object(w, "resolve_repo", side_effect=lambda p: p), \
                mock.patch.object(w, "git", side_effect=fake_git), \
                mock.patch.object(w, "make_contains", return_value=lambda commit, sha: True), \
                mock.patch.object(w, "list_runs", side_effect=listings), \
                mock.patch.object(w.time, "sleep"):
            return w.main(["c" * 40, "--workflow", "ci.yml", "--gh-repo", "o/r", "--interval", "0", *extra])

    def test_gh_error_is_retried_and_the_wait_still_passes(self):
        green = dict(run("c" * 40), _wf="ci.yml")
        self.assertEqual(self._main([RuntimeError("gh run list: timed out"), [green]]), 0)

    def test_gh_error_under_once_is_not_yet(self):
        self.assertEqual(self._main([RuntimeError("gh run list: HTTP 502")], "--once"), 2)

    def test_gh_that_keeps_failing_ends_the_wait(self):
        self.assertEqual(self._main([RuntimeError("gh: auth")] * getattr(w, "MAX_CALL_ERRORS", 5)), 1)
