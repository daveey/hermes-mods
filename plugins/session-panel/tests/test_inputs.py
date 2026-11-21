import importlib.util
import json
import sqlite3
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('panel_inputs_test', ROOT / 'inputs.py')
inputs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(inputs)


class Reader:
    def __init__(self, home):
        self.home = home
        self.connections = []

    def state_db(self):
        return self.home / 'state.db'

    def conn(self, path):
        if not path.exists():
            return None
        c = sqlite3.connect(f'file:{path}?mode=rw', uri=True)
        c.row_factory = sqlite3.Row
        c.execute('PRAGMA query_only=ON')
        self.connections.append(c)
        return c

    def close(self):
        for c in self.connections:
            c.close()


def test_reads_current_lifecycle_without_writes_and_reuses_runtime_producers(tmp_path, monkeypatch):
    h = tmp_path
    with sqlite3.connect(h / 'state.db') as c:
        c.execute('CREATE TABLE sessions(id TEXT,title TEXT,outcome_json TEXT)')
        c.execute('INSERT INTO sessions VALUES (?,?,?)', ('chat', 'Chat', '{"kind":"waiting"}'))
        c.execute('INSERT INTO sessions VALUES (?,?,?)', ('child', 'Research child', '{}'))
    (h / 'user-steps').mkdir()
    with sqlite3.connect(h / 'user-steps' / 'actions.sqlite3') as c:
        c.execute('CREATE TABLE requests(namespace TEXT,id TEXT,session TEXT,revision INT,state TEXT,resolution TEXT)')
        c.execute('CREATE TABLE revisions(namespace TEXT,id TEXT,revision INT,body TEXT)')
        for rid, state in [('open', 'open'), ('old', 'withdrawn')]:
            body = {'requestId': rid, 'state': 'open', 'owner': {'profile': 'default', 'storedSessionId': 'chat'}}
            c.execute('INSERT INTO requests VALUES (?,?,?,?,?,?)', ('ns', rid, 'chat', 1, state, None))
            c.execute('INSERT INTO revisions VALUES (?,?,?,?)', ('ns', rid, 1, json.dumps(body)))
    def sub(rows):
        rows[0]['execution_observations'] = [{'identity': {'kind': 'subagent', 'work_id': 'child'}}]
    def proc(rows):
        rows[0]['execution_observations'].append({'identity': {'kind': 'process', 'work_id': 'proc'}})
        rows[0]['background_processes'] = [{'id': 'proc', 'command': 'run tests'}]
    monkeypatch.setitem(sys.modules, 'hermes_cli.subagent_session_activity', types.SimpleNamespace(annotate_subagent_activity=sub))
    monkeypatch.setitem(sys.modules, 'hermes_cli.process_session_activity', types.SimpleNamespace(annotate_background_activity=proc))
    class Service:
        def start_refresh(self):
            pass
        def inventory(self, **kw):
            return {'gateway_id': 'g', 'machines': [{'key': 'box', 'display_name': 'Test box', 'ownership': [{'gateway_id': 'g', 'profile': 'default', 'session_id': 'chat'}]}, {'key': 'other', 'ownership': []}]}
    monkeypatch.setitem(sys.modules, '_session_panel_rentals_api', types.SimpleNamespace(_plugin=lambda: types.SimpleNamespace(get_service=Service)))
    r = Reader(h)
    try:
        result = inputs.read(r, 'chat', ['chat'])
        assert result['input_errors'] == []
        assert result['outcome_json'] == {'kind': 'waiting'}
        assert [x['state'] for x in result['requests']] == ['open', 'withdrawn']
        assert result['subagents'][0]['title'] == 'Research child'
        assert result['processes'][0]['title'] == 'run tests'
        assert [m['id'] for m in result['rentals']] == ['box']
        assert all(c.total_changes == 0 for c in r.connections)
    finally:
        r.close()


def test_unenrolled_gateway_does_not_abort_local_inputs(tmp_path, monkeypatch):
    class SyncUnavailable(RuntimeError):
        pass

    def missing_identity():
        raise SyncUnavailable('kanban sync: no hub key')

    reader = Reader(tmp_path)
    reader.ksc = types.SimpleNamespace(load_identity=missing_identity)
    try:
        result = inputs.read(reader, 'chat', ['chat'])
        assert result['gateway'] == ''
        assert result['requests'] == []
        assert result['rentals'] == []
    finally:
        reader.close()
