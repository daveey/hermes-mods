"""wait_for_ci without --workflow ignores meta runs on the branch head: workflow_run, schedule, workflow_dispatch.

Incident (2026-11-12): `wait_for_ci.py 8008d854b0 --branch main` (no --workflow) exited 1 "stalled: newest finished
run containing the commit ended failure: .../runs/43220839508 (ran on 2cfdbeedc2, ...)". That run was the hourly
reporting job "CI review comment", event workflow_dispatch on the main head, red only because one PR comment post got
HTTP 502. The commit's real CI (push run 43220923432 on a1342c3fbe, containing it) had concluded success.
workflow_run was already excluded; the same meta workflows now also run as workflow_dispatch and schedule.
"""

import contextlib
import io
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import wait_for_ci as w  # noqa: E402

SHA = "8008d854b0c82ae4ff271d9da42afbe986f4f4d3"   # the waited commit
CI_HEAD = "a1342c3fbe" + "0" * 30                   # later commit containing it: push CI green
META_HEAD = "2cfdbeedc2" + "0" * 30                 # later main head: the meta job's run
ORDER = [SHA, CI_HEAD, META_HEAD, "origin/main"]


def contains(commit, sha):
    return ORDER.index(sha) <= ORDER.index(commit)


def ci_push_green():
    return {"databaseId": 43220923432, "headSha": CI_HEAD, "status": "completed", "conclusion": "success",
            "workflowName": "CI", "event": "push",
            "url": "https://github.com/o/r/actions/runs/43220923432"}


def review_comment_red(event="workflow_dispatch"):
    return {"databaseId": 43220839508, "headSha": META_HEAD, "status": "completed", "conclusion": "failure",
            "workflowName": "CI review comment", "event": event,
            "url": "https://github.com/o/r/actions/runs/43220839508"}


def fake_list_runs(listing):
    """Like the real list_runs: with --workflow, only that workflow's runs, tagged `_wf`."""
    def list_runs(repo, branch, workflows, slug=None):
        if not workflows:
            return [dict(r) for r in listing]
        return [dict(r, _wf=wf) for wf in workflows for r in listing if r["workflowName"] == wf]
    return list_runs


class MetaEventTests(unittest.TestCase):
    def _main(self, listing, *extra, grace=None):
        def fake_git(repo, *args):
            return SHA if args[0] == "rev-parse" else ""
        buf = io.StringIO()
        with mock.patch.object(w, "resolve_repo", side_effect=lambda p, slug=None: p), \
                mock.patch.object(w, "git", side_effect=fake_git), \
                mock.patch.object(w, "make_contains", return_value=contains), \
                mock.patch.object(w, "list_runs", side_effect=fake_list_runs(listing)), \
                mock.patch.object(w, "list_jobs", return_value=[]), \
                mock.patch.object(w, "STARTUP_GRACE_S", 0 if grace is None else grace), \
                mock.patch.object(w.time, "sleep"), contextlib.redirect_stdout(buf):
            rc = w.main([SHA, "--gh-repo", "o/r", "--branch", "main", "--interval", "0", *extra])
        return rc, buf.getvalue()

    # ── the incident: a red workflow_dispatch meta run must not stall a commit whose push CI is green ──
    def test_dispatch_meta_failure_does_not_stall_green_commit(self):
        rc, out = self._main([review_comment_red(), ci_push_green()], "--timeout-min", "5")
        self.assertEqual(rc, 0, out)
        self.assertNotIn("stalled", out)
        self.assertIn("passed 8008d854b: CI https://github.com/o/r/actions/runs/43220923432", out)
        self.assertNotIn("CI review comment", out)

    def test_dispatch_meta_failure_under_once_is_done(self):
        rc, out = self._main([review_comment_red(), ci_push_green()], "--once")
        self.assertEqual(rc, 0, out)
        self.assertNotIn("CI review comment", out)

    def test_schedule_meta_failure_is_ignored_too(self):
        for event in ("schedule", "workflow_dispatch", "workflow_run"):
            with self.subTest(event=event):
                rc, out = self._main([review_comment_red(event), ci_push_green()], "--once")
                self.assertEqual(rc, 0, out)

    # ── naming the workflow opts back in ──
    def test_named_meta_workflow_still_counts_and_stalls(self):
        rc, out = self._main([review_comment_red(), ci_push_green()], "--workflow", "CI review comment",
                             "--timeout-min", "5")
        self.assertEqual(rc, 1, out)
        self.assertIn("stalled: newest finished run containing the commit ended failure: "
                      "https://github.com/o/r/actions/runs/43220839508", out)

    def test_named_meta_workflow_under_once_is_stalled(self):
        rc, out = self._main([review_comment_red(), ci_push_green()], "--workflow", "CI review comment", "--once")
        self.assertEqual(rc, 1, out)

    def test_meta_events_supply_no_success_failure_or_activity_when_discovering(self):
        push = dict(ci_push_green(), conclusion="failure")
        for event in ("schedule", "workflow_dispatch"):
            for status, conclusion in (("completed", "success"), ("completed", "failure"), ("in_progress", "")):
                with self.subTest(event=event, status=status, conclusion=conclusion):
                    meta = dict(ci_push_green(), headSha=META_HEAD, event=event, status=status,
                                conclusion=conclusion, url="u/meta")
                    state = w.evaluate([meta, push], [SHA], contains, None)
                    self.assertEqual(set(state), {"CI"})
                    self.assertIsNone(state["CI"]["done"])
                    self.assertEqual(state["CI"]["failed"], push)
                    self.assertEqual(state["CI"]["active"], 0)

    def test_active_meta_run_jobs_are_not_read_without_workflow(self):
        active = dict(review_comment_red(), status="in_progress", conclusion="")
        jobs_of = mock.Mock(return_value=[{"name": "post", "status": "completed", "conclusion": "failure"}])
        runs = [active]
        w.mark_failed_jobs(runs, [SHA], contains, None, jobs_of)
        jobs_of.assert_not_called()
        self.assertEqual(runs[0]["status"], "in_progress")
        w.mark_failed_jobs(runs, [SHA], contains, ["CI review comment"], jobs_of)
        self.assertEqual(runs[0]["conclusion"], "failure")


if __name__ == "__main__":
    unittest.main()
