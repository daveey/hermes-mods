"""agent_hook.py: the same owner marker for Claude Code / Codex sessions, and session-end removal of merged trees.

Run: python3 -B -m unittest discover -s plugins/worktree-owner/tests   (stdlib only; temp git repos)
"""
import importlib.util, json, os, subprocess, tempfile, time, unittest
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "agent_hook.py"
ENV = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")


def git(cwd, *a):
    return subprocess.run(["git", "-C", str(cwd), *a], check=True, capture_output=True, text=True, env=ENV).stdout


def load():
    spec = importlib.util.spec_from_file_location("worktree_owner_agent_hook_t", SRC)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class AgentHook(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name).resolve()
        self.h = load()
        self.b = self.h.base
        self.code = self.tmp / "code"; self.code.mkdir()
        self.b.REPO_GLOBS = [str(self.code / "*" / ".git"), str(self.code / "*" / "*" / ".git")]
        self.b.LOG = self.tmp / "owner.log"
        self.b.live_cwds = lambda: []
        self.h.STATE = self.tmp / "state"
        self.prs = {}  # branch -> merged PR head oid
        self.h.merged_pr_heads = lambda path, branch: [self.prs[branch]] if branch in self.prs else []
        self.bare = self.tmp / "remote.git"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(self.bare)], check=True)
        self.repo = self.code / "proj"
        git(self.tmp, "clone", "-q", str(self.bare), str(self.repo))
        (self.repo / "a.txt").write_text("a\n")
        git(self.repo, "add", "."); git(self.repo, "commit", "-qm", "init"); git(self.repo, "push", "-q", "origin", "HEAD:main")
        git(self.repo, "fetch", "-q")

    def post(self, cmd, sid="sess-1", tool_use_id="tu1", agent="claude", event="PostToolUse"):
        return self.h.handle({"hook_event_name": event, "session_id": sid, "cwd": str(self.repo), "tool_name": "Bash",
                              "tool_input": {"command": cmd}, "tool_use_id": tool_use_id}, agent, env={})

    def make(self, name, branch, sid="sess-1", agent="claude"):
        p = self.code / name
        cmd = f"cd {self.repo} && git worktree add -b {branch} {p} origin/main"
        self.post(cmd, sid, f"tu-{name}", agent, event="PreToolUse")
        git(self.repo, "worktree", "add", "-q", "-b", branch, str(p), "origin/main")
        self.post(cmd, sid, f"tu-{name}", agent)
        return p

    def marker(self, path):
        gd = git(path, "rev-parse", "--absolute-git-dir").strip()
        f = Path(gd) / self.b.MARKER
        return json.loads(f.read_text()) if f.exists() else None

    def commit_push(self, p, fname, branch):
        (p / fname).write_text("x\n"); git(p, "add", fname); git(p, "commit", "-qm", fname)
        git(p, "push", "-q", "origin", f"HEAD:{branch}")

    def merge_to_main(self, branch):
        git(self.repo, "fetch", "-q"); git(self.repo, "merge", "-q", "--ff-only", f"origin/{branch}")
        git(self.repo, "push", "-q", "origin", "HEAD:main")

    def end(self, sid="sess-1", agent="claude", event="SessionEnd"):
        return self.h.handle({"hook_event_name": event, "session_id": sid, "cwd": str(self.repo)}, agent, env={},
                             detach=False)

    # --- registration ---------------------------------------------------------------------------------------------
    def test_post_tool_use_writes_the_hermes_owner_marker_with_the_claude_session(self):
        p = self.make("proj-a", "ba")
        mk = self.marker(p)
        self.assertEqual(mk["agent"], "claude"); self.assertEqual(mk["agent_session_id"], "sess-1")
        self.assertEqual(os.path.realpath(mk["path"]), os.path.realpath(p))

    def test_codex_payload_is_tagged_codex(self):
        self.assertEqual(self.marker(self.make("proj-cx", "bcx", sid="cx-1", agent="codex"))["agent"], "codex")

    def test_kanban_card_env_is_kept_so_the_hermes_teardown_also_applies(self):
        p = self.code / "proj-k"
        git(self.repo, "worktree", "add", "-q", "-b", "bk", str(p), "origin/main")
        self.h.handle({"hook_event_name": "PostToolUse", "session_id": "s", "cwd": str(self.repo), "tool_name": "Bash",
                       "tool_input": {"command": f"git worktree add -b bk {p}"}}, "claude", env={"HERMES_KANBAN_TASK": "t_k"})
        self.assertEqual(self.marker(p)["task_id"], "t_k")

    def _identity_db(self, live, task="t_dead", run_id=5):
        import sqlite3
        db = self.tmp / f"{task}.db"
        conn = sqlite3.connect(db)
        conn.execute("CREATE TABLE tasks (id TEXT PRIMARY KEY, status TEXT, claim_lock TEXT, current_run_id INTEGER)")
        conn.execute(
            "CREATE TABLE task_runs (id INTEGER PRIMARY KEY, task_id TEXT, status TEXT, claim_lock TEXT, ended_at INTEGER)"
        )
        if live:
            conn.execute("INSERT INTO tasks VALUES (?, 'running', 'lock', ?)", (task, run_id))
            conn.execute("INSERT INTO task_runs VALUES (?, ?, 'running', 'lock', NULL)", (run_id, task))
        else:
            conn.execute("INSERT INTO tasks VALUES (?, 'done', NULL, NULL)", (task,))
            conn.execute("INSERT INTO task_runs VALUES (?, ?, 'done', 'lock', 1)", (run_id, task))
        conn.commit()
        conn.close()
        return db

    def test_finished_run_identity_is_not_stamped(self):
        p = self.code / "proj-stale"
        git(self.repo, "worktree", "add", "-q", "-b", "bstale", str(p), "origin/main")
        db = self._identity_db(False)
        self.h.handle({"hook_event_name": "PostToolUse", "session_id": "s", "cwd": str(self.repo), "tool_name": "Bash",
                       "tool_input": {"command": f"git worktree add -b bstale {p}"}}, "claude",
                      env={"HERMES_KANBAN_TASK": "t_dead", "HERMES_KANBAN_RUN_ID": "5", "HERMES_KANBAN_DB": str(db)})
        self.assertIsNone((self.marker(p) or {}).get("task_id"))

    def test_live_run_identity_is_stamped(self):
        p = self.code / "proj-live"
        git(self.repo, "worktree", "add", "-q", "-b", "blive", str(p), "origin/main")
        db = self._identity_db(True, "t_live", 9)
        self.h.handle({"hook_event_name": "PostToolUse", "session_id": "s", "cwd": str(self.repo), "tool_name": "Bash",
                       "tool_input": {"command": f"git worktree add -b blive {p}"}}, "claude",
                      env={"HERMES_KANBAN_TASK": "t_live", "HERMES_KANBAN_RUN_ID": "9", "HERMES_KANBAN_DB": str(db)})
        self.assertEqual(self.marker(p)["task_id"], "t_live")

    def test_ignores_other_commands_and_unnamed_concurrent_trees(self):
        p = self.code / "proj-n"
        git(self.repo, "worktree", "add", "-q", "-b", "bn", str(p), "origin/main")
        self.post("ls -la")
        self.post(f"git worktree add -b other {self.code}/proj-zzz origin/main")
        self.assertIsNone(self.marker(p))

    def test_hermes_core_wt_new_registers_the_tree_it_makes(self):
        # its script runs `git worktree add`; the command the agent ran only says `hermes-core-wt new <slug>`
        p = self.code / "session-outcome-desktop"
        cmd = "~/.hermes/bin/hermes-core-wt new session-outcome-desktop"
        self.post(cmd, tool_use_id="tu-cwt", event="PreToolUse")
        git(self.repo, "worktree", "add", "-q", "-b", "wt/session-outcome-desktop", str(p), "origin/main")
        self.post(cmd, tool_use_id="tu-cwt")
        self.assertEqual(self.marker(p)["agent_session_id"], "sess-1")

    def test_pre_tool_use_stamp_bounds_the_birth_window(self):
        p = self.code / "proj-old"
        git(self.repo, "worktree", "add", "-q", "-b", "bold", str(p), "origin/main")
        time.sleep(1.2)
        self.h.SLACK_S = 0.5
        loop = "for b in x y; do git worktree add -b $b $W/$b; done"
        self.post(loop, tool_use_id="tl", event="PreToolUse")
        self.post(loop, tool_use_id="tl")
        self.assertIsNone(self.marker(p))  # born before this call started: not claimed by a variable loop

    # --- teardown ---------------------------------------------------------------------------------------------------
    def test_session_end_removes_only_merged_clean_trees_of_this_session(self):
        merged = self.make("proj-merged", "bm"); self.commit_push(merged, "m.txt", "bm"); self.merge_to_main("bm")
        pushed = self.make("proj-pushed", "bp"); self.commit_push(pushed, "p.txt", "bp")  # on remote, not merged
        dirty = self.make("proj-dirty", "bd"); self.commit_push(dirty, "d.txt", "bd"); self.merge_to_main("bd")
        (dirty / "wip.txt").write_text("wip\n")
        fresh = self.make("proj-fresh", "bf")  # at origin/main, nothing done: merged, clean -> removable
        other = self.make("proj-other", "bo", sid="sess-2")
        res = self.end()
        acts = {Path(r["path"]).name: r["action"] for r in res}
        self.assertFalse(merged.exists()); self.assertEqual(acts["proj-merged"], "removed")
        self.assertFalse(fresh.exists())
        self.assertTrue(pushed.exists()); self.assertIn("not merged", acts["proj-pushed"])
        self.assertTrue(dirty.exists()); self.assertIn("uncommitted", acts["proj-dirty"])
        self.assertTrue(other.exists()); self.assertNotIn("proj-other", acts)
        self.assertIn("bm", git(self.repo, "branch"))  # branch kept

    def test_session_end_preserves_ignored_data(self):
        p = self.make("proj-ignored", "bignored")
        (p / ".gitignore").write_text("private/\n")
        git(p, "add", ".gitignore"); git(p, "commit", "-qm", "ignore")
        git(p, "push", "-q", "origin", "HEAD:bignored"); self.merge_to_main("bignored")
        (p / "private").mkdir()
        data = p / "private" / "notes.txt"
        data.write_text("irreplaceable\n")
        self.assertEqual(git(p, "status", "--porcelain"), "")
        res = self.end()
        self.assertTrue(p.exists(), res)
        self.assertEqual(data.read_text(), "irreplaceable\n")
        self.assertIn("ignored files", res[0]["action"])

    def test_session_end_keeps_tree_when_ignored_listing_fails(self):
        p = self.make("proj-scan", "bscan")
        original = self.b._git
        def fail_listing(path, *args, **kwargs):
            if "--ignored" in args:
                return subprocess.CompletedProcess(args, 1, "", "scan failed")
            return original(path, *args, **kwargs)
        self.b._git = fail_listing
        self.assertIn("unreadable ignored-file listing", self.end()[0]["action"])
        self.assertTrue(p.exists())

    def test_marker_cannot_redirect_teardown_to_another_worktree(self):
        p = self.make("proj-owner", "bowner")
        other = self.make("proj-other-owner", "bother", sid="sess-2")
        gd = Path(git(p, "rev-parse", "--absolute-git-dir").strip())
        rec = self.marker(p); rec["path"] = str(other)
        (gd / self.b.MARKER).write_text(json.dumps(rec))
        self.end()
        self.assertTrue(other.exists(), "foreign worktree was removed via marker path")
        self.assertTrue(p.exists())

    def test_missing_backlink_keeps_marked_tree(self):
        p = self.make("proj-no-backlink", "bnobacklink")
        gd = Path(git(p, "rev-parse", "--absolute-git-dir").strip())
        (gd / "gitdir").unlink()
        self.end()
        self.assertTrue(p.exists())
        self.assertNotIn("REMOVED", self.log())

    def test_backlink_and_marker_cannot_redirect_to_foreign_gitdir(self):
        p = self.make("proj-forged", "bforged")
        other = self.make("proj-foreign", "bforeign", sid="sess-2")
        gd = Path(git(p, "rev-parse", "--absolute-git-dir").strip())
        rec = self.marker(p); self.assertIsNotNone(rec)
        rec["path"] = str(other)
        (gd / self.b.MARKER).write_text(json.dumps(rec))
        (gd / "gitdir").write_text(str(other / ".git") + "\n")
        self.end()
        self.assertTrue(other.exists())
        self.assertTrue(p.exists())
        self.assertIn("worktree gitdir does not match", self.log())

    def test_unreadable_forward_gitdir_keeps_tree(self):
        p = self.make("proj-broken-forward", "bbrokenforward")
        (p / ".git").write_text("gitdir: /nonexistent/fixture-gitdir\n")
        self.end()
        self.assertTrue(p.exists())
        self.assertIn("worktree gitdir does not match", self.log())

    def test_scan_exception_preserves_tree(self):
        p = self.make("proj-error", "berror")
        def fail_scan(path):
            raise OSError("fixture scan denied")
        self.b._complex_reason = fail_scan
        res = self.end()
        self.assertEqual(res[0]["action"], "kept: scan failed: fixture scan denied")
        self.assertTrue(p.exists())
        self.assertIn("fixture scan denied", self.log())

    def test_scan_exception_does_not_skip_healthy_sibling(self):
        p = self.make("proj-error-first", "berrorfirst")
        sibling = self.make("proj-healthy-second", "bhealthysecond")
        original = self.b._complex_reason
        def fail_one(path):
            if path == str(p):
                raise PermissionError("fixture unreadable subdirectory")
            return original(path)
        self.b._complex_reason = fail_one
        # Force the failing candidate first, independent of filesystem ordering.
        from unittest import mock
        gds = [git(tree, "rev-parse", "--absolute-git-dir").strip() for tree in (p, sibling)]
        common = str(self.repo / ".git")
        with mock.patch.object(self.b, "_common_dirs", return_value=[common]), \
                mock.patch.object(self.h.glob, "glob", return_value=gds):
            res = self.end()
        self.assertTrue(p.exists())
        self.assertFalse(sibling.exists(), res)
        self.assertEqual([r["action"] for r in res],
                         ["kept: scan failed: fixture unreadable subdirectory", "removed"])

    def test_correct_marker_path_with_forged_backlink_is_kept(self):
        p = self.make("proj-correct-marker", "bcorrectmarker")
        other = self.make("proj-forged-backlink", "bforgedbacklink", sid="sess-2")
        gd = Path(git(p, "rev-parse", "--absolute-git-dir").strip())
        self.assertEqual(self.marker(p)["path"], str(p))
        (gd / "gitdir").write_text(str(other / ".git") + "\n")
        # The forward pointer still matches gd: only backlink validation rejects it.
        self.assertEqual(git(p, "rev-parse", "--absolute-git-dir").strip(), str(gd))
        from unittest import mock
        with mock.patch.object(self.h, "remove_if_merged") as remove:
            self.end()
        remove.assert_not_called()
        self.assertTrue(p.exists())
        self.assertTrue(other.exists())
        self.assertIn("marker path does not match gitdir backlink", self.log())

    def test_missing_marker_path_uses_valid_backlink(self):
        p = self.make("proj-no-path", "bnopath")
        gd = Path(git(p, "rev-parse", "--absolute-git-dir").strip())
        rec = self.marker(p); rec.pop("path")
        (gd / self.b.MARKER).write_text(json.dumps(rec))
        self.assertEqual(self.end()[0]["action"], "removed")
        self.assertFalse(p.exists())

    def test_session_end_removes_disposable_ignored_cache(self):
        p = self.make("proj-cache", "bcache")
        (p / ".gitignore").write_text("__pycache__/\n")
        git(p, "add", ".gitignore"); git(p, "commit", "-qm", "cache")
        git(p, "push", "-q", "origin", "HEAD:bcache"); self.merge_to_main("bcache")
        (p / "__pycache__").mkdir()
        (p / "__pycache__" / "module.pyc").write_bytes(b"cache")
        self.assertEqual(self.end()[0]["action"], "removed")
        self.assertFalse(p.exists())

    def test_squash_merged_pr_whose_head_matches_is_removed_even_after_branch_delete(self):
        p = self.make("proj-sq", "bsq"); self.commit_push(p, "s.txt", "bsq")
        git(self.repo, "push", "-q", "origin", "--delete", "bsq")  # GitHub auto-delete after squash merge
        self.prs["bsq"] = git(p, "rev-parse", "HEAD").strip()
        self.assertEqual(self.end()[0]["action"], "removed"); self.assertFalse(p.exists())

    def test_merged_pr_with_newer_local_commits_is_kept(self):
        p = self.make("proj-sq2", "bsq2"); self.commit_push(p, "s.txt", "bsq2")
        self.prs["bsq2"] = git(p, "rev-parse", "HEAD").strip()
        (p / "more.txt").write_text("more\n"); git(p, "add", "more.txt"); git(p, "commit", "-qm", "more")
        self.assertTrue(self.end()[0]["action"].startswith("kept")); self.assertTrue(p.exists())

    def test_live_process_cwd_keeps_the_tree(self):
        p = self.make("proj-busy", "bb")
        self.b.live_cwds = lambda: [str(p)]
        self.assertIn("live process", self.end()[0]["action"]); self.assertTrue(p.exists())

    def test_stop_is_throttled_but_session_end_is_not(self):
        p = self.make("proj-s", "bs")
        self.b.live_cwds = lambda: [str(p)]
        self.assertEqual(len(self.end(event="Stop")), 1)
        self.assertEqual(self.end(event="Stop"), [])  # within the throttle window
        self.assertEqual(len(self.end(event="SessionEnd")), 1)

    def test_overlapping_stop_and_session_end_do_not_double_handle(self):
        import threading
        # merged work, so both events remove it (a fresh tree is kept on Stop; see the born_head tests)
        p = self.make("proj-race", "brace"); self.commit_push(p, "r.txt", "brace"); self.merge_to_main("brace")
        out = []
        ts = [threading.Thread(target=lambda e=e: out.append(self.end(event=e))) for e in ("Stop", "SessionEnd")]
        [t.start() for t in ts]; [t.join() for t in ts]
        acts = [r["action"] for res in out for r in res]
        self.assertEqual(acts, ["removed"]); self.assertFalse(p.exists())

    def test_kanban_owned_tree_waits_for_verified_completion(self):
        p = self.make('proj-worker', 'worker')
        gd = Path(git(p, 'rev-parse', '--absolute-git-dir').strip())
        marker = self.marker(p)
        marker['task_id'] = 'task'
        (gd / self.b.MARKER).write_text(json.dumps(marker))
        self.assertEqual(self.end(), [])
        self.assertTrue(p.exists())

    # --- Stop keeps trees nobody has worked in yet (2026-11-18: a session's prepared trees removed 60 s after its turn)
    def make_detached(self, name, sid="sess-1"):
        p = self.code / name
        cmd = f"cd {self.repo} && git worktree add --detach {p} origin/main"
        self.post(cmd, sid, f"tu-{name}", event="PreToolUse")
        git(self.repo, "worktree", "add", "-q", "--detach", str(p), "origin/main")
        self.post(cmd, sid, f"tu-{name}")
        return p

    def head(self, p):
        return git(p, "rev-parse", "HEAD").strip()

    def log(self):
        return self.b.LOG.read_text() if self.b.LOG.exists() else ""

    def assert_fresh_kept_on_stop_then_removed_on_session_end(self, p):
        self.assertEqual(self.marker(p)["born_head"], self.head(p))
        res = self.end(event="Stop")
        self.assertEqual([r["action"] for r in res], ["kept: no commits since it was made"])
        self.assertTrue(p.exists())
        self.assertIn(f"KEEP {p} (made by claude session sess-1): no commits since it was made; the session is still "
                      f"running", self.log())
        self.assertEqual([r["action"] for r in self.end(event="SessionEnd")], ["removed"])
        self.assertFalse(p.exists())

    def test_fresh_detached_tree_is_kept_on_stop_and_removed_on_session_end(self):
        self.assert_fresh_kept_on_stop_then_removed_on_session_end(self.make_detached("proj-det"))

    def test_fresh_branch_tree_is_kept_on_stop_and_removed_on_session_end(self):
        self.assert_fresh_kept_on_stop_then_removed_on_session_end(self.make("proj-br", "wt/br"))

    def test_tree_with_a_merged_commit_is_removed_on_stop(self):
        p = self.make("proj-mstop", "bms"); self.commit_push(p, "m.txt", "bms"); self.merge_to_main("bms")
        self.assertEqual([r["action"] for r in self.end(event="Stop")], ["removed"]); self.assertFalse(p.exists())

    def test_tree_whose_head_is_a_merged_pr_head_is_removed_on_stop(self):
        p = self.make("proj-prstop", "bps"); self.commit_push(p, "s.txt", "bps")
        git(self.repo, "push", "-q", "origin", "--delete", "bps")
        self.prs["bps"] = self.head(p)
        self.assertEqual([r["action"] for r in self.end(event="Stop")], ["removed"]); self.assertFalse(p.exists())

    def _legacy(self, p, age_s=0.0):
        gd = Path(git(p, "rev-parse", "--absolute-git-dir").strip())
        mk = self.marker(p); mk.pop("born_head")
        (gd / self.b.MARKER).write_text(json.dumps(mk))
        if age_s:
            t = time.time() - age_s
            os.utime(gd / self.b.MARKER, (t, t))

    def test_legacy_marker_younger_than_a_day_at_the_remote_tip_is_kept_on_stop(self):
        p = self.make_detached("proj-leg-young"); self._legacy(p, age_s=23 * 3600)
        res = self.end(event="Stop")
        self.assertTrue(res[0]["action"].startswith("kept: no commits since it was made (legacy marker"))
        self.assertTrue(p.exists())
        self.assertEqual([r["action"] for r in self.end(event="SessionEnd")], ["removed"])

    def test_legacy_marker_older_than_a_day_keeps_todays_rule_on_stop(self):
        p = self.make_detached("proj-leg-old"); self._legacy(p, age_s=25 * 3600)
        self.assertEqual([r["action"] for r in self.end(event="Stop")], ["removed"]); self.assertFalse(p.exists())

    def test_legacy_marker_behind_the_remote_tip_keeps_todays_rule_on_stop(self):
        p = self.make_detached("proj-leg-behind"); self._legacy(p)
        other = self.make("proj-adv", "badv", sid="sess-9"); self.commit_push(other, "n.txt", "badv")
        self.merge_to_main("badv")  # origin/main moved on; the legacy tree is an ancestor, not the tip
        self.assertEqual([r["action"] for r in self.end(event="Stop")], ["removed"]); self.assertFalse(p.exists())

    def test_locked_tree_is_kept_on_both_events(self):
        p = self.make("proj-lock", "blk"); self.commit_push(p, "l.txt", "blk"); self.merge_to_main("blk")
        git(self.repo, "worktree", "lock", "--reason", "subagent at work", str(p))
        for ev in ("Stop", "SessionEnd"):
            self.assertEqual([r["action"] for r in self.end(event=ev)], ["kept: locked (subagent at work)"], ev)
            self.assertTrue(p.exists())
        self.assertIn(f"KEEP {p} (made by claude session sess-1): locked (subagent at work)", self.log())
        git(self.repo, "worktree", "unlock", str(p))
        self.assertEqual([r["action"] for r in self.end(event="SessionEnd")], ["removed"])

    def test_locked_tree_without_a_reason_is_kept(self):
        p = self.make_detached("proj-lock2")
        git(self.repo, "worktree", "lock", str(p))
        self.assertEqual([r["action"] for r in self.end(event="SessionEnd")], ["kept: locked (no reason given)"])
        self.assertTrue(p.exists())

    def test_teardown_argv_carries_the_event_and_old_format_means_stop(self):
        from unittest import mock
        with mock.patch.object(self.h.subprocess, "Popen") as po:
            self.h.handle({"hook_event_name": "SessionEnd", "session_id": "s1", "cwd": "/c"}, "claude", env={})
            self.h.handle({"hook_event_name": "Stop", "session_id": "s2", "cwd": "/c"}, "claude", env={})
        argvs = [c.args[0] for c in po.call_args_list]
        self.assertEqual([self.h.parse_teardown_argv(a[1:])[1:] for a in argvs],
                         [("s1", "/c", self.h.END_DELAY_S, "SessionEnd"), ("s2", "/c", 0.0, "Stop")])
        old = ["agent_hook.py", "--teardown", "claude", "sess-1", str(self.repo), "3.0"]
        self.assertEqual(self.h.parse_teardown_argv(old), ("claude", "sess-1", str(self.repo), 3.0, "Stop"))
        self.assertEqual(self.h.parse_teardown_argv(old + ["bogus"])[-1], "Stop")
        # an old-format child (spawned by a hook that predates the event arg) keeps a fresh tree
        p = self.make_detached("proj-oldargv")
        self.assertEqual(self.h.main(old[:-1] + ["0"]), 0)
        self.assertTrue(p.exists())
        self.assertIn("event=Stop: 1 marked tree(s), 0 removed", self.log())
        self.assertEqual(self.h.main(old[:-1] + ["0", "SessionEnd"]), 0)
        self.assertFalse(p.exists())

    def test_main_never_fails_on_garbage(self):
        self.assertEqual(self.h.handle({}, "claude", env={}), [])
        self.assertEqual(self.h.handle({"hook_event_name": "PostToolUse", "tool_input": None}, "claude", env={}), [])


if __name__ == "__main__":
    unittest.main()
