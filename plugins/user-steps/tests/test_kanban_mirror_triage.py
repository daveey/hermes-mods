"""Block-loop triage asks retain exact-card delivery and generation fences."""
import json
import sqlite3

import pytest

from test_kanban_mirror import OWNER, decide, fixture
from test_withdrawal import ROOT, load_module


def test_panel_read_produces_canonical_request_without_legacy_poll(tmp_path, monkeypatch):
    mirror, store, path, _ = fixture(tmp_path)
    api = load_module('triage_panel_api', ROOT / 'dashboard' / 'plugin_api.py')
    with sqlite3.connect(tmp_path / 'state.db') as c:
        c.execute('CREATE TABLE sessions(id TEXT PRIMARY KEY,parent_session_id TEXT,end_reason TEXT,source TEXT)')
        c.execute("INSERT INTO sessions(id,source) VALUES (?,'desktop')", (OWNER['storedSessionId'],))
    core = mirror.core
    monkeypatch.setattr(core, 'home', lambda: tmp_path)
    monkeypatch.setattr(core, 'server_owner', lambda sid: dict(OWNER, storedSessionId=sid))
    def load(name):
        from types import SimpleNamespace
        return core if name == 'core' else SimpleNamespace(Mirror=lambda *_: mirror)
    monkeypatch.setattr(api, '_load', load)
    # Only the store constructor is substituted; projection, immutable binding,
    # presentation and decision delivery all use the real producer/store.
    monkeypatch.setattr(core, 'ActionStore', lambda: store)
    requests = api.panel_requests(tmp_path, OWNER['storedSessionId'], [OWNER['storedSessionId']])
    assert len(requests) == 1
    assert requests[0]['presentation']['kind'] == 'kanban-question'
    operation = decide(store, requests[0])
    with monkeypatch.context() as patch:
        def no_reconcile(*args):
            raise AssertionError('fast canonical reads must not hydrate boards')
        patch.setattr(mirror, 'reconcile', no_reconcile)
        pending = api.panel_requests(tmp_path, OWNER['storedSessionId'], [OWNER['storedSessionId']], reconcile=False)
        assert len(pending) == 1
        assert pending[0]['resolution']['operationId'] == operation['operationId']
        assert pending[0]['state'] in ('open', 'needs_context')
    receipt = mirror.deliver(operation)
    assert receipt['task'] == 't_test'
    with pytest.raises(ValueError, match='profile'):
        api.panel_requests(tmp_path / 'another-profile', OWNER['storedSessionId'], [OWNER['storedSessionId']])


def test_triage_ask_is_canonical_and_delivers_exact_value_once(tmp_path):
    mirror, store, path, _ = fixture(tmp_path)
    with sqlite3.connect(path) as c:
        c.execute("UPDATE tasks SET status='triage'")
        c.execute("INSERT INTO task_events(task_id,kind) VALUES ('t_test','block_loop_detected')")
    mirror.reconcile(OWNER)
    mirror.reconcile(OWNER)
    requests = store.requests(OWNER)
    assert len(requests) == 1
    op = decide(store, requests[0])
    receipt = mirror.deliver(op)
    assert receipt['value'] == 'green'
    assert mirror.readback(op, receipt)
    assert mirror.deliver(op) == receipt
    with sqlite3.connect(path) as c:
        assert c.execute('SELECT status FROM tasks').fetchone()[0] == 'ready'
        assert c.execute('SELECT count(*) FROM task_comments').fetchone()[0] == 1


def test_new_triage_generation_rejects_recorded_old_answer(tmp_path):
    mirror, store, path, _ = fixture(tmp_path)
    with sqlite3.connect(path) as c:
        c.execute("UPDATE tasks SET status='triage'")
    mirror.reconcile(OWNER)
    requests = store.requests(OWNER)
    assert len(requests) == 1
    op = decide(store, requests[0])
    with sqlite3.connect(path) as c:
        c.execute("INSERT INTO task_events(task_id,kind) VALUES ('t_test','block_loop_detected')")
    with pytest.raises(ValueError, match='changed|refused'):
        mirror.deliver(op)
    with sqlite3.connect(path) as c:
        assert c.execute('SELECT status FROM tasks').fetchone()[0] == 'triage'
        assert c.execute('SELECT count(*) FROM task_comments').fetchone()[0] == 0


def test_triage_without_human_ask_is_not_a_decision(tmp_path):
    mirror, store, path, _ = fixture(tmp_path)
    with sqlite3.connect(path) as c:
        c.execute("UPDATE tasks SET status='triage',human_request=NULL")
    mirror.reconcile(OWNER)
    assert store.requests(OWNER) == []
