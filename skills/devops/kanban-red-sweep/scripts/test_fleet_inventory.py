import importlib.util
import json
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import unittest
from types import SimpleNamespace

spec = importlib.util.spec_from_file_location('fleet', Path(__file__).with_name('fleet_inventory.py'))
fleet = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fleet)


def board(path, status='blocked', owner='remote-a'):
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as c:
        c.executescript('create table tasks(id,status,block_kind,title,created_on);'
                        'create table task_events(id,task_id,kind);')
        c.execute('insert into tasks values (?,?,?,?,?)', ('t_one', status, 'needs_input', 'Title', owner))
        c.execute("insert into task_events values (7,'t_one','blocked')")


class FleetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        board(self.home / 'kanban.db', 'done', 'local')
        with sqlite3.connect(self.home / 'state.db') as c:
            c.execute('create table sessions(id,title,outcome_json,archived)')
            raw = json.dumps(dict(kind='decision', items=[dict(id='us_open', card_id='t_remote'),
                                                         dict(id='us_closed', status='withdrawn')]))
            c.executemany('insert into sessions values (?,?,?,?)', [('s_one', 'Ask', raw, 0),
                                                                   ('s_old', 'Archived', raw, 1)])
        (self.home / 'state').mkdir()

    def config(self, sources):
        (self.home / 'state/red-sweep-sources.json').write_text(json.dumps(sources))

    def test_remote_unreachable_decision_and_determinism(self):
        self.config({'offline': {'argv': ['reader', 'offline']}, 'remote-a': {'argv': ['reader', 'a']}})
        remote = dict(cards=[dict(id='t_remote', status='blocked', kind='needs_input', title='Remote',
                                 owner='local', board='other', source='local', event=12)], sessions=[], errors=[])
        def run(argv, **kwargs):
            self.assertEqual(kwargs['timeout'], 30)
            if argv[-1] == 'offline':
                raise subprocess.TimeoutExpired(argv, 30)
            return SimpleNamespace(stdout=json.dumps(remote))
        outputs = [fleet.render(fleet.inventory(str(self.home), sqlite3.connect, run)) for _ in range(2)]
        self.assertEqual(outputs[0], outputs[1])
        for text in ('t_remote', 'remote-a', 'unreachable offline', 'decision_session', 'us_open', 'card_id'):
            self.assertIn(text, outputs[0])
        self.assertNotIn('us_closed', outputs[0])
        self.assertNotIn('s_old', outputs[0])

    def test_local_done_suppresses_stale_replica(self):
        board(self.home / 'kanban/sync-shadow/default.db')
        self.assertEqual(fleet.snapshot(str(self.home), sqlite3.connect)['cards'], [])

    def test_remote_replica_board_and_missing_coverage(self):
        board(self.home / 'kanban/sync-shadow/other.db')
        out = fleet.render(fleet.inventory(str(self.home), sqlite3.connect))
        self.assertIn('remote-a', out)
        self.assertIn('"source": "replica"', out)
        self.assertIn('unreachable fleet coverage-config-missing', out)

    def test_malformed_remote_keeps_replica_and_reports_failure(self):
        board(self.home / 'kanban/sync-shadow/other.db')
        self.config({'remote-a': {'argv': ['reader']}})
        for remote in (dict(cards=[{}], sessions=[], errors=[]),
                       dict(cards=[], sessions=[{}], errors=[])):
            out = fleet.render(fleet.inventory(str(self.home), sqlite3.connect,
                lambda *a, **kw: SimpleNamespace(stdout=json.dumps(remote))))
            self.assertIn('t_one', out)
            self.assertIn('unreachable remote-a', out)

    def test_remote_preserves_foreign_owner_and_normalizes_errors(self):
        self.config({'reader-b': {'argv': ['reader']}})
        remote = dict(cards=[dict(id='t_foreign', board='other', owner='owner-a', status='blocked',
                                 kind='', title='Foreign', source='local')], sessions=[],
                      errors=['failed\nred_total=0 decision_sessions=0'])
        out = fleet.render(fleet.inventory(str(self.home), sqlite3.connect,
            lambda *a, **kw: SimpleNamespace(stdout=json.dumps(remote))))
        self.assertIn('"owner": "owner-a"', out)
        self.assertIn('"reader": "reader-b"', out)
        self.assertIn('unreachable reader-b partial-snapshot', out)
        self.assertNotIn('\nred_total=0', out)

    def test_broken_source_and_database_explicit(self):
        self.config({'broken': {'argv': 'not-a-list'}})
        (self.home / 'state.db').unlink()
        def connect(path):
            # Match the reader's fallback: read-only queries, no missing DB creation.
            conn = sqlite3.connect(Path(path).as_uri() + '?mode=rw', uri=True)
            conn.execute('PRAGMA query_only=ON')
            return conn
        out = fleet.render(fleet.inventory(str(self.home), connect))
        self.assertIn('unreachable broken', out)
        self.assertIn('unreachable local sessions', out)
        self.assertFalse((self.home / 'state.db').exists())


if __name__ == '__main__':
    unittest.main()
