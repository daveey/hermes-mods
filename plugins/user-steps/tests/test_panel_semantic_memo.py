"""The panel skips the projection using real semantic SQLite dependencies."""
from contextlib import closing
import json
import sqlite3
import threading
from types import SimpleNamespace

import pytest

from test_kanban_mirror import OWNER, fixture, decide
from test_panel_membership import sources  # noqa: F401 -- shared disposable stores


@pytest.fixture
def panel(tmp_path, monkeypatch, sources):
    root = tmp_path / 'mirror'
    root.mkdir()
    mirror, store, path, _ = fixture(root)
    monkeypatch.setattr(mirror.remote, 'reconcile', lambda owner: None)
    mirror.reconcile(OWNER)  # settle the local binding before capturing inputs
    clock = [100.0]
    glob = mirror.reconcile.__func__.__globals__
    monkeypatch.setitem(glob, 'time', SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr(mirror.remote, 'membership_capture', lambda owner: sources.capture())
    calls = []
    monkeypatch.setattr(mirror.remote, 'reconcile', lambda owner: calls.append(owner))
    # A semantic hit must not consult the rejected global physical stamp either.
    def legacy(*args):
        raise AssertionError('global physical stamp used')
    monkeypatch.setattr(mirror.remote, 'input_stamp', legacy)
    return SimpleNamespace(mirror=mirror, store=store, path=path, clock=clock,
                           calls=calls, sources=sources, run=lambda: mirror.reconcile_panel(OWNER))


def test_unchanged_chat_skips_projection_despite_other_chat_writes(panel):
    p = panel
    p.run()
    assert len(p.calls) == 1
    p.sources.put(p.sources.task(session='another-chat'))
    with p.store._tx() as c:
        c.execute('CREATE TABLE unrelated(value INTEGER)')
        c.execute('INSERT INTO unrelated VALUES (1)')
        c.execute('INSERT INTO requests VALUES (?,?,?,?,?,NULL)',
                  (p.store._ns(OWNER), 'unbound-other', 'another-chat', 1, 'open'))
        c.execute('UPDATE lifecycle SET notified_at=1234')
    p.run()
    assert len(p.calls) == 1
    assert p.mirror.panel_remote_unchanged
    assert len(p.store.requests(OWNER)) == 1  # local handling remains active


@pytest.mark.parametrize('change', ['arrival', 'removal', 'alias', 'ledger'])
def test_membership_change_invalidates_before_ceiling(panel, change):
    p = panel
    if change == 'removal':
        p.sources.put(p.sources.task())
    p.run()
    if change == 'arrival':
        p.sources.put(p.sources.task())
    elif change == 'removal':
        p.sources.sql('replica', "DELETE FROM replica WHERE coll='tasks'")
    elif change == 'alias':
        p.sources.sql('state', "UPDATE sessions SET end_reason='compression'")
        p.sources.sql('state', 'INSERT INTO sessions VALUES (?,?,NULL)', ('tip', OWNER['storedSessionId']))
    else:
        # Complete current owner publication for a ledger-only obligation.
        p.sources.obligation()
        p.sources.put(dict(id='t_ledger', status='blocked', sessions=[]))
    p.run()
    assert len(p.calls) == 2
    assert not p.mirror.panel_remote_unchanged


@pytest.mark.parametrize('table,statement', [
    ('requests', "UPDATE requests SET state='needs_context'"),
    ('revisions', "UPDATE revisions SET body=body || ' '"),
    ('kanban_projections', "UPDATE kanban_projections SET request=request || ' '"),
    ('lifecycle', "UPDATE lifecycle SET withdrawal='{}'"),
    ('withdrawal_history', "INSERT INTO withdrawal_history SELECT namespace,id,revision,'{}' FROM requests"),
])
def test_actionstore_dependency_invalidates_remote_memo(panel, monkeypatch, table, statement):
    p = panel
    # Isolate the remote invalidation assertion from intentionally malformed local
    # fixture rows; production _panel_stamp and all source reads remain real.
    monkeypatch.setattr(p.mirror, '_reconcile_local', lambda *args: None)
    p.run()
    p.run()
    assert len(p.calls) == 1
    with p.store._tx() as c:
        c.execute(statement)
    p.run()
    assert len(p.calls) == 2, table


def test_operation_change_invalidates_remote_memo(panel):
    p = panel
    op = decide(p.store, p.store.requests(OWNER)[0])
    p.run()
    p.run()
    assert len(p.calls) == 1
    with p.store._tx() as c:
        row = c.execute('SELECT body FROM operations WHERE id=?', (op['operationId'],)).fetchone()
        body = json.loads(row[0])
        body['dispatchState'] = 'failed'
        c.execute('UPDATE operations SET body=? WHERE id=?', (json.dumps(body), op['operationId']))
    p.run()
    assert len(p.calls) == 2


def test_historical_namespace_binding_remains_a_dependency(panel, monkeypatch):
    p = panel
    monkeypatch.setattr(p.mirror, '_reconcile_local', lambda *args: None)
    with p.store._tx() as c:
        c.execute("UPDATE kanban_projections SET session='old-chat'")
        c.execute("UPDATE requests SET session='old-chat'")
    p.run()
    p.run()
    assert len(p.calls) == 1
    with p.store._tx() as c:
        c.execute("UPDATE requests SET state='withdrawn'")
    p.run()
    assert len(p.calls) == 2


def test_canonical_ceiling_is_start_based_and_non_sliding(panel, monkeypatch):
    p = panel
    def slow(owner):
        p.calls.append(owner)
        p.clock[0] += 20
    monkeypatch.setattr(p.mirror.remote, 'reconcile', slow)
    p.run()
    for when in (121, 140, 159):
        p.clock[0] = when
        p.run()
    assert len(p.calls) == 1
    p.clock[0] = 160
    p.run()
    assert len(p.calls) == 2


def test_unknown_membership_never_reuses_success_or_unknown(panel):
    p = panel
    p.run()
    p.sources.paths['ledger'].unlink()
    p.run()
    p.run()
    assert len(p.calls) == 3
    assert not p.mirror.panel_remote_unchanged


def test_capture_is_before_projection_and_concurrent_write_is_owed(panel, monkeypatch):
    p = panel
    def writes(owner):
        p.calls.append(owner)
        p.sources.put(p.sources.task(title=str(len(p.calls))))
    monkeypatch.setattr(p.mirror.remote, 'reconcile', writes)
    p.run()
    p.run()
    assert len(p.calls) == 2


def test_semantic_deferred_backoff_does_not_certify_empty_and_local_still_runs(panel, monkeypatch):
    p = panel
    def deferred(owner):
        p.calls.append(owner)
        return False
    monkeypatch.setattr(p.mirror.remote, 'reconcile', deferred)
    with pytest.raises(RuntimeError, match='deferred'):
        p.run()
    p.sources.put(p.sources.task())
    p.clock[0] = 101
    with closing(sqlite3.connect(p.path)) as c, c:
        c.execute("UPDATE tasks SET status='completed'")
    with pytest.raises(RuntimeError, match='deferred'):
        p.run()
    assert len(p.calls) == 1
    assert p.store.requests(OWNER) == []
    assert not p.mirror.panel_remote_unchanged
    p.clock[0] = 105
    with pytest.raises(RuntimeError, match='deferred'):
        p.run()
    assert len(p.calls) == 2


def test_single_flight_with_semantic_source_change(panel, monkeypatch):
    p = panel
    entered, release = threading.Event(), threading.Event()
    errors = []
    def blocking(owner):
        p.calls.append(owner)
        entered.set()
        assert release.wait(5)
    monkeypatch.setattr(p.mirror.remote, 'reconcile', blocking)
    def worker():
        try:
            p.run()
        except BaseException as exc:
            errors.append(exc)
    t = threading.Thread(target=worker)
    t.start()
    try:
        assert entered.wait(5)
        p.sources.put(p.sources.task())
        with pytest.raises(RuntimeError, match='in progress'):
            p.run()
        assert len(p.calls) == 1
    finally:
        release.set()
        t.join(5)
    assert not t.is_alive()
    assert not errors
    # In-flight source change was not swallowed by completion.
    p.run()
    assert len(p.calls) == 2
