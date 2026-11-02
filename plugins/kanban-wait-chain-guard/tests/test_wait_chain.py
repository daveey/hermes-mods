"""Regression tests for self-heal defect agent-ff9c2f154c (hub shepherd card t_e3e216d4, Oct 31 2026).

The shepherd (141 runs) linked itself behind incident t_7e2ee45c (todo), which had linked itself behind
coordinator t_01a194ca90736cea (scheduled), which waited on an unrelated BB324 admission card. Every
kanban_link / kanban_block(kind=dependency) / kanban_schedule(awaiting card) along the chain returned a
plain ok, so each agent read "parked behind an owner" as a started fix.

Fixture: chain A -> B -> C where C (child) parks behind B (todo, waiting on A), and A is scheduled and idle.
Controls: a running parent and a fresh ready parent with no parents get no hint.

    python3 -B -m unittest discover -s plugins/kanban-wait-chain-guard/tests
"""
import importlib.util
import json
import os
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path

PATH = Path(__file__).resolve().parents[1] / '__init__.py'


def _load():
    spec = importlib.util.spec_from_file_location('kanban_wait_chain_guard', PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


guard = _load()

NOW = time.time()
H = 3600.0


def make_board(path, tasks, links=(), runs=(), events=()):
    """tasks: (id, status, created_at, human_request). runs: (task_id, started_at[, outcome]).
    events: (task_id, kind, at[, payload])."""
    c = sqlite3.connect(path)
    c.executescript("""
        CREATE TABLE tasks (id TEXT PRIMARY KEY, title TEXT, status TEXT, created_at INTEGER,
                            human_request TEXT, block_kind TEXT);
        CREATE TABLE task_links (parent_id TEXT, child_id TEXT, PRIMARY KEY (parent_id, child_id));
        CREATE TABLE task_runs (id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT, started_at INTEGER,
                                ended_at INTEGER, outcome TEXT);
        CREATE TABLE task_events (id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT, run_id INTEGER,
                                  kind TEXT, payload TEXT, created_at INTEGER);
    """)
    for tid, status, created, hr in tasks:
        c.execute('INSERT INTO tasks (id, title, status, created_at, human_request) VALUES (?,?,?,?,?)',
                  (tid, tid + ' title', status, int(created), hr))
        c.execute('INSERT INTO task_events (task_id, kind, created_at) VALUES (?,?,?)', (tid, 'created', int(created)))
    c.executemany('INSERT INTO task_links VALUES (?,?)', links)
    for tid, at, *outcome in runs:
        c.execute('INSERT INTO task_runs (task_id, started_at, ended_at, outcome) VALUES (?,?,?,?)',
                  (tid, int(at), int(at) + 60, outcome[0] if outcome else 'scheduled'))
    for tid, kind, at, *payload in events:
        c.execute('INSERT INTO task_events (task_id, kind, created_at, payload) VALUES (?,?,?,?)',
                  (tid, kind, int(at), json.dumps(payload[0]) if payload else None))
    c.commit()
    c.close()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, 'kanban.db')
        self._env = {k: os.environ.get(k) for k in ('HERMES_KANBAN_DB', 'HERMES_KANBAN_TASK')}
        os.environ['HERMES_KANBAN_DB'] = self.db
        os.environ.pop('HERMES_KANBAN_TASK', None)

    def tearDown(self):
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    def call(self, tool, args, result='{"ok": true}'):
        """The tool as the agent sees it: pre-hook refusal, else the (transformed) ok result."""
        pre = guard.pre_tool_call(tool_name=tool, args=args)
        if pre:
            return pre
        out = guard.transform_tool_result(tool_name=tool, args=args, result=result)
        return result if out is None else out


class ParkedChain(Base):
    """A (scheduled, idle 5 days) <- B (todo, waits on A) <- C (the child parking now)."""

    def board(self, child_runs=3):
        make_board(self.db, [('t_A', 'scheduled', NOW - 120 * H, None),
                             ('t_B', 'todo', NOW - 200 * H, None),
                             ('t_C', 'running', NOW - 220 * H, None)],
                   links=[('t_A', 't_B')],
                   runs=[('t_C', NOW - 200 * H + i * H) for i in range(child_runs)])

    def assert_hint(self, out):
        text = out if isinstance(out, str) else json.dumps(out)
        self.assertIn('kanban-wait-chain-guard', text)
        self.assertIn('t_B is itself waiting on t_A', text)
        self.assertIn('parking here is not a started fix', text)
        self.assertIn('idle ', text)

    def test_link_returns_hint(self):
        self.board()
        out = self.call('kanban_link', {'parent_id': 't_B', 'child_id': 't_C'})
        self.assertTrue(json.loads(out)['ok'])
        self.assert_hint(out)

    def test_block_dependency_returns_hint(self):
        self.board()
        sqlite3.connect(self.db).execute("INSERT INTO task_links VALUES ('t_B','t_C')").connection.commit()
        out = self.call('kanban_block', {'task_id': 't_C', 'kind': 'dependency', 'reason': 'wait on B'})
        self.assert_hint(out)

    def test_block_dependency_defaults_to_env_task(self):
        self.board()
        sqlite3.connect(self.db).execute("INSERT INTO task_links VALUES ('t_B','t_C')").connection.commit()
        os.environ['HERMES_KANBAN_TASK'] = 't_C'
        self.assert_hint(self.call('kanban_block', {'kind': 'dependency', 'reason': 'wait'}))

    def test_schedule_awaiting_card_returns_hint(self):
        self.board()
        out = self.call('kanban_schedule', {'task_id': 't_C', 'reason': 'SCHEDULED_UNTIL=x',
                                            'awaiting': {'kind': 'card', 'ref': 't_B'}})
        self.assert_hint(out)

    def test_nested_tool_call_hint(self):
        self.board()
        args = {'calls': [{'name': 'kanban_link', 'arguments': {'parent_id': 't_B', 'child_id': 't_C'}}]}
        out = self.call('tool_call', args, result='[{"ok": true}]')
        self.assertIn('t_B is itself waiting on t_A', out)

    def test_refuses_child_with_20_idle_runs(self):
        self.board(child_runs=20)
        for tool, args in (('kanban_link', {'parent_id': 't_B', 'child_id': 't_C'}),
                           ('kanban_schedule', {'task_id': 't_C', 'awaiting': {'kind': 'card', 'ref': 't_B'}}),
                           ('mcp__tool_call', {'calls': [{'name': 'kanban_link',
                                                          'arguments': {'parent_id': 't_B', 'child_id': 't_C'}}]})):
            with self.subTest(tool=tool):
                out = guard.pre_tool_call(tool_name=tool, args=args)
                self.assertEqual(out['action'], 'block')
                self.assertIn('20 runs', out['message'])
                self.assertIn('t_B is itself waiting on t_A', out['message'])

    def test_runs_before_last_progress_do_not_count(self):
        self.board(child_runs=25)
        sqlite3.connect(self.db).execute(
            "INSERT INTO task_events (task_id, kind, created_at) VALUES ('t_C','review_requested',?)",
            (int(NOW - 1 * H),)).connection.commit()
        self.assertIsNone(guard.pre_tool_call(tool_name='kanban_link', args={'parent_id': 't_B', 'child_id': 't_C'}))

    def test_idle_scheduled_parent_without_parents(self):
        # A alone: scheduled with no progress for 5 days -> hint, naming no grandparent.
        self.board()
        out = self.call('kanban_link', {'parent_id': 't_A', 'child_id': 't_C'})
        self.assertIn('t_A has made no progress', out)


class Controls(Base):
    def test_running_parent_no_hint(self):
        make_board(self.db, [('t_P', 'running', NOW - 300 * H, None), ('t_C', 'running', NOW - 10 * H, None)],
                   runs=[('t_C', NOW - 9 * H + i) for i in range(30)])
        self.assertIsNone(guard.pre_tool_call(tool_name='kanban_link', args={'parent_id': 't_P', 'child_id': 't_C'}))
        self.assertIsNone(guard.transform_tool_result(tool_name='kanban_link',
                                                      args={'parent_id': 't_P', 'child_id': 't_C'}, result='{"ok": true}'))

    def test_ready_parent_without_parents_no_hint(self):
        make_board(self.db, [('t_P', 'ready', NOW - 300 * H, None), ('t_C', 'running', NOW - 10 * H, None)])
        self.assertIsNone(guard.transform_tool_result(tool_name='kanban_link',
                                                      args={'parent_id': 't_P', 'child_id': 't_C'}, result='{"ok": true}'))

    def test_fresh_pipeline_behind_running_head_no_hint(self):
        # Orchestrated pipeline: A running, B todo waiting on A (created 1h ago), C links behind B.
        make_board(self.db, [('t_A', 'running', NOW - 2 * H, None), ('t_B', 'todo', NOW - 1 * H, None),
                             ('t_C', 'running', NOW - 1 * H, None)], links=[('t_A', 't_B')])
        self.assertIsNone(guard.transform_tool_result(tool_name='kanban_link',
                                                      args={'parent_id': 't_B', 'child_id': 't_C'}, result='{"ok": true}'))

    def test_human_ask_parent_no_hint(self):
        # Waiting behind David's blocked ask card is the prescribed pattern, however long it takes.
        make_board(self.db, [('t_ASK', 'blocked', NOW - 300 * H, '{"action": "Reply A or B"}'),
                             ('t_C', 'running', NOW - 300 * H, None)],
                   runs=[('t_C', NOW - 200 * H + i) for i in range(40)])
        args = {'parent_id': 't_ASK', 'child_id': 't_C'}
        self.assertIsNone(guard.pre_tool_call(tool_name='kanban_link', args=args))
        self.assertIsNone(guard.transform_tool_result(tool_name='kanban_link', args=args, result='{"ok": true}'))

    def test_chain_ending_at_human_ask_no_refusal(self):
        # C waits on B (todo, idle) which waits on David's ask: a transitive wait on David, not a watcher loop.
        make_board(self.db, [('t_ASK', 'blocked', NOW - 300 * H, '{"action": "unblock after the password"}'),
                             ('t_B', 'todo', NOW - 200 * H, None), ('t_C', 'running', NOW - 300 * H, None)],
                   links=[('t_ASK', 't_B')], runs=[('t_C', NOW - 200 * H + i) for i in range(40)])
        args = {'parent_id': 't_B', 'child_id': 't_C'}
        self.assertIsNone(guard.pre_tool_call(tool_name='kanban_link', args=args))
        self.assertIsNone(guard.transform_tool_result(tool_name='kanban_link', args=args, result='{"ok": true}'))

    def test_other_tools_and_kinds_untouched(self):
        make_board(self.db, [('t_B', 'todo', NOW - 200 * H, None), ('t_C', 'running', NOW, None)])
        self.assertIsNone(guard.pre_tool_call(tool_name='kanban_block', args={'task_id': 't_C', 'kind': 'needs_input'}))
        self.assertIsNone(guard.pre_tool_call(tool_name='terminal', args={'parent_id': 't_B', 'child_id': 't_C'}))
        self.assertIsNone(guard.pre_tool_call(tool_name='kanban_link', args=None))

    def test_missing_board_fails_open(self):
        os.environ['HERMES_KANBAN_DB'] = os.path.join(self.tmp.name, 'absent.db')
        self.assertIsNone(guard.pre_tool_call(tool_name='kanban_link', args={'parent_id': 't_B', 'child_id': 't_C'}))
        self.assertFalse(os.path.exists(os.environ['HERMES_KANBAN_DB']))

    def test_cross_gateway_awaiting_skipped(self):
        make_board(self.db, [('t_B', 'todo', NOW - 200 * H, None)])
        args = {'task_id': 't_C', 'awaiting': {'kind': 'card', 'ref': 't_B', 'gateway': 'macbook'}}
        self.assertIsNone(guard.transform_tool_result(tool_name='kanban_schedule', args=args, result='{"ok": true}'))

    def test_registration(self):
        hooks = {}

        class Ctx:
            def register_hook(self, name, fn):
                hooks[name] = fn
        guard.register(Ctx())
        self.assertEqual(set(hooks), {'pre_tool_call', 'transform_tool_result'})


class CyclingAuthor(Base):
    """Regression for t_01a19a453972699d (runners board, 2026-11-02 03:20Z).

    Shepherd t_82f80db4 (scheduled, many runs) parked awaiting author t_01a188c7c974c529, which was `todo` only
    because 7s earlier it had linked a fresh reviewer (running) and gone dependency_wait. The guard measured the
    author's idleness from its 81h-old 'created' event and refused: "has made no progress in 81h".
    """
    S, P, R = 't_82f80db4', 't_01a188c7c974c529', 't_01a19a43ef7fe8b9'

    def board(self, reviewer_status='running', fresh=True):
        at = NOW - (0.05 * H if fresh else 100 * H)
        make_board(self.db,
                   [(self.S, 'scheduled', NOW - 220 * H, None), (self.P, 'todo', NOW - 81 * H, None),
                    (self.R, reviewer_status, at, None)],
                   links=[(self.R, self.P)],
                   runs=[(self.S, NOW - 80 * H + i * H) for i in range(30)]
                   + [(self.P, NOW - 30 * H + i * H, 'blocked') for i in range(10)]
                   + ([(self.R, at + 5)] if reviewer_status == 'running' else []),
                   events=[(self.P, 'linked', at, {'parent': self.R, 'child': self.P}),
                           (self.P, 'dependency_wait', at + 7, {'reason': 'await reviewer'})])

    def schedule(self):
        args = {'task_id': self.S, 'reason': 'SCHEDULED_UNTIL=x', 'awaiting': {'kind': 'card', 'ref': self.P}}
        return guard.pre_tool_call(tool_name='kanban_schedule', args=args), \
            guard.transform_tool_result(tool_name='kanban_schedule', args=args, result='{"ok": true}')

    def test_todo_author_behind_fresh_running_reviewer_not_refused(self):
        self.board()
        pre, post = self.schedule()
        self.assertIsNone(pre)
        self.assertIsNone(post)

    def test_long_running_parent_still_counts_via_dependency_wait(self):
        # The reviewer was created long ago but is running now; the author's own dependency_wait on it is fresh.
        self.board(fresh=False)
        sqlite3.connect(self.db).execute("INSERT INTO task_events (task_id, kind, created_at) VALUES (?,?,?)",
                                         (self.P, 'dependency_wait', int(NOW - 0.1 * H))).connection.commit()
        self.assertEqual(self.schedule(), (None, None))

    def test_fresh_link_counts_without_active_parent(self):
        # Reviewer already done (link closed): the fresh 'linked' handoff itself is the author's progress.
        self.board(reviewer_status='done')
        self.assertEqual(self.schedule(), (None, None))

    def test_handoff_run_outcome_counts(self):
        make_board(self.db, [('t_P', 'todo', NOW - 200 * H, None), ('t_C', 'running', NOW - 220 * H, None)],
                   runs=[('t_P', NOW - 2 * H, 'review_requested')] + [('t_C', NOW - 100 * H + i) for i in range(30)])
        self.assertIsNone(guard.pre_tool_call(tool_name='kanban_link', args={'parent_id': 't_P', 'child_id': 't_C'}))

    def test_dependency_wait_behind_parked_parent_still_refused(self):
        # The watcher loop (t_e3e216d4): fresh dependency_waits, but the parent is parked and the link is old.
        make_board(self.db, [('t_A', 'scheduled', NOW - 120 * H, None), ('t_B', 'todo', NOW - 200 * H, None),
                             ('t_C', 'running', NOW - 220 * H, None)],
                   links=[('t_A', 't_B')], runs=[('t_C', NOW - 100 * H + i * H) for i in range(25)],
                   events=[('t_B', 'linked', NOW - 190 * H, {'parent': 't_A', 'child': 't_B'})]
                   + [('t_B', 'dependency_wait', NOW - i * H) for i in range(1, 10)])
        out = guard.pre_tool_call(tool_name='kanban_link', args={'parent_id': 't_B', 'child_id': 't_C'})
        self.assertEqual(out['action'], 'block')
        self.assertIn('t_B is itself waiting on t_A', out['message'])

    def test_dependency_wait_without_parents_still_idle(self):
        make_board(self.db, [('t_B', 'todo', NOW - 200 * H, None), ('t_C', 'running', NOW - 220 * H, None)],
                   runs=[('t_C', NOW - 100 * H + i * H) for i in range(25)],
                   events=[('t_B', 'dependency_wait', NOW - i * H) for i in range(1, 10)])
        out = guard.pre_tool_call(tool_name='kanban_link', args={'parent_id': 't_B', 'child_id': 't_C'})
        self.assertEqual(out['action'], 'block')
        self.assertIn('t_B has made no progress', out['message'])


if __name__ == '__main__':
    unittest.main()
