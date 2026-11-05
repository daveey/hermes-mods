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

    def test_workflow_whose_runs_all_skipped_is_not_a_check_of_the_commit(self):
        # A workflow_run job gated by `if:` (a "runner-loss recovery" job that acts only after a failed run) ends
        # every run `skipped`, so it never succeeds. Waiting for its success held every discovered wait at
        # "not yet ... 0 run(s) queued/running" although every real check had passed.
        runs = [run("c", wf="Recovery", conclusion="skipped"), run("b", wf="Recovery", conclusion="skipped"), run("b")]
        state = w.evaluate(runs, ["b"], contains, None)
        self.assertTrue(state["CI"]["done"])
        self.assertFalse(state["CI"]["skipped"])
        self.assertEqual(state["Recovery"]["done"]["url"], "u/Recovery/c0")
        self.assertTrue(state["Recovery"]["skipped"])

    def test_skipped_workflow_ignores_its_runs_still_active_on_later_commits(self):
        # Every later commit starts new runs of it; one of those queued must not hold this commit's verdict.
        runs = [run("d", wf="Recovery", status="in_progress", conclusion=""),
                run("b", wf="Recovery", conclusion="skipped"), run("b")]
        state = w.evaluate(runs, ["b"], contains, None)
        self.assertTrue(state["Recovery"]["skipped"])

    def test_skipped_workflow_waits_while_its_run_on_the_commit_itself_is_active(self):
        runs = [run("b", wf="Recovery", status="queued", conclusion=""),
                run("b", wf="Recovery", conclusion="skipped"), run("b")]
        state = w.evaluate(runs, ["b"], contains, None)
        self.assertIsNone(state["Recovery"]["done"])

    def test_skipped_then_failed_reports_the_failure(self):
        runs = [run("c", wf="Recovery", conclusion="failure"), run("b", wf="Recovery", conclusion="skipped"), run("b")]
        state = w.evaluate(runs, ["b"], contains, None)
        self.assertIsNone(state["Recovery"]["done"])
        self.assertEqual(state["Recovery"]["failed"]["url"], "u/Recovery/c0")

    def test_a_named_workflow_that_only_skipped_still_has_to_pass(self):
        state = w.evaluate([run("b", wf="Recovery", conclusion="skipped")], ["b"], contains, ["Recovery"])
        self.assertIsNone(state["Recovery"]["done"])


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
        # A temp dir that happens to sit inside some checkout must still count as "not a checkout".
        self.old_ceiling = os.environ.get("GIT_CEILING_DIRECTORIES")
        os.environ["GIT_CEILING_DIRECTORIES"] = str(self.home.parent)
        repo = self.home / "code" / "proj"
        repo.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", "git@github.com:me/proj.git"], check=True)
        self.repo = repo
        self.plain = self.home / "plain"  # a directory that is not a checkout, like ~
        self.plain.mkdir()

    def tearDown(self):
        import os
        import shutil
        for key, old in (("HOME", self.old_home), ("GIT_CEILING_DIRECTORIES", self.old_ceiling)):
            if old is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old
        shutil.rmtree(self.home, ignore_errors=True)

    def test_checkout_path_is_used_as_is(self):
        self.assertEqual(w.resolve_repo(self.repo), self.repo)

    def test_owner_name_slug_maps_to_matching_checkout(self):
        self.assertEqual(w.resolve_repo(Path("me/proj")), self.repo)

    def test_slug_for_another_owner_fails_clearly(self):
        with self.assertRaises(SystemExit) as e:
            w.resolve_repo(Path("someone/proj"))
        self.assertIn("not a local checkout", str(e.exception))

    def test_plain_directory_with_gh_repo_uses_that_repos_checkout(self):
        # A wait started from ~ with --gh-repo used to be handed ~ itself and died on its first `git fetch`.
        self.assertEqual(w.resolve_repo(self.plain, "me/proj"), self.repo)

    def test_plain_directory_without_a_matching_checkout_fails_clearly(self):
        for slug in (None, "someone/proj"):
            with self.assertRaises(SystemExit) as e:
                w.resolve_repo(self.plain, slug)
            self.assertIn("not a git checkout", str(e.exception))

    def test_origin_must_match_exactly_not_by_suffix(self):
        import subprocess
        subprocess.run(["git", "-C", str(self.repo), "remote", "set-url", "origin", "git@github.com:xme/proj.git"],
                       check=True)
        with self.assertRaises(SystemExit):
            w.resolve_repo(Path("me/proj"))
        with self.assertRaises(SystemExit):
            w.resolve_repo(self.plain, "me/proj")

    def _main_in_plain_cwd(self, argv, git_side_effect=None):
        import os
        from unittest import mock
        old = os.getcwd()
        os.chdir(self.plain)
        try:
            with mock.patch.object(w, "git", side_effect=git_side_effect) as git:
                try:
                    w.main(argv)
                except (SystemExit, RuntimeError) as e:
                    return git, e
                return git, None
        finally:
            os.chdir(old)

    def test_cli_from_a_plain_cwd_runs_git_in_the_gh_repo_checkout(self):
        # Entry path: the CLI with --repo omitted, i.e. the current directory.
        def stop(repo, *args):
            raise RuntimeError("stop after the first git call")
        git, err = self._main_in_plain_cwd(["abc1234", "--gh-repo", "me/proj", "--once"], stop)
        self.assertIsInstance(err, RuntimeError)
        self.assertEqual(git.call_args_list[0].args[0], self.repo)
        self.assertEqual(git.call_args_list[0].args[1], "fetch")

    def test_cli_from_a_plain_cwd_without_a_checkout_exits_before_any_git_call(self):
        git, err = self._main_in_plain_cwd(["abc1234", "--gh-repo", "someone/proj", "--once"])
        self.assertIsInstance(err, SystemExit)
        self.assertIn("not a git checkout", str(err))
        git.assert_not_called()


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


class NarrowRefspecFetchTests(unittest.TestCase):
    """A checkout whose remote.origin.fetch maps only main must still see a pushed helper branch's sha."""

    def test_branch_wait_works_with_a_main_only_refspec(self):
        import contextlib
        import io
        import subprocess
        import tempfile
        from unittest import mock
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            origin, local = root / "origin", root / "local"

            def g(repo, *args):
                return subprocess.run(["git", "-C", str(repo), "-c", "commit.gpgsign=false", "-c",
                                       "core.hooksPath=/dev/null", "-c", "user.email=y@e.com", "-c", "user.name=y",
                                       *args], check=True, capture_output=True, text=True).stdout.strip()
            subprocess.run(["git", "init", "-q", "-b", "main", str(origin)], check=True)
            g(origin, "commit", "-q", "--allow-empty", "-m", "a")
            subprocess.run(["git", "clone", "-q", str(origin), str(local)], check=True, capture_output=True)
            g(local, "config", "--replace-all", "remote.origin.fetch", "+refs/heads/main:refs/remotes/origin/main")
            g(origin, "checkout", "-q", "-b", "helper")
            g(origin, "commit", "-q", "--allow-empty", "-m", "h")
            sha = g(origin, "rev-parse", "HEAD")
            g(local, "fetch", "-q", "origin", "helper")  # FETCH_HEAD only: origin/helper still missing
            self.assertNotEqual(subprocess.run(["git", "-C", str(local), "rev-parse", "--verify", "-q",
                                                "origin/helper"], capture_output=True).returncode, 0)
            out = io.StringIO()
            with mock.patch.object(w, "list_runs", return_value=[]), contextlib.redirect_stdout(out):
                rc = w.main([sha, "--repo", str(local), "--branch", "helper", "--once", "--gh-repo", "o/r"])
            self.assertNotEqual(rc, 3, out.getvalue())
            self.assertNotIn("is not on origin/helper", out.getvalue())


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
        with mock.patch.object(w, "resolve_repo", side_effect=lambda p, slug=None: p), \
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

    def test_finished_failure_under_once_is_stalled_not_not_yet(self):
        # A wake probe using --once must see a finished red run as final (1); returning 2 forever
        # left a card parked until its clock although its CI had already failed.
        red = dict(run("c" * 40, conclusion="failure"), _wf="ci.yml")
        self.assertEqual(self._main([[red]], "--once"), 1)

    def test_failure_with_a_rerun_active_under_once_is_not_yet(self):
        red = dict(run("c" * 40, conclusion="failure"), _wf="ci.yml")
        rerun = dict(run("c" * 40, status="in_progress", conclusion=""), _wf="ci.yml")
        self.assertEqual(self._main([[rerun, red]], "--once"), 2)

    def test_no_run_yet_under_once_is_not_yet(self):
        self.assertEqual(self._main([[]], "--once"), 2)

    def test_gh_that_keeps_failing_ends_the_wait(self):
        self.assertEqual(self._main([RuntimeError("gh: auth")] * getattr(w, "MAX_CALL_ERRORS", 5)), 1)

    def _main_out(self, listings, *extra):
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = self._main(listings, *extra)
        return rc, buf.getvalue()

    def test_pass_on_a_later_commit_names_the_commit_the_run_built(self):
        # The run proving a commit can have built a later commit containing it. Its URL was printed under the
        # requested sha alone and quoted as "CI on <sha>"; the line must name the commit the run actually built.
        green = dict(run("d" * 40), _wf="ci.yml")
        rc, out = self._main_out([[green]], "--once")
        self.assertEqual(rc, 0)
        self.assertIn("passed ccccccccc: ci.yml u/CI/" + "d" * 40 + "0 (ran on dddddddddd, a later commit containing it)",
                      out)

    def test_pass_on_the_commit_itself_prints_just_the_url(self):
        green = dict(run("c" * 40), _wf="ci.yml")
        rc, out = self._main_out([[green]], "--once")
        self.assertEqual(rc, 0)
        self.assertNotIn("ran on", out)

    def test_stall_on_a_later_commit_names_the_commit_that_failed(self):
        # A red run on a later commit may be failing on another change; say which commit it built.
        red = dict(run("d" * 40, conclusion="failure"), _wf="ci.yml")
        rc, out = self._main_out([[red]], "--once")
        self.assertEqual(rc, 1)
        self.assertIn("(ran on dddddddddd, a later commit containing it)", out)

    def test_pass_on_a_later_commit_says_the_commits_own_run_failed(self):
        # The commit's own run went red and a later commit's run went green, because that later commit fixed
        # it. "passed <sha>: … (ran on <later>)" alone got quoted as proof that this commit fixed CI.
        green = dict(run("d" * 40), _wf="ci.yml")
        red = dict(run("c" * 40, conclusion="failure"), _wf="ci.yml")
        rc, out = self._main_out([[green, red]], "--once")
        self.assertEqual(rc, 0)
        self.assertIn("(ran on dddddddddd, a later commit containing it); but its own run on cccccccccc ended "
                      "failure: u/CI/" + "c" * 40 + "0", out)

    def test_own_run_rerun_green_or_proof_on_the_commit_adds_no_note(self):
        # A rerun on the commit itself passed (newest own run green), or the proving run built the commit:
        # nothing to warn about.
        own_green = dict(run("c" * 40, n=1), _wf="ci.yml")
        own_red = dict(run("c" * 40, conclusion="failure"), _wf="ci.yml")
        later_green = dict(run("d" * 40), _wf="ci.yml")
        for listing in ([later_green, own_green, own_red], [own_green, own_red]):
            rc, out = self._main_out([listing], "--once")
            self.assertEqual(rc, 0, out)
            self.assertNotIn("its own run", out)

    def test_own_cancelled_run_adds_no_note(self):
        # Busy branches cancel runs on intermediate commits; a cancelled own run is not a red one.
        green = dict(run("d" * 40), _wf="ci.yml")
        cancelled = dict(run("c" * 40, conclusion="cancelled"), _wf="ci.yml")
        rc, out = self._main_out([[green, cancelled]], "--once")
        self.assertEqual(rc, 0)
        self.assertNotIn("its own run", out)

    def test_pass_says_when_a_newer_run_containing_it_went_red(self):
        # The commit's own run passed, then a later push broke the workflow: every newer run containing the commit
        # is red. "passed <sha>: hermes-sync tests <own green url>" was quoted as "state CI is green" while the
        # branch had been red for an hour. The pass stands (exit 0), but the line must name the newest red run.
        red = dict(run("d" * 40, conclusion="failure"), _wf="ci.yml")
        green = dict(run("c" * 40), _wf="ci.yml")
        rc, out = self._main_out([[red, green]], "--once")
        self.assertEqual(rc, 0, out)
        self.assertIn("passed ccccccccc: ci.yml u/CI/" + "c" * 40 + "0; but the newest finished run containing it, on "
                      "dddddddddd, ended failure: u/CI/" + "d" * 40 + "0", out)

    def test_newer_red_note_and_own_red_note_both_show(self):
        # Own run red, a later commit's run green (the pass), a still later run red: both caveats apply.
        newest_red = dict(run("e" * 40, conclusion="failure"), _wf="ci.yml")
        green = dict(run("d" * 40), _wf="ci.yml")
        own_red = dict(run("c" * 40, conclusion="failure"), _wf="ci.yml")
        rc, out = self._main_out([[newest_red, green, own_red]], "--once")
        self.assertEqual(rc, 0, out)
        self.assertIn("(ran on dddddddddd, a later commit containing it); but its own run on cccccccccc ended failure: "
                      "u/CI/" + "c" * 40 + "0; but the newest finished run containing it, on eeeeeeeeee, ended "
                      "failure: u/CI/" + "e" * 40 + "0", out)

    def test_newest_run_green_cancelled_or_running_adds_no_newer_note(self):
        # Only a FINISHED red run after the pass is news: a newer green, a cancelled replacement or a run still in
        # flight says nothing about the branch being red.
        own_green = dict(run("c" * 40), _wf="ci.yml")
        for newer in (dict(run("d" * 40), _wf="ci.yml"),
                      dict(run("d" * 40, conclusion="cancelled"), _wf="ci.yml"),
                      dict(run("d" * 40, status="in_progress", conclusion=""), _wf="ci.yml")):
            with self.subTest(newer=newer["status"] + "/" + newer["conclusion"]):
                rc, out = self._main_out([[newer, own_green]], "--once")
                self.assertEqual(rc, 0, out)
                self.assertNotIn("newest finished run", out)

    def _discovered_out(self, listings, *extra):
        """main() without --workflow: the workflows are the ones with a run containing the commit."""
        import contextlib
        import io
        from unittest import mock

        def fake_git(repo, *args):
            return "c" * 40 if args[0] == "rev-parse" else ""
        buf = io.StringIO()
        with mock.patch.object(w, "resolve_repo", side_effect=lambda p, slug=None: p), \
                mock.patch.object(w, "git", side_effect=fake_git), \
                mock.patch.object(w, "make_contains", return_value=lambda commit, sha: True), \
                mock.patch.object(w, "list_runs", side_effect=listings), \
                mock.patch.object(w.time, "sleep"), contextlib.redirect_stdout(buf):
            rc = w.main(["c" * 40, "--gh-repo", "o/r", "--interval", "0", *extra])
        return rc, buf.getvalue()

    def _event_runs(self):
        sha = "c" * 40
        pushes = [dict(run(sha, wf=name), event="push")
                  for name in ("CI", "Docker", "Nix", "Workflow lint", "State tests")]
        meta = [dict(run(sha, wf="Review", status="in_progress", conclusion=""), event="workflow_run"),
                dict(run(sha, wf="Recovery", conclusion="failure"), event="workflow_run"),
                dict(run(sha, wf="Comment", conclusion="success"), event="workflow_run")]
        return pushes, meta

    def test_discovery_ignores_workflow_run_even_on_same_head(self):
        pushes, meta = self._event_runs()
        state = w.evaluate(meta + pushes, ["c" * 40], lambda c, s: c == s, None)
        self.assertEqual(set(state), {r["workflowName"] for r in pushes})
        self.assertTrue(all(s["done"] for s in state.values()))

    def test_once_ignores_active_and_failed_meta_runs_and_prints_only_pushes(self):
        pushes, meta = self._event_runs()
        rc, out = self._discovered_out([meta + pushes], "--once")
        self.assertEqual(rc, 0, out)
        for r in pushes:
            self.assertIn(r["url"], out)
        for r in meta:
            self.assertNotIn(r["workflowName"], out)

    def test_once_ignores_failed_meta_run_without_active_runs(self):
        pushes, meta = self._event_runs()
        rc, out = self._discovered_out([[meta[1]] + pushes], "--once")
        self.assertEqual(rc, 0, out)
        self.assertNotIn("Recovery", out)

    def test_same_name_meta_run_cannot_supply_success_failure_or_activity(self):
        push = dict(run("c", conclusion="failure"), event="push")
        for status, conclusion in (("completed", "success"), ("completed", "failure"), ("in_progress", "")):
            with self.subTest(status=status, conclusion=conclusion):
                meta = dict(run("c", status=status, conclusion=conclusion, n=1), event="workflow_run")
                state = w.evaluate([meta, push], ["c"], contains, None)["CI"]
                self.assertIsNone(state["done"])
                self.assertEqual(state["failed"], push)
                self.assertEqual(state["active"], 0)

    def test_explicit_workflow_run_waits_and_then_accepts_success(self):
        for selector in ("ci.yml", "CI"):
            with self.subTest(selector=selector):
                active = dict(run("c" * 40, status="in_progress", conclusion=""),
                              event="workflow_run", _wf=selector)
                green = dict(active, status="completed", conclusion="success")
                rc, out = self._discovered_out([[active]], "--workflow", selector, "--once")
                self.assertEqual(rc, 2, out)
                rc, out = self._discovered_out([[green]], "--workflow", selector, "--once")
                self.assertEqual(rc, 0, out)
                self.assertIn(green["url"], out)

    def test_once_passes_when_the_only_other_workflow_skipped(self):
        green, skipped = run("c" * 40), run("c" * 40, wf="Recovery", conclusion="skipped")
        rc, out = self._discovered_out([[skipped, green]], "--once")
        self.assertEqual(rc, 0, out)
        self.assertIn("passed ccccccccc: CI u/CI/", out)
        self.assertIn("Recovery skipped u/Recovery/", out)

    def test_wait_loop_passes_when_the_only_other_workflow_skipped(self):
        # The loop used to time out here (2), or end "stalled" red (1) once nothing of that workflow was active.
        green, skipped = run("c" * 40), run("c" * 40, wf="Recovery", conclusion="skipped")
        rc, out = self._discovered_out([[skipped, green]], "--timeout-min", "0")
        self.assertEqual(rc, 0, out)
        self.assertIn("Recovery skipped u/Recovery/", out)


class PerJobFailureTests(unittest.TestCase):
    """A failed job is final even while its run is still queued behind a sibling job waiting for a runner.

    Regression for validate.yml run 41956091801: macos failed 04:34Z while ios stayed queued, so the run read
    `queued` and `--once` said "not yet" through three parks. A queued job is never a pass either.
    """

    SHA = "c" * 40

    def queued_run(self, sha=None, rid=7, status="queued"):
        return dict(run(sha or self.SHA, status=status, conclusion=""), _wf="ci.yml", databaseId=rid)

    @staticmethod
    def job(name, status="completed", conclusion="success"):
        return {"name": name, "status": status, "conclusion": conclusion, "url": f"j/{name}"}

    def _main_out(self, listings, jobs, *extra, grace=None):
        import contextlib
        import io
        from unittest import mock
        calls = []

        def jobs_of(repo, slug, rid):
            calls.append(rid)
            value = jobs[rid] if isinstance(jobs, dict) else jobs
            if isinstance(value, Exception):
                raise value
            return value

        def fake_git(repo, *args):
            return self.SHA if args[0] == "rev-parse" else ""
        buf = io.StringIO()
        patches = [mock.patch.object(w, "resolve_repo", side_effect=lambda p, slug=None: p),
                   mock.patch.object(w, "git", side_effect=fake_git),
                   mock.patch.object(w, "make_contains", return_value=lambda commit, sha: commit.startswith("origin/") or contains(commit[0], sha[0])),
                   mock.patch.object(w, "list_runs", side_effect=listings),
                   mock.patch.object(w, "list_jobs", side_effect=jobs_of),
                   mock.patch.object(w.time, "sleep")]
        if grace is not None:
            patches.append(mock.patch.object(w, "STARTUP_GRACE_S", grace))
        with contextlib.ExitStack() as stack, contextlib.redirect_stdout(buf):
            for p in patches:
                stack.enter_context(p)
            rc = w.main([self.SHA, "--workflow", "ci.yml", "--gh-repo", "o/r", "--interval", "0", *extra])
        self.calls = calls
        return rc, buf.getvalue()

    def test_queued_run_with_a_failed_job_is_stalled_under_once(self):
        jobs = [self.job("plan"), self.job("ios", "queued", None), self.job("macos", conclusion="failure")]
        rc, out = self._main_out([[self.queued_run()]], jobs, "--once")
        self.assertEqual(rc, 1, out)
        self.assertIn("job 'macos' ended failure while the run is still queued: j/macos", out)

    def test_timed_out_and_startup_failure_jobs_are_failures(self):
        for conclusion in ("timed_out", "startup_failure"):
            with self.subTest(conclusion=conclusion):
                jobs = [self.job("ios", "queued", None), self.job("macos", conclusion=conclusion)]
                rc, out = self._main_out([[self.queued_run(status="in_progress")]], jobs, "--once")
                self.assertEqual(rc, 1, out)

    def test_queued_jobs_alone_are_not_yet_never_a_pass(self):
        jobs = [self.job("plan"), self.job("ios", "queued", None), self.job("macos", "queued", None)]
        rc, out = self._main_out([[self.queued_run()]], jobs, "--once")
        self.assertEqual(rc, 2, out)
        self.assertIn("not yet", out)

    def test_skipped_cancelled_neutral_and_green_jobs_beside_a_queued_one_are_not_yet(self):
        jobs = [self.job("plan"), self.job("guards", conclusion="skipped"), self.job("lint", conclusion="neutral"),
                self.job("old", conclusion="cancelled"), self.job("macos", "queued", None)]
        rc, out = self._main_out([[self.queued_run()]], jobs, "--once")
        self.assertEqual(rc, 2, out)

    def test_every_job_green_while_the_run_still_reads_queued_is_not_a_pass(self):
        # Only the run's own `success` passes; job evidence can make a run red, never green.
        rc, out = self._main_out([[self.queued_run(status="in_progress")]], [self.job("plan"), self.job("macos")],
                                 "--once")
        self.assertEqual(rc, 2, out)

    def test_unknown_job_conclusion_keeps_waiting_and_says_so(self):
        for conclusion in (None, "stale", "action_required"):
            with self.subTest(conclusion=conclusion):
                jobs = [self.job("macos", conclusion=conclusion), self.job("ios", "queued", None)]
                rc, out = self._main_out([[self.queued_run()]], jobs, "--once")
                self.assertEqual(rc, 2, out)
                self.assertIn("job 'macos' ended " + repr(conclusion) + " (unknown; still waiting)", out)

    def test_unreadable_jobs_keep_waiting_and_say_so(self):
        rc, out = self._main_out([[self.queued_run()]], RuntimeError("gh run view 7: HTTP 502"), "--once")
        self.assertEqual(rc, 2, out)
        self.assertIn("jobs of run 7 unreadable", out)

    def test_another_active_run_containing_the_commit_still_waits(self):
        later = self.queued_run(sha="d" * 40, rid=8, status="in_progress")
        jobs = {7: [self.job("macos", conclusion="failure"), self.job("ios", "queued", None)],
                8: [self.job("macos", "in_progress", None)]}
        rc, out = self._main_out([[later, self.queued_run()]], jobs, "--once")
        self.assertEqual(rc, 2, out)

    def test_a_green_run_containing_the_commit_still_passes(self):
        green = dict(run("d" * 40), _wf="ci.yml", databaseId=9)
        rc, out = self._main_out([[green, self.queued_run()]], {7: [self.job("macos", conclusion="failure")]},
                                 "--once")
        self.assertEqual(rc, 0, out)

    def test_runs_not_containing_the_commit_and_finished_runs_are_not_queried(self):
        older = dict(self.queued_run(sha="a" * 40, rid=5))
        done = dict(run(self.SHA, conclusion="failure"), _wf="ci.yml", databaseId=6)
        self._main_out([[older, done]], {}, "--once")
        self.assertEqual(self.calls, [])

    def test_loop_mode_stalls_on_a_failed_job_without_waiting_for_the_queued_sibling(self):
        q = self.queued_run()
        jobs = [self.job("macos", conclusion="failure"), self.job("ios", "queued", None)]
        rc, out = self._main_out([[dict(q)], [dict(q)], [dict(q)]], jobs, "--timeout-min", "5", grace=0)
        self.assertEqual(rc, 1, out)
        self.assertIn("stalled: newest finished run containing the commit ended failure", out)
        self.assertIn("job 'macos'", out)

    def test_list_jobs_is_bounded_and_names_the_repo(self):
        from unittest import mock
        seen = []

        def fake(cmd, **kw):
            seen.append((cmd, kw.get("timeout")))
            return mock.Mock(returncode=0, stdout='{"jobs": [{"name": "macos"}]}', stderr="")
        with mock.patch.object(w.subprocess, "run", side_effect=fake):
            self.assertEqual(w.list_jobs(Path("."), "o/r", 41), [{"name": "macos"}])
        cmd, timeout = seen[0]
        self.assertEqual(cmd, ["gh", "run", "view", "41", "--json", "jobs", "-R", "o/r"])
        self.assertTrue(timeout)
