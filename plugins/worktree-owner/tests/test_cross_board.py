"""A tree an open card on ANOTHER board names as its workspace is never removed, and a recovery agent that
re-adds another card's tree registers it to that card (self-heal kanban-9101461fb6).

Mini, 2026-11-03: a default-board supervisor card re-added ~/code/bb-dev/bb-1175 (the workspace of an open bb-board
card); worktree-owner registered the tree to the supervisor, and its sweep removed it at 09:38 because the
"still used by an open card?" check only read the supervisor's own board.
"""
import json
import sqlite3
import tempfile
import time
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch
from test_worktree_owner import load, git


def board(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as c, c:
        c.execute('create table tasks (id text, status text, current_run_id text, workspace_path text)')
        c.execute('create table task_links (parent_id text, child_id text)')
        c.execute('create table task_runs (id text, task_id text, status text, outcome text, ended_at integer)')


class CrossBoard(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.m = load()
        self.m.KANBAN_ROOT = self.root / 'hermes'
        self.repo = self.root / 'code/babybear'
        self.repo.mkdir(parents=True)
        git(self.repo, 'init', '-q')
        (self.repo / 'a').write_text('base')
        git(self.repo, 'add', '.')
        git(self.repo, 'commit', '-qm', 'base')
        git(self.repo, 'update-ref', 'refs/remotes/origin/main', 'HEAD')
        git(self.repo, 'branch', 'bb-dev/bb-1175')
        self.m.REPO_GLOBS = [str(self.repo / '.git')]
        self.m.live_cwds = lambda: []
        # board A (default): the supervisor card that re-added the tree, now done
        self.a = self.m.KANBAN_ROOT / 'kanban.db'
        board(self.a)
        self.ws = self.root / 'workspaces/super'
        self.ws.mkdir(parents=True)
        with closing(sqlite3.connect(self.a)) as c, c:
            c.execute("insert into tasks values ('super', 'running', 'run1', ?)", (str(self.ws),))
            c.execute("insert into task_runs values ('run1', 'super', 'running', null, null)")
        # board B (bb): the open card whose workspace IS the tree
        self.b = self.m.KANBAN_ROOT / 'kanban/boards/bb/kanban.db'
        board(self.b)
        self.tree = self.root / 'code/bb-dev/bb-1175'
        with closing(sqlite3.connect(self.b)) as c, c:
            c.execute("insert into tasks values ('bbcard', 'scheduled', null, ?)", (str(self.tree),))
            c.execute("insert into task_runs values ('2624', 'bbcard', 'done', 'blocked', 1)")
        self.env = dict(HERMES_KANBAN_TASK='super', HERMES_KANBAN_RUN_ID='run1', HERMES_KANBAN_DB=str(self.a),
                        HERMES_KANBAN_WORKSPACE=str(self.ws))

    def readd(self):
        """The supervisor's recovery command, through the real post_tool_call path."""
        cmd = f'git -C {self.repo} worktree prune && git -C {self.repo} worktree add {self.tree} bb-dev/bb-1175'
        git(self.repo, 'worktree', 'add', '-q', str(self.tree), 'bb-dev/bb-1175')
        return self.m.on_tool_call('terminal', {'command': cmd}, duration_ms=1000, env=self.env)

    def marker(self):
        gd = Path(git(self.tree, 'rev-parse', '--absolute-git-dir').strip())
        return json.loads((gd / self.m.MARKER).read_text())

    def mark_as(self, task, db, run):
        """A tree registered by the pre-fix code: to the supervisor that re-added it."""
        git(self.repo, 'worktree', 'add', '-q', str(self.tree), 'bb-dev/bb-1175')
        gd = Path(git(self.tree, 'rev-parse', '--absolute-git-dir').strip())
        (gd / self.m.MARKER).write_text(json.dumps(dict(task_id=task, board_db=str(db), run_id=run, path=str(self.tree))))

    def finish_super(self, ago_s=3600):
        with closing(sqlite3.connect(self.a)) as c, c:
            c.execute("update tasks set status='done', current_run_id=null where id='super'")
            c.execute("update task_runs set status='done', outcome='completed', ended_at=? where id='run1'",
                      (int(time.time() - ago_s),))

    def complete_super(self):
        return self.m.on_tool_call('kanban_complete', {}, env=self.env,
                                   result=dict(ok=True, task_id='super', run_id='run1'))

    # (b) registration names the card whose workspace the tree is
    def test_readd_of_another_cards_workspace_registers_that_card(self):
        self.readd()
        rec = self.marker()
        self.assertEqual(rec['task_id'], 'bbcard')
        self.assertEqual(rec['board_db'], str(self.b))
        self.assertEqual(str(rec['run_id']), '2624')
        self.assertEqual(rec.get('made_by', {}).get('task_id'), 'super')
        self.assertIn(f'REGISTER {self.tree} owner=bbcard', self.m.LOG.read_text())

    def test_readd_then_supervisor_completes_and_sweeps_keeps_tree(self):
        self.readd()
        self.finish_super()
        self.complete_super()
        self.m.sweep()
        self.assertTrue(self.tree.exists())

    # (a) a tree already registered to a done card on board A, open card on board B: teardown and sweep keep it
    def test_teardown_keeps_tree_an_open_card_on_another_board_uses(self):
        self.mark_as('super', self.a, 'run1')
        self.finish_super(0)
        self.complete_super()
        self.assertTrue(self.tree.exists())
        self.assertIn(f'KEEP {self.tree} (registered to super): shared with an open card', self.m.LOG.read_text())

    def test_sweep_keeps_tree_an_open_card_on_another_board_uses(self):
        self.mark_as('super', self.a, 'run1')
        self.finish_super(3600)
        self.assertEqual([r['action'] for r in self.m.sweep()], ['kept: shared with an open card'])
        self.assertTrue(self.tree.exists())

    def test_tilde_spelling_on_the_other_board_still_counts(self):
        with closing(sqlite3.connect(self.b)) as c, c:
            c.execute("update tasks set workspace_path=?", ('~/' + str(self.tree.relative_to(self.root)),))
        self.mark_as('super', self.a, 'run1')
        self.finish_super(3600)
        home = self.m.os.environ.get('HOME')
        self.m.os.environ['HOME'] = str(self.root)
        try:
            self.m.sweep()
        finally:
            self.m.os.environ['HOME'] = home
        self.assertTrue(self.tree.exists())

    def test_unreadable_other_board_keeps_tree_and_logs_keep(self):
        self.mark_as('super', self.a, 'run1')
        self.finish_super(3600)
        self.b.write_bytes(b'not a sqlite database' * 100)
        self.assertEqual([r['action'] for r in self.m.sweep()], ['kept: a kanban board is unreadable'])
        self.assertTrue(self.tree.exists())
        self.assertIn(f'KEEP {self.tree} (registered to super): cannot read board {self.b}', self.m.LOG.read_text())

    def test_control_tree_removed_once_the_other_boards_card_is_done(self):
        self.mark_as('super', self.a, 'run1')
        self.finish_super(3600)
        with closing(sqlite3.connect(self.b)) as c, c:
            c.execute("update tasks set status='done'")
        self.assertEqual([r['action'] for r in self.m.sweep()], ['removed'])
        self.assertFalse(self.tree.exists())

    def test_live_workspace_paths_covers_every_board(self):
        with patch.dict(self.m.os.environ, {'HERMES_KANBAN_DB': str(self.a)}):
            paths = self.m.live_workspace_paths()
        self.assertIn(str(self.tree), paths)
        self.assertIn(str(self.ws), paths)


if __name__ == '__main__':
    unittest.main()
