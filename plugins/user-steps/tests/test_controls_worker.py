"""Bounded event-driven controls work; no polling or refresh on the request thread."""
import concurrent.futures
import contextvars
import sqlite3
import threading
from contextlib import closing

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_decisions import core, ROOT, OWNER, load_module, store, request


def test_lineage_with_incomplete_core_schema_stays_on_current_chat(tmp_path):
    pytest.importorskip('hermes_state_compression')
    db = tmp_path / 'state.db'
    with closing(sqlite3.connect(db)) as conn, conn:
        conn.execute('CREATE TABLE sessions(id TEXT, parent_session_id TEXT, end_reason TEXT, source TEXT, model_config TEXT)')
        conn.executemany('INSERT INTO sessions VALUES (?,?,?,?,?)', [
            ('root', None, 'compression', 'desktop', '{}'), ('tip', 'root', None, 'desktop', '{}')])
    assert core.lineage('tip', db) == ['tip']


@pytest.mark.parametrize('failure', ['connect', 'pragma'])
def test_lineage_contains_third_connection_sqlite_errors(tmp_path, monkeypatch, failure):
    db = tmp_path / 'state.db'
    with closing(sqlite3.connect(db)) as conn, conn:
        conn.execute('CREATE TABLE sessions(id TEXT, parent_session_id TEXT, end_reason TEXT, source TEXT)')
        conn.executemany('INSERT INTO sessions VALUES (?,?,?,?)', [
            ('root', None, 'compression', 'desktop'), ('tip', 'root', None, 'desktop')])
    original = core._ro_connect
    count = 0
    opened = []
    class BrokenPragma:
        def execute(self, *args):
            raise sqlite3.OperationalError('injected lineage PRAGMA failure')
        def close(self):
            opened.append('closed')
    def connect(*args, **kwargs):
        nonlocal count
        count += 1
        if count == 3:
            if failure == 'connect':
                raise sqlite3.OperationalError('injected third connection failure')
            return BrokenPragma()
        return original(*args, **kwargs)
    monkeypatch.setattr(core, '_ro_connect', connect)
    assert core.lineage('tip', db) == ['tip']
    assert count == 3
    assert opened == (['closed'] if failure == 'pragma' else [])


def test_ledger_snapshot_failure_is_before_durable_replace(tmp_path, monkeypatch):
    ledger = core.Ledger(tmp_path / 'ledger', tmp_path / 'state.db')
    original = core._open_controls
    count = 0
    def snapshot(document):
        nonlocal count
        count += 1
        if count == 2:
            raise TypeError('snapshot unavailable')
        return original(document)
    monkeypatch.setattr(core, '_open_controls', snapshot)
    with pytest.raises(TypeError, match='snapshot unavailable'):
        with ledger.edit('root') as document:
            document['card'] = 'changed'
    assert not ledger.path('root').exists()


def test_copied_context_submits_after_batch_exit(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(core.CONTROLS_REFRESHER, 'submit', lambda db, sessions: calls.append(set(sessions)))
    with core.defer_controls_refresh():
        core.queue_controls_refresh(tmp_path / 'state.db', ['before'])
        copied = contextvars.copy_context()
    copied.run(core.queue_controls_refresh, tmp_path / 'state.db', ['after'])
    assert calls == [{'before'}, {'after'}]


def test_dead_retrier_restarted_by_decision_does_not_keep_closed_batch(tmp_path, monkeypatch):
    api = load_module('controls_dead_retrier_api', ROOT / 'dashboard' / 'plugin_api.py')
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    s = store(tmp_path)
    r = s.create(OWNER, request())
    assert core.flush_controls_refresh(5)
    calls = []
    for mod in (core, api._load('core')):
        monkeypatch.setattr(mod.CONTROLS_REFRESHER, 'submit', lambda db, sessions: calls.append(set(sessions)))
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    class Receiver:
        def deliver(self, operation):
            entered.set()
            assert release.wait(5)
            return {'id': operation['operationId']}
        def readback(self, operation, receipt):
            finished.set()
            return True
    router = api.create_router(ledger_dir=tmp_path / 'ledger', state_db=tmp_path / 'state.db',
        action_store=s, owner_resolver=lambda sid: dict(OWNER, storedSessionId=sid), receiver=Receiver())
    app = FastAPI(); app.include_router(router)
    runtime = api._load('outbox')
    with TestClient(app) as client:
        try:
            workers = [w for w in runtime._WORKERS.values() if router._delivery_token in w.owners]
            assert len(workers) == 1
            # Inject only the dead-retrier precondition; use the real route/store/replacement worker.
            assert runtime.release(workers[0], router._delivery_token)
            response = client.post(f'/requests/{r["requestId"]}/decisions', json=dict(
                session=OWNER['storedSessionId'], expectedRevision=1, choiceId='approve', idempotencyKey='dead-retrier'))
            assert response.status_code == 200
            assert entered.wait(2)
            initial_count = len(calls)
            assert initial_count >= 1
            release.set()
            assert finished.wait(2)
            # Join the replacement after its real delivery commit, not just receiver readback.
            router.stop_delivery()
            operation_id = response.json()['operation']['operationId']
            assert s.operation(OWNER, operation_id)['operation']['dispatchState'] == 'delivered'
            assert len(calls) > initial_count, 'delivered change must submit outside the exited request batch'
        finally:
            release.set()
            router.stop_delivery()



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
