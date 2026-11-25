"""Block-loop triage asks retain exact-card delivery and generation fences."""
import json
import sqlite3

import pytest

from test_kanban_mirror import OWNER, decide, fixture
from test_withdrawal import ROOT, load_module


@pytest.mark.parametrize('source_phase', [False, True, 'remote-unavailable'])
def test_panel_read_produces_canonical_request_without_legacy_poll(tmp_path, monkeypatch, source_phase):
    monkeypatch.setenv('HERMES_KANBAN_HOME', str(tmp_path))
    mirror, store, path, _ = fixture(tmp_path)
    api = load_module('triage_panel_api', ROOT / 'dashboard' / 'plugin_api.py')
    with sqlite3.connect(tmp_path / 'state.db') as c:
        c.execute('CREATE TABLE sessions(id TEXT PRIMARY KEY,parent_session_id TEXT,end_reason TEXT,source TEXT)')
        c.execute("INSERT INTO sessions(id,source) VALUES (?,'desktop')", (OWNER['storedSessionId'],))
    core = mirror.core
    monkeypatch.setattr(core, 'home', lambda: tmp_path)
    monkeypatch.setattr(core, 'server_owner', lambda sid: dict(OWNER, storedSessionId=sid))
    original_load = api._load
    def load(name):
        from types import SimpleNamespace
        if name == 'core':
            return core
        if name == 'kanban_mirror':
            runtime = mirror.reconcile_panel.__func__.__globals__
            return SimpleNamespace(Mirror=lambda *_: mirror, panel_trace=runtime['panel_trace'],
                                   _stage=runtime['_stage'])
        return original_load(name)
    monkeypatch.setattr(api, '_load', load)
    # Only the store constructor is substituted; projection, immutable binding,
    # presentation and decision delivery all use the real producer/store.
    store_type = core.ActionStore
    monkeypatch.setattr(core, 'ActionStore', lambda **kwargs: store_type(store.path, **kwargs))
    if source_phase == 'remote-unavailable':
        # A host with core but no hub projection: local cards are still produced, the rest is reported.
        def gone(owner):
            raise OSError('Remote ask owner projection unavailable')
        monkeypatch.setattr(mirror.remote, 'snapshots', gone)
    if source_phase:
        left = api.panel_reconcile(tmp_path, OWNER['storedSessionId'], [OWNER['storedSessionId']])
        if source_phase == 'remote-unavailable':
            assert len(left) == 1 and 'projection unavailable' in left[0]
    requests = api.panel_requests(tmp_path, OWNER['storedSessionId'], [OWNER['storedSessionId']],
                                  reconcile=not source_phase)
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


def panel_fixture(tmp_path, monkeypatch):
    from types import SimpleNamespace
    mirror, store, path, _ = fixture(tmp_path)
    clock, stamp, calls = [100.0], ['rev-a'], []
    # Isolate this producer's clock, not the interpreter-wide time module.
    glob = mirror.reconcile.__func__.__globals__
    monkeypatch.setitem(glob, 'time', SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr(mirror, '_panel_stamp', lambda _: stamp[0])
    monkeypatch.setattr(mirror.remote, 'reconcile', lambda owner: calls.append(owner))
    def run():
        fn = getattr(mirror, 'reconcile_panel', None)
        return fn(OWNER) if fn else mirror.reconcile(OWNER, strict=True)
    return mirror, store, path, clock, stamp, calls, run


def test_panel_unchanged_remote_stamp_skips_and_ceiling_does_not_slide(tmp_path, monkeypatch):
    mirror, store, path, clock, stamp, calls, run = panel_fixture(tmp_path, monkeypatch)
    run()
    for when in (101, 110, 159):
        clock[0] = when
        run()
    assert len(calls) == 1
    assert len(store.requests(OWNER)) == 1  # local projection still ran
    clock[0] = 160
    run()
    assert len(calls) == 2
    stamp[0] = 'rev-b'
    run()
    assert len(calls) == 3


@pytest.mark.parametrize('throws', [False, True])
def test_panel_remote_deferred_backs_off_despite_source_churn_and_keeps_local(tmp_path, monkeypatch, throws):
    mirror, store, path, clock, stamp, calls, run = panel_fixture(tmp_path, monkeypatch)
    def unavailable(owner):
        calls.append(owner)
        if throws:
            raise OSError('projection unavailable')
        return False
    monkeypatch.setattr(mirror.remote, 'reconcile', unavailable)
    with pytest.raises(RuntimeError):
        run()
    assert len(store.requests(OWNER)) == 1
    # An unrelated source move must not turn every feed pass into a remote retry.
    for when in (101, 104):
        clock[0], stamp[0] = when, str(when)
        with pytest.raises(RuntimeError):
            run()
    assert len(calls) == 1
    # Local withdrawal is not delayed by an unavailable remote board.
    with sqlite3.connect(path) as c:
        c.execute("UPDATE tasks SET status='completed'")
    with pytest.raises(RuntimeError):
        run()
    assert store.requests(OWNER) == []  # withdrawn rows are excluded by default
    clock[0] = 105
    with pytest.raises(RuntimeError):
        run()
    assert len(calls) == 2
    clock[0] = 114
    with pytest.raises(RuntimeError):
        run()
    assert len(calls) == 2
    clock[0] = 115
    monkeypatch.setattr(mirror.remote, 'reconcile', lambda owner: calls.append(owner))
    run()
    assert len(calls) == 3
    run()
    assert len(calls) == 3


def test_panel_unknown_stamp_never_vouches_success(tmp_path, monkeypatch):
    mirror, store, path, clock, stamp, calls, run = panel_fixture(tmp_path, monkeypatch)
    stamp[0] = None
    run()
    run()
    assert len(calls) == 2


def test_panel_source_change_during_pass_is_not_consumed(tmp_path, monkeypatch):
    mirror, store, path, clock, stamp, calls, run = panel_fixture(tmp_path, monkeypatch)
    def remote(owner):
        calls.append(owner)
        stamp[0] = 'rev-b'
    monkeypatch.setattr(mirror.remote, 'reconcile', remote)
    run()
    run()
    assert len(calls) == 2


def test_panel_ceiling_uses_start_not_completion_and_skips_do_not_clear_freshness(tmp_path, monkeypatch):
    mirror, store, path, clock, stamp, calls, run = panel_fixture(tmp_path, monkeypatch)
    def slow(owner):
        calls.append(owner)
        clock[0] += 20
    monkeypatch.setattr(mirror.remote, 'reconcile', slow)
    run()
    clock[0] = 159
    run()
    assert len(calls) == 1
    assert mirror.panel_remote_unchanged is True
    clock[0] = 160
    run()
    assert len(calls) == 2
    def deferred(owner):
        calls.append(owner)
        return False
    monkeypatch.setattr(mirror.remote, 'reconcile', deferred)
    stamp[0] = 'failed'
    with pytest.raises(RuntimeError):
        run()
    glob = mirror.reconcile.__func__.__globals__
    cleared = dict(glob['_REMOTE_CLEARED'])
    clock[0] += 1
    with pytest.raises(RuntimeError):
        run()
    assert glob['_REMOTE_CLEARED'] == cleared
