"""Bounded event-driven controls work; no polling or refresh on the request thread."""
import concurrent.futures
import sqlite3
import threading

from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_decisions import core, ROOT, OWNER, load_module, store, request


def test_burst_deduplicates_but_write_during_refresh_runs_again(tmp_path):
    entered, release = threading.Event(), threading.Event()
    calls = []
    def refresh(db, sessions):
        calls.append(list(sessions))
        if len(calls) == 1:
            entered.set()
            assert release.wait(5)
    worker = core.ControlsRefresher(cap=2, refresh=refresh)
    try:
        worker.submit(tmp_path / 'state.db', ['chat'])
        assert entered.wait(2)
        for _ in range(100): worker.submit(tmp_path / 'state.db', ['chat'])
        assert len(worker._pending) == 1
        assert worker.stats['deduplicated'] == 99
    finally:
        release.set()
        assert worker.flush(5)
    assert calls == [['chat'], ['chat']]


def test_overflow_coarsens_without_caller_refresh(tmp_path):
    db = tmp_path / 'state.db'
    from contextlib import closing
    with closing(sqlite3.connect(db)) as conn, conn:
        conn.execute('CREATE TABLE sessions(id TEXT)')
        conn.executemany('INSERT INTO sessions VALUES (?)', [('a',), ('b',), ('c',)])
    entered, release = threading.Event(), threading.Event()
    calls = []
    caller = threading.get_ident()
    def refresh(path, sessions):
        assert threading.get_ident() != caller
        calls.append(set(sessions))
        if len(calls) == 1:
            entered.set()
            assert release.wait(5)
    worker = core.ControlsRefresher(cap=2, refresh=refresh)
    try:
        worker.submit(db, ['a'])
        assert entered.wait(2)
        worker.submit(db, ['a', 'b', 'c'])
        assert len(worker._pending) <= 2
        assert worker.stats['overflow'] == 1
    finally:
        release.set()
        assert worker.flush(5)
    assert calls == [{'a'}, {'a', 'b', 'c'}]


def test_tx_never_reads_touch_list_after_commit(tmp_path, monkeypatch):
    s = store(tmp_path)
    connect = s._connect
    events = []
    class Connection:
        def __init__(self): self.inner = connect()
        def __getattr__(self, key): return getattr(self.inner, key)
        def execute(self, sql, *args):
            if sql.startswith('SELECT DISTINCT session FROM _controls_touched'):
                events.append('touch')
                if 'commit' in events: raise sqlite3.OperationalError('postcommit reader unavailable')
            return self.inner.execute(sql, *args)
        def commit(self):
            events.append('commit')
            self.inner.commit()
    monkeypatch.setattr(s, '_connect', Connection)
    assert s.create(OWNER, request())['requestId']
    assert events == ['touch', 'commit']


def test_decision_route_returns_while_controls_refresh_is_held(tmp_path, monkeypatch):
    api = load_module('controls_latency_api', ROOT / 'dashboard' / 'plugin_api.py')
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    s = store(tmp_path)
    r = s.create(OWNER, request())
    if hasattr(core, 'flush_controls_refresh'):
        assert core.flush_controls_refresh(5)
    entered, release, hinted = threading.Event(), threading.Event(), threading.Event()
    class Receiver:
        def deliver(self, operation):
            hinted.set()
            return {'id': operation['operationId']}
        def readback(self, operation, receipt): return True
    def refresh(db, sessions):
        entered.set()
        assert release.wait(5)
    worker = core.ControlsRefresher(refresh=refresh) if hasattr(core, 'ControlsRefresher') else None
    for mod in (core, api._load('core')):
        if worker:
            monkeypatch.setattr(mod, 'CONTROLS_REFRESHER', worker)
        else:  # original implementation's synchronous refresh (behavioral RED control)
            monkeypatch.setattr(mod, 'refresh_controls', refresh)
    router = api.create_router(ledger_dir=tmp_path / 'ledger', state_db=tmp_path / 'state.db',
        action_store=s, owner_resolver=lambda sid: dict(OWNER, storedSessionId=sid), receiver=Receiver())
    app = FastAPI(); app.include_router(router)
    with TestClient(app) as client, concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(client.post, f'/requests/{r["requestId"]}/decisions', json=dict(
            session=OWNER['storedSessionId'], expectedRevision=1, choiceId='approve', idempotencyKey='controls-held'))
        try:
            assert entered.wait(2)
            assert future.result(timeout=1).status_code == 200
            assert hinted.wait(2)
        finally:
            release.set()
            future.result(timeout=5)
            router.stop_delivery()
            if worker:
                assert worker.flush(5)
