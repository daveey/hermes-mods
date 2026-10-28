"""Isolated board + real Git regression tests. Never touches a live board."""
import json
import os
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import time
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
            c.execute('insert into tasks values (?, ?, ?, ?)', ('task', 'done', None, str(self.ws)))
            c.execute('create table task_links (parent_id text, child_id text)')
            c.execute('create table task_runs (id text, task_id text, status text, outcome text, ended_at integer)')
            c.execute("insert into task_runs values ('run', 'task', 'done', 'completed', 1)")
        self.env = dict(HERMES_KANBAN_TASK='task', HERMES_KANBAN_RUN_ID='run', HERMES_KANBAN_DB=str(self.db), HERMES_KANBAN_WORKSPACE=str(self.ws))
        self.result = dict(ok=True, task_id='task', run_id='run')
        self.m.register_markers(0, self.env)
        self.m.live_cwds = lambda: []

    def call(self, result=None, args=None):
        return self.m.on_tool_call('kanban_complete', args or {}, env=self.env, result=result)

    def test_integer_run_id_from_actual_core_contract(self):
        # tasks.current_run_id and tool result run_id are INTEGER, env is text.
        with closing(sqlite3.connect(self.db)) as c, c:
            c.execute('drop table tasks')
            c.execute('create table tasks (id text, status text, current_run_id integer, workspace_path text)')
            c.execute('insert into tasks values (?, ?, ?, ?)', ('task', 'done', None, str(self.ws)))
            c.execute('drop table task_runs')
            c.execute('create table task_runs (id integer, task_id text, status text, outcome text, ended_at integer)')
            c.execute("insert into task_runs values (7, 'task', 'done', 'completed', 1)")
        self.env['HERMES_KANBAN_RUN_ID'] = '7'
        gd = Path(git(self.tree, 'rev-parse', '--absolute-git-dir').strip())
        (gd / self.m.MARKER).unlink()
        self.m.register_markers(0, self.env)
        self.call(dict(ok=True, task_id='task', run_id=7))
        self.assertFalse(self.tree.exists())

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

    def test_active_child_without_workspace_keeps_parent_trees(self):
        with closing(sqlite3.connect(self.db)) as c, c:
            c.execute("insert into tasks values ('child', 'ready', null, null)")
            c.execute("insert into task_links values ('task', 'child')")
        self.call(self.result)
        self.assertTrue(self.tree.exists())

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

    def _commit_ignore(self, patterns):
        """Commit a .gitignore on the tree's branch and 'push' it, so the tree is clean and on a remote."""
        (self.tree / '.gitignore').write_text(''.join(p + '\n' for p in patterns))
        git(self.tree, 'add', '.gitignore')
        git(self.tree, 'commit', '-qm', 'ignore')
        git(self.tree, 'update-ref', 'refs/remotes/origin/topic', 'HEAD')

    def _add_caches(self):
        (self.tree / 'pkg/__pycache__').mkdir(parents=True)
        (self.tree / 'pkg/__pycache__/m.cpython-314.pyc').write_bytes(b'\0pyc')
        for d in ('.pytest_cache', '.ruff_cache', '.mypy_cache'):
            (self.tree / d).mkdir()
            (self.tree / d / 'CACHEDIR.TAG').write_text('Signature: 8a477f597d28d172789f06886806bc55\n')
            (self.tree / d / 'data').write_text('x')
        (self.tree / 'node_modules/.bin').mkdir(parents=True)
        (self.tree / 'node_modules/left-pad').mkdir()
        (self.tree / 'node_modules/left-pad/index.js').write_text('module.exports = 1')
        (self.tree / 'node_modules/.bin/left-pad').symlink_to('../left-pad/index.js')  # npm makes these
        (self.tree / 'out').mkdir()  # any tool's cache dir that carries the standard CACHEDIR.TAG
        (self.tree / 'out/CACHEDIR.TAG').write_text('Signature: 8a477f597d28d172789f06886806bc55\n')
        (self.tree / 'out/blob').write_text('rebuildable')

    def test_rebuildable_caches_do_not_keep_tree(self):
        self._commit_ignore(['__pycache__/', '.pytest_cache/', '.ruff_cache/', '.mypy_cache/', 'node_modules/', 'out/'])
        self._add_caches()
        result = self.call(self.result)
        self.assertEqual([r['action'] for r in result], ['removed'])
        self.assertFalse(self.tree.exists())
        self.assertIn('REMOVED', self.m.LOG.read_text())

    def test_real_ignored_file_keeps_tree_and_logs_why(self):
        self._commit_ignore(['__pycache__/', '.env'])
        self._add_caches()
        (self.tree / '.env').write_text('TOKEN=fixture')
        result = self.call(self.result)
        self.assertTrue(self.tree.exists())
        self.assertEqual((self.tree / '.env').read_text(), 'TOKEN=fixture')
        self.assertIn('ignored', result[0]['action'])
        log = self.m.LOG.read_text()
        self.assertRegex(log, r'KEEP .*tree .*ignored.*\.env')

    def test_real_file_beside_cache_in_ignored_dir_keeps_tree(self):
        self._commit_ignore(['scratch/'])
        (self.tree / 'scratch/__pycache__').mkdir(parents=True)
        (self.tree / 'scratch/__pycache__/x.pyc').write_bytes(b'\0')
        (self.tree / 'scratch/notes.md').write_text('only copy')
        result = self.call(self.result)
        self.assertTrue(self.tree.exists())
        self.assertEqual(result[0]['action'], 'kept: ignored files: scratch/')

    def test_tracked_symlink_does_not_keep_tree(self):
        # babybear commits AppIcon-1024.png as a symlink: a checkout restores it, so it is not data at risk
        (self.tree / 'icon.png').symlink_to('a')
        git(self.tree, 'add', 'icon.png'); git(self.tree, 'commit', '-qm', 'link')
        git(self.tree, 'update-ref', 'refs/remotes/origin/topic', 'HEAD')
        self.call(self.result)
        self.assertFalse(self.tree.exists())

    def test_host_disposable_globs(self):
        # repo-specific build/test artifacts declared per host in <HERMES_HOME>/worktree-owner.json
        home = self.root / 'hh'; home.mkdir()
        (home / 'worktree-owner.json').write_text(json.dumps({'disposable_ignored': ['test_durations.json', '.hermes/bin']}))
        self._commit_ignore(['test_durations.json', '.hermes/', 'pkg.egg-info/'])
        (self.tree / 'test_durations.json').write_text('{}')
        (self.tree / '.hermes/bin').mkdir(parents=True)
        (self.tree / '.hermes/bin/hermes').write_text('#!/bin/sh')
        (self.tree / 'pkg.egg-info').mkdir()
        (self.tree / 'pkg.egg-info/PKG-INFO').write_text('x')
        with patch.dict(os.environ, {'HERMES_HOME': str(home)}):
            (self.tree / '.hermes/config.yaml').write_text('real: setting')  # outside the declared bin/
            self.assertEqual(self.call(self.result)[0]['action'], 'kept: ignored files: .hermes/')
            (self.tree / '.hermes/config.yaml').unlink()
            self.call(self.result)
        self.assertFalse(self.tree.exists())

    def test_tree_from_earlier_run_of_same_card_removed(self):
        # made in run 1, review/retry loop, card completes in run 3
        gd = Path(git(self.tree, 'rev-parse', '--absolute-git-dir').strip())
        rec = json.loads((gd / self.m.MARKER).read_text())
        rec['run_id'] = '1'
        (gd / self.m.MARKER).write_text(json.dumps(rec))
        with closing(sqlite3.connect(self.db)) as c, c:
            c.execute('delete from task_runs')
            c.executemany('insert into task_runs values (?, ?, ?, ?, ?)',
                          [('1', 'task', 'done', 'blocked', 1), ('2', 'task', 'done', 'reclaimed', 2),
                           ('3', 'task', 'done', 'completed', 3)])
        self.env['HERMES_KANBAN_RUN_ID'] = '3'
        self.call(dict(self.result, run_id=3))
        self.assertFalse(self.tree.exists())

    def test_marker_run_of_another_card_still_kept(self):
        gd = Path(git(self.tree, 'rev-parse', '--absolute-git-dir').strip())
        rec = json.loads((gd / self.m.MARKER).read_text())
        rec['run_id'] = '99'  # a run id that is not one of this card's runs
        (gd / self.m.MARKER).write_text(json.dumps(rec))
        with closing(sqlite3.connect(self.db)) as c, c:
            c.execute("insert into task_runs values ('99', 'other', 'done', 'completed', 1)")
        result = self.call(self.result)
        self.assertTrue(self.tree.exists())
        self.assertIn('not a run of', self.m.LOG.read_text())
        self.assertEqual(result, [])

    def test_complex_content_preserved(self):
        for kind in ('ignored', 'symlink', 'nested'):
            with self.subTest(kind=kind):
                if kind == 'ignored':  # a real file (not a cache) that git ignores
                    (self.tree / '.gitignore').write_text('secret\n')
                    (self.tree / 'secret').write_text('valuable')
                elif kind == 'symlink':
                    (self.tree / 'link').symlink_to(self.repo)
                else:
                    (self.tree / 'nested/.git').mkdir(parents=True)
                self.call(self.result)
                self.assertTrue(self.tree.exists())

    def test_staged_and_working_versions_are_not_collapsed(self):
        (self.tree / 'a').write_text('staged version')
        git(self.tree, 'add', 'a')
        (self.tree / 'a').write_text('working version')
        self.call(self.result)
        self.assertTrue(self.tree.exists())
        self.assertEqual(git(self.tree, 'show', ':a').strip(), 'staged version')

    def test_archive_failure_keeps_data(self):
        (self.tree / 'wip').write_text('valuable')
        with patch.object(self.m, '_archive', side_effect=OSError('disk full')):
            self.call(self.result)
        self.assertEqual((self.tree / 'wip').read_text(), 'valuable')

    # --- deferred teardown: trees the completion hook had to keep are reaped once the card settles ---

    def _ended(self, ago_s):
        with closing(sqlite3.connect(self.db)) as c, c:
            c.execute('update task_runs set ended_at=?', (int(time.time() - ago_s),))

    def test_open_child_keep_is_logged_and_sweep_removes_after_child_finishes(self):
        with closing(sqlite3.connect(self.db)) as c, c:
            c.execute("insert into tasks values ('child', 'todo', null, null)")
            c.execute("insert into task_links values ('task', 'child')")
        self._ended(3600)
        self.call(self.result)
        self.assertTrue(self.tree.exists())
        self.assertIn(f'KEEP {self.tree} (registered to task): open child card(s) child', self.m.LOG.read_text())
        self.m.sweep()
        self.assertTrue(self.tree.exists())  # child still open
        with closing(sqlite3.connect(self.db)) as c, c:
            c.execute("update tasks set status='done' where id='child'")
        self.assertEqual([r['action'] for r in self.m.sweep()], ['removed'])
        self.assertFalse(self.tree.exists())

    def test_sweep_reaps_card_closed_without_the_hook_after_grace_only(self):
        self._ended(60)  # e.g. `hermes kanban complete` from a CLI: no tool hook ran
        self.assertEqual(self.m.sweep(), [])  # inside the grace window the completing worker owns it
        self.assertTrue(self.tree.exists())
        for status in ('review', 'running', 'archived'):
            with closing(sqlite3.connect(self.db)) as c, c:
                c.execute('update tasks set status=?', (status,))
            self.assertEqual(self.m.sweep(min_age_s=0), [])
            self.assertTrue(self.tree.exists())
        with closing(sqlite3.connect(self.db)) as c, c:
            c.execute("update tasks set status='done'")
            c.execute("insert into task_runs values ('run2', 'task', 'running', null, null)")
        self.assertEqual(self.m.sweep(min_age_s=0), [])  # latest run not completed
        self.assertTrue(self.tree.exists())
        with closing(sqlite3.connect(self.db)) as c, c:
            c.execute("delete from task_runs where id='run2'")
        self.assertEqual([r['action'] for r in self.m.sweep(min_age_s=0)], ['removed'])

    def test_sweep_leaves_cards_older_than_the_gc_window_to_the_gc(self):
        self._ended(9 * 86400)
        self.assertEqual(self.m.sweep(), [])
        self.assertTrue(self.tree.exists())

    def test_sweep_retries_a_tree_a_process_held_at_completion(self):
        self.m.live_cwds = lambda: [str(self.tree)]
        self.call(self.result)
        self.assertTrue(self.tree.exists())
        self.assertIn('a live process works in it', self.m.LOG.read_text())
        self.m.live_cwds = lambda: []
        self._ended(3600)
        self.assertEqual([r['action'] for r in self.m.sweep()], ['removed'])

    def test_completion_starts_one_detached_sweep_an_hour(self):
        self.call(self.result)
        self.assertEqual(len(self.m.spawned_sweeps), 1)
        with patch.object(self.m.subprocess, 'Popen') as popen, \
                patch.dict(os.environ, {'HERMES_KANBAN_TASK': 'task', 'HERMES_KANBAN_DB': 'x'}):
            self.m._real_spawn_sweep()
            self.m._real_spawn_sweep()
        self.assertEqual(popen.call_count, 1)
        argv, kw = popen.call_args.args[0], popen.call_args.kwargs
        self.assertEqual(argv[-1], 'sweep')
        self.assertTrue(kw['start_new_session'])
        self.assertFalse(any(k.startswith('HERMES_KANBAN_') for k in kw['env']))

    def test_sweep_cli_entry_point(self):
        # the exact child _spawn_sweep starts: `python __init__.py sweep`, with HOME as the scan root
        (self.root / 'code').mkdir()
        (self.root / 'code/repo').symlink_to(self.repo)
        self._ended(3600)
        try:
            load().live_cwds()  # the real scan (setUp stubs self.m.live_cwds)
        except RuntimeError as e:
            self.skipTest(f'this host cannot scan process cwds: {e}')
        env = {k: v for k, v in os.environ.items() if not k.startswith('HERMES_')}
        env['HOME'] = str(self.root)
        # host opt-ins (linux_sudo_readlink) live in the real HERMES_HOME; logs and repos come from HOME
        env['HERMES_HOME'] = os.environ.get('HERMES_HOME') or str(Path.home() / '.hermes')
        r = subprocess.run([sys.executable, '-B', self.m.__file__, 'sweep'], cwd='/', env=env,
                           capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout.splitlines()[0])['action'], 'removed')
        self.assertFalse(self.tree.exists())
        self.assertIn('REMOVED', (self.root / '.hermes/logs/worktree-owner.log').read_text())

    # --- an untracked node_modules symlink (shared install) is not work at risk ---

    def _link_node_modules(self, name='node_modules'):
        target = self.root / 'shared-install'
        target.mkdir(exist_ok=True)
        (target / 'pkg.js').write_text('keep me')
        (self.tree / 'tests').mkdir()
        (self.tree / 'tests' / name).symlink_to(target)
        return target

    def test_untracked_cache_symlink_does_not_keep_tree_and_target_survives(self):
        self._commit_ignore(['node_modules/'])  # a dir rule: git still lists the symlink as untracked
        with closing(sqlite3.connect(self.db)) as c, c:
            c.execute("update tasks set workspace_path=''")  # outside scratch: no archive fallback
        target = self._link_node_modules()
        self.assertIn('?? tests/', git(self.tree, 'status', '--porcelain'))
        result = self.call(self.result)
        self.assertEqual([r['action'] for r in result], ['removed'])
        self.assertFalse(self.tree.exists())
        self.assertEqual((target / 'pkg.js').read_text(), 'keep me')

    def test_other_untracked_symlink_still_keeps_tree(self):
        self._commit_ignore([])
        with closing(sqlite3.connect(self.db)) as c, c:
            c.execute("update tasks set workspace_path=''")
        self._link_node_modules('data')
        self.call(self.result)
        self.assertTrue((self.tree / 'tests/data').is_symlink())
        self.assertIn('nested git or symlink: tests/data', self.m.LOG.read_text())


class LinuxCwd(unittest.TestCase):
    def test_opt_in_sudo_readlink_fallback_is_narrow_and_fail_closed(self):
        m = load()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / '42').mkdir()
            (root / '42/stat').write_text('42 (test) S ' + '0 ' * 30)
            with patch.object(m, '_sudo_proc_enabled', return_value=True, create=True), \
                 patch.object(m.os, 'readlink', side_effect=PermissionError()), \
                 patch.object(m.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, '/fixture\n', '')) as run:
                self.assertEqual(m._proc_cwds(tmp), ['/fixture'])
                self.assertEqual(run.call_args.args[0], ['sudo', '-n', '--', '/usr/bin/readlink', '--', str(root / '42/cwd')])
                run.return_value = subprocess.CompletedProcess([], 1, '', 'denied')
                with self.assertRaises(RuntimeError):
                    m._proc_cwds(tmp)


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
