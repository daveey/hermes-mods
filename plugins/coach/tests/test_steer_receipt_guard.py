"""A steer's own text carries its ACK token, so a count/exists poll over task_comments is a false ACK.

Self-heal agent-6ac431aed9: a watcher ran `count(*) ... body LIKE '%STEER-ACK-tkopark%'`, matched the
supervising chat's own steer comment and printed ack=1 for a worker that died at startup.
"""
import importlib.util
from pathlib import Path
import unittest

HERE = Path(__file__).resolve().parents[1]

INCIDENT = ("sqlite3 ~/.hermes/kanban.db \"select count(*) from task_comments where task_id='t_x' "
            "and body like '%STEER-ACK-tkopark%'\"")


def load(name):
    spec = importlib.util.spec_from_file_location('steer_guard_test_' + name, HERE / (name + '.py'))
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class SteerReceiptGuardTests(unittest.TestCase):
    def test_refuses_ack_polls(self):
        guard = load('steer_receipt_guard').blocked
        for cmd in [
            INCIDENT,
            # shell-wrapped over ssh, escaped quotes
            "ssh hub \"sqlite3 ~/.hermes/kanban.db \\\"SELECT COUNT(*) FROM task_comments WHERE task_id='t_x' "
            "AND body LIKE '%STEER-ACK-tkopark%'\\\"\"",
            "bash -lc 'sqlite3 kanban.db \"select count(id) from task_comments where body like \\'%ACK-abc%\\'\"'",
            # id > steer does not help: own quote / old-run reply still counts
            "sqlite3 k.db \"select count(*) from task_comments where task_id='t' and id > 194775 "
            "and body like '%STEER-ACK-x%'\"",
            "sqlite3 k.db \"select exists(select 1 from task_comments where instr(body, 'STEER-ACK-x'))\"",
            "while ! sqlite3 k.db \"select 1 from task_comments where body glob '*STEER-ACK-x*' limit 1\" | grep -q 1;"
            " do sleep 60; done",
            "n=$(sqlite3 k.db \"select count(*) from task_comments c where c.body like '%ACK-tag%'\"); echo ack=$n",
        ]:
            with self.subTest(cmd=cmd):
                self.assertTrue(guard('terminal', {'command': cmd}))

    def test_allows_diagnostic_reads(self):
        guard = load('steer_receipt_guard').blocked
        for cmd in [
            "sqlite3 k.db \"select id, author, body from task_comments where body like '%STEER-ACK-x%'\"",
            "sqlite3 k.db \"select count(*) from task_comments where task_id='t_x'\"",
            "sqlite3 k.db \"select count(*) from task_events where kind='comment_delivered'\"",
            "python3 ~/.hermes/skills/devops/kanban-orchestrator/scripts/steer_receipt.py --db k.db --task t "
            "--request-comment 5 --run 9 --token STEER-ACK-x",
            "grep -c STEER-ACK-x worker.log",
            "rg 'count\\(\\*\\) from task_comments .*ACK' docs/",
        ]:
            with self.subTest(cmd=cmd):
                self.assertFalse(guard('terminal', {'command': cmd}))

    def test_execute_code(self):
        guard = load('steer_receipt_guard').blocked
        self.assertTrue(guard('execute_code', {'code': (
            "import sqlite3\nc = sqlite3.connect('k.db')\n"
            "n = c.execute(\"SELECT COUNT(*) FROM task_comments WHERE body LIKE '%STEER-ACK-x%'\").fetchone()[0]")}))
        self.assertTrue(guard('execute_code', {'code': 'terminal(command=%r)' % INCIDENT}))
        self.assertFalse(guard('execute_code', {'code': (
            "rows = c.execute(\"select id, body from task_comments where body like '%ACK-x%'\").fetchall()")}))
        self.assertFalse(guard('read_file', {'path': INCIDENT}))
        self.assertFalse(guard('terminal', {'command': None}))
        self.assertFalse(guard('terminal', 'not a dict'))

    def test_message_names_verifier(self):
        msg = load('steer_receipt_guard').MESSAGE
        self.assertIn('steer_receipt.py', msg)
        self.assertIn('writer_run_id', msg)

    @unittest.skipUnless(importlib.util.find_spec('hermes_constants'), 'core-backed hook test')
    def test_registered_hook(self):
        mod = load('__init__')
        import tempfile
        with tempfile.TemporaryDirectory() as root:
            store = mod._load('core').Store(root=Path(root))
            _, hooks = mod.build(store=store, checkout=None)
            result = hooks['pre_tool_call'](tool_name='terminal', args={'command': INCIDENT})
            self.assertIsNotNone(result)
            self.assertEqual(result['action'], 'block')
            self.assertIn('steer_receipt.py', result['message'])
            result = hooks['pre_tool_call'](tool_name='execute_code', args={'code': 'terminal(%r)' % INCIDENT})
            self.assertEqual(result['action'], 'block')
            self.assertIsNone(hooks['pre_tool_call'](tool_name='terminal', args={
                'command': "sqlite3 k.db \"select id, body from task_comments where body like '%ACK-x%'\""}))


if __name__ == '__main__':
    unittest.main()
