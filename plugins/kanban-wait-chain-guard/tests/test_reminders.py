"""Owed reminders must wake before the final response deadline."""
import importlib.util
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('reminder_guard', Path(__file__).resolve().parents[1] / '__init__.py')
assert spec is not None and spec.loader is not None
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)
T = 1800000000
H = 3600
BODY = ('Park now with SCHEDULED_UNTIL=<T+72h>.\n'
        'If there is no reply, nudge once on #17 after 24 h. At the 72 h deadline, then complete.')


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).isoformat().replace('+00:00', 'Z')


class Reminders(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'board.db'
        self.conn = sqlite3.connect(self.path)
        self.addCleanup(self.conn.close)
        self.conn.executescript('CREATE TABLE tasks (id TEXT, body TEXT); CREATE TABLE task_events '
                                '(id INTEGER PRIMARY KEY, task_id TEXT, kind TEXT, created_at INTEGER);')
        self.conn.execute('INSERT INTO tasks VALUES (?, ?)', ('t_fixture', BODY))
        self.event('created', T - H)
        self.path_patch = patch.object(guard, '_db_path', return_value=self.path)
        self.path_patch.start()
        self.addCleanup(self.path_patch.stop)

    def event(self, kind, at):
        self.conn.execute('INSERT INTO task_events(task_id, kind, created_at) VALUES (?, ?, ?)',
                          ('t_fixture', kind, at))
        self.conn.commit()

    def body(self, text):
        self.conn.execute('UPDATE tasks SET body=?', (text,))
        self.conn.commit()

    def call(self, hours: float = 72, now: float = T, reason=None, tool='kanban_schedule', nested=False):
        args = {'task_id': 't_fixture', 'board': 'fixture',
                'reason': reason if reason is not None else 'SCHEDULED_UNTIL=' + iso(T + hours * H)}
        if nested:
            tool, args = 'tool_call', {'calls': [{'name': 'mcp__kanban_schedule', 'arguments': args}]}
        with patch.object(guard.time, 'time', return_value=now):
            return guard.pre_tool_call(tool_name=tool, args=args)

    def test_first_park_refused(self):
        result = self.call()
        self.assertEqual(result['action'], 'block')
        self.assertIn('due ' + iso(T + 24 * H), result['message'])

    def test_due_wake_allowed(self):
        self.assertIsNone(self.call(24))

    def test_default_wake_allowed(self):
        self.assertIsNone(self.call(reason='Wait for response'))

    def test_repark_keeps_first_anchor(self):
        self.event('scheduled', T)
        self.event('scheduled', T + H)
        self.assertIn('due ' + iso(T + 24 * H), self.call(now=T + 3.5 * H)['message'])

    def test_sent_nudge_bypass(self):
        self.event('scheduled', T)
        self.assertIsNone(self.call(now=T + 25 * H, reason='nudged; SCHEDULED_UNTIL=' + iso(T + 72 * H)))

    def test_overdue_reminder_refused(self):
        self.event('scheduled', T)
        self.assertIn('send it now', self.call(now=T + 25 * H)['message'])

    def test_old_wait_allowed(self):
        self.event('scheduled', T)
        self.assertIsNone(self.call(now=T + 49 * H))

    def test_no_reminder_allowed(self):
        self.body('Wait 24 h for a response')
        self.assertIsNone(self.call())

    def test_no_duration_allowed(self):
        self.body('Nudge once if silent')
        self.assertIsNone(self.call())

    def check_duration(self, text, hours):
        self.body(text)
        self.assertIn('due ' + iso(T + hours * H), self.call(120)['message'])

    def test_hours_before_verb(self):
        self.check_duration('If she is silent 24 h after an ask, nudge once', 24)

    def test_days(self):
        self.check_duration('remind them in 2 days', 48)

    def test_compact_hours(self):
        self.check_duration('ping again after 48h', 48)

    def test_compact_days(self):
        self.check_duration('follow up in 3d', 72)

    def test_nested_deferred(self):
        self.assertEqual(self.call(nested=True)['action'], 'block')

    def test_missing_board(self):
        with patch.object(guard, '_db_path', return_value=self.path.with_name('missing.db')):
            self.assertIsNone(self.call())

    def test_locked_board(self):
        self.conn.execute('BEGIN EXCLUSIVE')
        self.addCleanup(self.conn.rollback)
        self.assertIsNone(self.call())

    def test_parse_error(self):
        self.assertIsNone(self.call(reason='SCHEDULED_UNTIL=invalid'))

    def test_schedule_only(self):
        self.assertIsNone(self.call(tool='kanban_block'))

    def test_reset_events(self):
        for kind in ('created', 'edited', 'specified', 'unblocked'):
            with self.subTest(kind=kind):
                self.conn.execute('DELETE FROM task_events')
                self.event('scheduled', T - 10 * H)
                self.event(kind, T - H)
                self.event('scheduled', T)
                self.assertIn('due ' + iso(T + 24 * H), self.call(now=T + H)['message'])

    def test_smallest_decimal_duration_and_case(self):
        self.check_duration('REMINDER in 2 days\nRe-ping after 1.5 hrs or 3 hours', 1.5)

    def test_grace_boundary(self):
        self.assertIsNone(self.call(24.5))
        self.assertIsNotNone(self.call(24.5 + 1 / H))

    def test_old_wait_boundary(self):
        self.event('scheduled', T)
        self.assertIsNotNone(self.call(now=T + 48 * H))
        self.assertIsNone(self.call(now=T + 48 * H + 1))

    def test_environment_task(self):
        with patch.dict('os.environ', {'HERMES_KANBAN_TASK': 't_fixture'}), patch.object(guard.time, 'time', return_value=T):
            result = guard.pre_tool_call(tool_name='kanban_schedule', args={'reason': 'SCHEDULED_UNTIL=' + iso(T + 72 * H)})
        self.assertEqual(result['action'], 'block')

    def test_line_excerpt_bounded(self):
        self.body('nudge in 24 h ' + 'x' * 200)
        self.assertNotIn('x' * 160, self.call()['message'])
