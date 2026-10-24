"""Isolated board + real Git regression tests. Never touches a live board."""
import json
import os
import sqlite3
import subprocess
import tarfile
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch
from test_worktree_owner import load, git


class Lifecycle(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.m = load()
        self.m.LOG = self.root / 'log'
        self.m.ARCHIVE = self.root / 'archive'
        self.repo = self.root / 'repo'
        self.repo.mkdir()
        git(self.repo, 'init', '-q')
        (self.repo / 'a').write_text('base')
        git(self.repo, 'add', '.')
        git(self.repo, 'commit', '-qm', 'base')
        git(self.repo, 'update-ref', 'refs/remotes/origin/main', 'HEAD')
        self.ws = self.root / 'kanban/workspaces/task'
        self.ws.mkdir(parents=True)
        self.tree = self.ws / 'tree'
        git(self.repo, 'worktree', 'add', '-qb', 'topic', str(self.tree))
        self.m.REPO_GLOBS = [str(self.repo / '.git')]
        self.db = self.root / 'board.db'
        with closing(sqlite3.connect(self.db)) as c, c:
            c.execute('create table tasks (id text, status text, current_run_id text, workspace_path text)')
            c.execute('insert into tasks values (?, ?, ?, ?)', ('task', 'done', 'run', str(self.ws)))
        self.env = dict(HERMES_KANBAN_TASK='task', HERMES_KANBAN_RUN_ID='run', HERMES_KANBAN_DB=str(self.db), HERMES_KANBAN_WORKSPACE=str(self.ws))
        self.result = dict(ok=True, task_id='task', run_id='run')
        self.m.register_markers(0, self.env)
        self.m.live_cwds = lambda: []

    def call(self, result=None, args=None):
        return self.m.on_tool_call('kanban_complete', args or {}, env=self.env, result=result)

    def test_result_gate(self):
        for result in (None, {}, {'ok': False}, dict(self.result, error='failed'), dict(self.result, task_id='other'), dict(self.result, run_id='old')):
            self.assertEqual(self.call(result), [])
            self.assertTrue(self.tree.exists())
        self.assertEqual(self.call(self.result, {'board': 'other'}), [])

    def test_authoritative_state_and_run(self):
        for status, run in [('review', 'run'), ('running', 'run'), ('done', 'old')]:
            with closing(sqlite3.connect(self.db)) as c, c:
                c.execute('update tasks set status=?, current_run_id=?', (status, run))
            self.assertEqual(self.call(self.result), [])
            self.assertTrue(self.tree.exists())

    def test_actual_hook_forwards_result(self):
        with patch.dict(os.environ, self.env):
            self.m._post_tool_call(tool_name='kanban_complete', args={}, result=json.dumps(self.result))
        self.assertFalse(self.tree.exists())

    def test_legacy_marker_kept(self):
        gd = Path(git(self.tree, 'rev-parse', '--absolute-git-dir').strip())
        (gd / self.m.MARKER).write_text(json.dumps(dict(task_id='task', path=str(self.tree))))
        self.call(self.result)
        self.assertTrue(self.tree.exists())

    def test_legacy_marker_on_own_workspace_kept(self):
        with closing(sqlite3.connect(self.db)) as c, c:
            c.execute('update tasks set workspace_path=?', (str(self.tree),))
        self.test_legacy_marker_kept()

    def test_shared_parent_and_child_keep_marked_tree(self):
        for shared in (self.ws, self.tree / 'child'):
            with closing(sqlite3.connect(self.db)) as c, c:
                c.execute("delete from tasks where id='other'")
                c.execute('insert into tasks values (?, ?, ?, ?)', ('other', 'running', 'other', str(shared)))
            self.call(self.result)
            self.assertTrue(self.tree.exists())

    def test_lock_held_through_remove(self):
        original = self.m._remove
        def remove(*args, **kwargs):
            with closing(sqlite3.connect(self.db, timeout=0)) as c, c:
                with self.assertRaises(sqlite3.OperationalError):
                    c.execute("update tasks set status='running'")
            return original(*args, **kwargs)
        with patch.object(self.m, '_remove', side_effect=remove):
            self.call(self.result)
        self.assertFalse(self.tree.exists())

    def test_unreadable_board_keeps_tree(self):
        self.db.unlink()
        self.call(self.result)
        self.assertTrue(self.tree.exists())
        self.assertFalse(self.db.exists())

    def test_remote_command_never_claims_local_tree(self):
        gd = Path(git(self.tree, 'rev-parse', '--absolute-git-dir').strip())
        (gd / self.m.MARKER).unlink()
        self.m.on_tool_call('terminal', {'command': 'ssh example.com "git worktree add tree"'}, env=self.env)
        self.assertFalse((gd / self.m.MARKER).exists())

    def test_recovery_self_contained(self):
        (self.tree / 'a').write_text('commit')
        git(self.tree, 'commit', '-qam', 'local')
        head = git(self.tree, 'rev-parse', 'HEAD').strip()
        (self.tree / 'a').write_text('dirty')
        (self.tree / 'untracked').write_text('unsaved')
        result = self.call(self.result)
        self.assertEqual(result[0]['action'], 'archived+removed')
        archive = Path(result[0]['archive'])
        restored = self.root / 'restored'
        git(self.root, 'clone', str(archive / 'unpushed.bundle'), str(restored))
        self.assertEqual(git(restored, 'rev-parse', 'HEAD').strip(), head)
        subprocess.run(['git', '-C', str(restored), 'apply', str(archive / 'uncommitted.diff')], check=True)
        with tarfile.open(archive / 'untracked.tar.gz') as tar:
            tar.extractall(restored, filter='data')
        self.assertEqual((restored / 'a').read_text(), 'dirty')
        self.assertEqual((restored / 'untracked').read_text(), 'unsaved')

    def test_complex_content_preserved(self):
        for kind in ('ignored', 'symlink', 'nested'):
            with self.subTest(kind=kind):
                if kind == 'ignored':
                    (self.tree / '.gitignore').write_text('secret\n')
                    (self.tree / 'secret').write_text('valuable')
                elif kind == 'symlink':
                    (self.tree / 'link').symlink_to(self.repo)
                else:
                    (self.tree / 'nested/.git').mkdir(parents=True)
                self.call(self.result)
                self.assertTrue(self.tree.exists())

    def test_archive_failure_keeps_data(self):
        (self.tree / 'wip').write_text('valuable')
        with patch.object(self.m, '_archive', side_effect=OSError('disk full')):
            self.call(self.result)
        self.assertEqual((self.tree / 'wip').read_text(), 'valuable')


class LinuxCwd(unittest.TestCase):
    def test_proc_unknown_permission_and_zombie(self):
        m = load()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for pid, state in [('1', 'S'), ('2', 'Z')]:
                (root / pid).mkdir()
                (root / pid / 'stat').write_text(f'{pid} (test) {state} 0')
            (root / '1/cwd').symlink_to(Path.cwd())
            self.assertEqual(m._proc_cwds(tmp), [str(Path.cwd())])
            (root / '2/stat').write_text('2 (test) S 0')
            with self.assertRaises(RuntimeError):
                m._proc_cwds(tmp)
            with patch.object(m.os, 'readlink', side_effect=PermissionError()):
                with self.assertRaises(RuntimeError):
                    m._proc_cwds(tmp)
            # Other users are not inspected, even when cwd is unreadable.
            with patch.object(m.os, 'getuid', return_value=-1):
                with self.assertRaisesRegex(RuntimeError, 'empty'):
                    m._proc_cwds(tmp)

    @unittest.skipUnless(os.path.isdir('/proc'), 'Linux only')
    def test_real_scan_without_lsof(self):
        m = load()
        with patch.object(m.shutil, 'which', return_value=None):
            try:
                paths = m.live_cwds()
            except RuntimeError as error:
                # Non-dumpable same-UID processes (e.g. systemd --user) can deny
                # readlink even to their owner. This host must preserve trees.
                self.assertIn('cannot read cwd for live pid', str(error))
            else:
                self.assertIn(os.getcwd(), paths)
