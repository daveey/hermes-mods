"""worktree-owner: creator-side registration + removal of git worktrees made by Hermes sessions/cards.

Run: python3 -B -m unittest discover -s plugins/worktree-owner/tests   (stdlib only; temp git repos)
"""
import importlib.util, json, os, subprocess, tempfile, time, unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


class CwdPortability(unittest.TestCase):
    def test_lsof_resolved_from_path(self):
        m = load()
        with patch("shutil.which", return_value="/usr/bin/lsof"), \
             patch.object(m.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "p123\nn/fixture/tree\n", "")) as run:
            self.assertEqual(m.live_cwds(), ["/fixture/tree"])
            self.assertEqual(run.call_args.args[0][0], "/usr/bin/lsof")

    def test_lsof_failures_never_authorize_removal(self):
        m = load()
        for result in (subprocess.CompletedProcess([], 1, "", ""),
                       subprocess.CompletedProcess([], 0, "n/partial\n", "permission denied"),
                       subprocess.CompletedProcess([], 0, "", "")):
            with self.subTest(result=result), patch("shutil.which", return_value="/usr/bin/lsof"), \
                 patch.object(m.subprocess, "run", return_value=result), \
                 patch.object(m, "_git") as git_call:
                with self.assertRaises(RuntimeError):
                    m._remove("/fixture/tree", "fixture")
                git_call.assert_not_called()

    def test_missing_or_timed_out_lsof_never_authorizes_removal(self):
        m = load()
        for error in (FileNotFoundError(), subprocess.TimeoutExpired("lsof", 120)):
            with self.subTest(error=error), patch.object(m.subprocess, "run", side_effect=error), \
                 patch.object(m, "_git") as git_call:
                with self.assertRaises((OSError, RuntimeError, subprocess.TimeoutExpired)):
                    m._remove("/fixture/tree", "fixture")
                git_call.assert_not_called()


class BirthPortability(unittest.TestCase):
    def test_native_birth_and_stat_failures(self):
        m = load()
        with patch.object(m.os, "stat", return_value=SimpleNamespace(st_birthtime=123, st_ctime=999)), \
             patch.object(m.subprocess, "run") as run:
            self.assertEqual(m._birth("/fixture/gitdir"), 123)
            run.assert_not_called()
        with tempfile.TemporaryDirectory() as root:
            for failure in (FileNotFoundError(), subprocess.TimeoutExpired("stat", 20)):
                with self.subTest(failure=failure), \
                     patch.object(m.os, "stat", return_value=SimpleNamespace(st_ctime=999)), \
                     patch.object(m.subprocess, "run", side_effect=failure):
                    self.assertIsNone(m._birth(root))

    def test_linux_uses_birth_not_recent_metadata_change(self):
        m = load()
        with patch.object(m.os, "stat", return_value=SimpleNamespace(st_ctime=999)), \
             patch.object(m.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "123\n", "")) as run:
            self.assertEqual(m._birth("/fixture/gitdir"), 123)
            self.assertEqual(run.call_args.args[0], ["stat", "-c", "%W", "--", "/fixture/gitdir"])


    def test_reflog_fallback_requires_creation_record(self):
        m = load()
        with tempfile.TemporaryDirectory() as root:
            gd = Path(root)
            (gd / "logs").mkdir()
            log = gd / "logs" / "HEAD"
            creation = "0" * 40 + " " + "a" * 40 + " User <u@example.com> 123 +0000\t\n"
            with patch.object(m.os, "stat", return_value=SimpleNamespace(st_ctime=999)), \
                 patch.object(m.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "0\n", "")):
                log.write_text(creation + "later entry\n")
                self.assertEqual(m._birth(root), 123)
                log.write_text(creation.replace("0" * 40, "b" * 40))
                self.assertIsNone(m._birth(root))  # expired initial reflog is not creation
                log.write_text("malformed\n")
                self.assertIsNone(m._birth(root))
                log.unlink()
                self.assertIsNone(m._birth(root))

    def test_unknown_birth_skips_candidate_without_breaking_scan(self):
        m = load()
        with tempfile.TemporaryDirectory() as root:
            for name in ("unknown", "known"):
                Path(root, "worktrees", name).mkdir(parents=True)
            with patch.object(m, "_common_dirs", return_value=[root]), \
                 patch.object(m, "_birth", side_effect=lambda p: None if p.endswith("unknown") else 123):
                self.assertEqual(m.new_gitdirs(100), [str(Path(root, "worktrees", "known"))])

SRC = Path(__file__).resolve().parent.parent / "__init__.py"
ENV = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")


def git(cwd, *a):
    return subprocess.run(["git", "-C", str(cwd), *a], check=True, capture_output=True, text=True, env=ENV).stdout


def load():
    spec = importlib.util.spec_from_file_location("worktree_owner_t", SRC)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class Owner(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.m = load()
        self.code = self.tmp / "code"
        self.code.mkdir()
        self.m.REPO_GLOBS = [str(self.code / "*" / ".git"), str(self.code / "*" / "*" / ".git")]
        self.m.LOG = self.tmp / "owner.log"
        self.m.live_workspace_paths = lambda exclude=None: set(self.live)
        self.live = []
        self.m.live_cwds = lambda: []
        bare = self.tmp / "remote.git"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True)
        self.repo = self.code / "proj"
        git(self.tmp, "clone", "-q", str(bare), str(self.repo))
        (self.repo / "a.txt").write_text("a\n")
        git(self.repo, "add", "."); git(self.repo, "commit", "-qm", "init"); git(self.repo, "push", "-q", "origin", "HEAD:main")
        git(self.repo, "fetch", "-q")

    def make(self, path, branch, env, cmd=None):
        """Run `git worktree add` the way an agent does, then fire the hook as the terminal tool would."""
        t0 = time.time()
        git(self.repo, "worktree", "add", "-q", "-b", branch, str(path), "origin/main")
        cmd = cmd or f"cd {self.repo} && git worktree add -b {branch} {path} origin/main"
        self.m.on_tool_call("terminal", {"command": cmd}, duration_ms=int((time.time() - t0) * 1000) + 50, env=env)
        return path

    def marker(self, path):
        gd = git(path, "rev-parse", "--absolute-git-dir").strip()
        p = Path(gd) / self.m.MARKER
        return json.loads(p.read_text()) if p.exists() else None

    def test_registers_owner_card_and_session(self):
        p = self.make(self.code / "proj-x", "bx", {"HERMES_KANBAN_TASK": "t_aaaa1111", "HERMES_SESSION_ID": "s1"})
        mk = self.marker(p)
        self.assertEqual(mk["task_id"], "t_aaaa1111"); self.assertEqual(mk["session_id"], "s1")
        self.assertEqual(os.path.realpath(mk["path"]), os.path.realpath(p))

    def test_ignores_commands_without_worktree_add(self):
        p = self.code / "proj-y"
        git(self.repo, "worktree", "add", "-q", "-b", "by", str(p), "origin/main")
        self.m.on_tool_call("terminal", {"command": "ls -la"}, duration_ms=10, env={"HERMES_KANBAN_TASK": "t_x"})
        self.assertIsNone(self.marker(p))

    def test_registers_for_loop_fanout_and_nested_scratch_worktrees(self):
        ws = self.tmp / "kanban" / "workspaces" / "t_bbbb2222"; ws.mkdir(parents=True)
        t0 = time.time()
        for b in ("a1", "a2"):
            git(self.repo, "worktree", "add", "-q", "-b", f"ws/{b}", str(ws / b), "origin/main")
        self.m.on_tool_call("terminal", {"command": "for b in a1 a2; do git worktree add -q -b ws/$b $W/$b origin/main; done"},
                            duration_ms=int((time.time() - t0) * 1000) + 50, env={"HERMES_KANBAN_TASK": "t_bbbb2222"})
        self.assertEqual(self.marker(ws / "a1")["task_id"], "t_bbbb2222"); self.assertEqual(self.marker(ws / "a2")["task_id"], "t_bbbb2222")

    def test_complete_removes_own_clean_pushed_worktrees_only(self):
        env = {"HERMES_KANBAN_TASK": "t_cccc3333"}
        mine = self.make(self.code / "proj-mine", "bm", env)
        dirty = self.make(self.code / "proj-dirty", "bd", env); (dirty / "new.txt").write_text("wip\n")
        unpushed = self.make(self.code / "proj-unp", "bu", env)
        (unpushed / "c.txt").write_text("c\n"); git(unpushed, "add", "c.txt"); git(unpushed, "commit", "-qm", "c")
        other = self.make(self.code / "proj-other", "bo", {"HERMES_KANBAN_TASK": "t_dddd4444"})
        chat = self.make(self.code / "proj-chat", "bc", {"HERMES_SESSION_ID": "chat1"})
        res = self.m.on_tool_call("kanban_complete", {"summary": "done"}, env=env)
        self.assertFalse(mine.exists())
        for p in (dirty, unpushed, other, chat):
            self.assertTrue(p.exists(), p)
        self.assertNotIn(str(mine), git(self.repo, "worktree", "list"))
        self.assertIn("bm", git(self.repo, "branch"))  # branch kept: nothing is lost
        kinds = {Path(r["path"]).name: r["action"] for r in res}
        self.assertEqual(kinds["proj-mine"], "removed"); self.assertTrue(kinds["proj-dirty"].startswith("kept"))

    def test_complete_does_nothing_outside_a_kanban_worker(self):
        p = self.make(self.code / "proj-c2", "bc2", {"HERMES_SESSION_ID": "chat2"})
        self.assertEqual(self.m.on_tool_call("kanban_complete", {}, env={"HERMES_SESSION_ID": "chat2"}), [])
        self.assertTrue(p.exists())

    def test_complete_removes_own_dir_workspace_worktree_unless_another_live_card_uses_it(self):
        # a chat made the worktree and handed it to this card as kind=dir; the card's completion retires it
        ws = self.make(self.code / "proj-dir", "bdir", {"HERMES_SESSION_ID": "chat3"})
        env = {"HERMES_KANBAN_TASK": "t_eeee5555", "HERMES_KANBAN_WORKSPACE": str(ws)}
        self.live = [os.path.realpath(ws)]  # a sibling card still open on the same dir
        self.m.on_tool_call("kanban_complete", {}, env=env)
        self.assertTrue(ws.exists())
        self.live = []
        self.m.on_tool_call("kanban_complete", {}, env=env)
        self.assertFalse(ws.exists())

    def test_does_not_claim_a_concurrent_foreign_worktree(self):
        # another agent (Claude Code, a chat) creates a tree in the same seconds; its name is not in our command
        t0 = time.time()
        git(self.repo, "worktree", "add", "-q", "-b", "bmine", str(self.code / "proj-mine2"), "origin/main")
        git(self.repo, "worktree", "add", "-q", "-b", "bforeign", str(self.code / "proj-foreign"), "origin/main")
        self.m.on_tool_call("terminal", {"command": f"git worktree add -b bmine {self.code}/proj-mine2 origin/main"},
                            duration_ms=int((time.time() - t0) * 1000) + 50, env={"HERMES_KANBAN_TASK": "t_ffff6666"})
        self.assertEqual(self.marker(self.code / "proj-mine2")["task_id"], "t_ffff6666")
        self.assertIsNone(self.marker(self.code / "proj-foreign"))

    def test_complete_keeps_tree_a_live_process_works_in(self):
        env = {"HERMES_KANBAN_TASK": "t_gggg7777"}
        p = self.make(self.code / "proj-busy", "bbusy", env)
        self.m.live_cwds = lambda: [str(p / "sub")]
        res = self.m.on_tool_call("kanban_complete", {}, env=env)
        self.assertTrue(p.exists()); self.assertIn("live process", res[0]["action"])

    def test_dirty_tree_nested_in_scratch_is_archived_before_the_scratch_is_deleted(self):
        # the dispatcher rmtrees a scratch workspace on completion; a dirty nested tree must be saved first
        ws = self.tmp / "kanban" / "workspaces" / "t_hhhh8888"; ws.mkdir(parents=True)
        env = {"HERMES_KANBAN_TASK": "t_hhhh8888", "HERMES_KANBAN_WORKSPACE": str(ws)}
        p = self.make(ws / "repo", "bnest", env); (p / "wip.txt").write_text("unsaved\n")
        self.m.ARCHIVE = self.tmp / "archive"
        res = self.m.on_tool_call("kanban_complete", {}, env=env)
        self.assertEqual(res[0]["action"], "archived+removed")
        arch = list(self.m.ARCHIVE.iterdir())[0]
        self.assertTrue((arch / "untracked.tar.gz").exists())
        self.assertNotIn(str(p), git(self.repo, "worktree", "list"))

    def test_execute_code_with_braces_does_not_claim_unnamed_trees(self):
        t0 = time.time()
        git(self.repo, "worktree", "add", "-q", "-b", "bxx", str(self.code / "proj-unnamed"), "origin/main")
        self.m.on_tool_call("execute_code", {"code": "d = {'a': 1}\nsubprocess.run(['git', 'worktree', 'add', path])"},
                            duration_ms=int((time.time() - t0) * 1000) + 50, env={"HERMES_KANBAN_TASK": "t_iiii9999"})
        self.assertIsNone(self.marker(self.code / "proj-unnamed"))

    def test_hook_never_raises(self):
        self.m.REPO_GLOBS = ["/nonexistent/*/.git"]
        self.assertEqual(self.m.on_tool_call("terminal", {"command": "git worktree add x"}, duration_ms=5, env={}), [])
        self.assertEqual(self.m._post_tool_call(tool_name="terminal", args=None), None)


if __name__ == "__main__":
    unittest.main()
