"""Change-keyed reconcile (design t_01a1d563324d7e8e card L): an unchanged input key skips the pass; every input
change (board, store, sessions), a delete, a replaced file and the FULL_EVERY_S ceiling run it in full."""

import shutil
import sqlite3
import sys
import types

import pytest

from test_kanban_mirror import fixture, decide, OWNER, core


@pytest.fixture
def keyed(tmp_path, monkeypatch):
    """The fixture's mirror with the production resolvers (only those are keyed) over a real sessions table."""
    # Core's origin-work reader is independent of the mirror's injected board.
    # Keep it off the account's real snapshot, including during CPU replays.
    monkeypatch.setenv('HERMES_KANBAN_HOME', str(tmp_path))
    state = tmp_path / "state.db"
    with sqlite3.connect(state) as c:
        c.execute("CREATE TABLE sessions(id TEXT PRIMARY KEY, parent_session_id TEXT, end_reason TEXT, "
                  "source TEXT, started_at REAL)")
        c.executemany("INSERT INTO sessions VALUES (?, NULL, NULL, 'desktop', 0)", [("session-a",), ("session-b",)])
    monkeypatch.setattr(core, "_state_db", lambda: state)
    chain = types.SimpleNamespace(_CHAIN_STEP_SQL="SELECT id FROM sessions WHERE id=?",
                                  _walk_compression_chain=lambda sid, step: [sid])
    monkeypatch.setitem(sys.modules, "hermes_state_compression", chain)
    mirror, store, path, hr = fixture(tmp_path)
    mirror.session_exists = mirror._user_session
    mirror.session_present = mirror._stored_session
    mirror.tip = mirror._tip
    mod = type(mirror).reconcile.__globals__  # the module loaded under a test name, not in sys.modules
    passes = []
    full = mirror._reconcile
    monkeypatch.setattr(mirror, "_reconcile", lambda owner, fenced: passes.append(1) or full(owner, fenced))
    return types.SimpleNamespace(mirror=mirror, store=store, path=path, state=state, mod=mod, passes=passes)


def settle(k):
    """Reconcile until a pass is skipped (a pass that wrote the store runs once more); return the full passes."""
    for _ in range(4):
        before = len(k.passes)
        k.mirror.reconcile(OWNER)
        if len(k.passes) == before:
            return before
    raise AssertionError("never settled")


def runs(k):
    before = len(k.passes)
    k.mirror.reconcile(OWNER)
    return len(k.passes) > before


def board(k, sql, *args):
    with sqlite3.connect(k.path) as c:
        c.execute(sql, args)


def test_unchanged_inputs_skip_and_unrelated_board_churn_still_skips(keyed):
    k = keyed
    settle(k)
    assert len(k.store.requests(OWNER)) == 1
    assert not runs(k)
    board(k, "INSERT INTO tasks VALUES ('t_other','x','running','session-a',NULL,NULL)")
    board(k, "INSERT INTO task_events(task_id,kind) VALUES ('t_other','heartbeat')")
    assert not runs(k)  # the board committed, but no row a pass reads moved


def test_board_change_is_picked_up(keyed):
    k = keyed
    settle(k)
    board(k, "UPDATE tasks SET status='ready'")
    assert runs(k)
    assert k.store.requests(OWNER) == []


def test_new_lifecycle_event_is_picked_up(keyed):
    k = keyed
    settle(k)
    first = k.store.requests(OWNER)[0]["requestId"]
    board(k, "INSERT INTO task_events(id,task_id,kind) VALUES (2,'t_test','unblocked')")
    board(k, "INSERT INTO task_events(id,task_id,kind) VALUES (3,'t_test','blocked')")
    assert runs(k)
    assert [r["requestId"] for r in k.store.requests(OWNER)] != [first]


def test_store_change_is_picked_up(keyed):
    k = keyed
    settle(k)
    decide(k.store, k.store.requests(OWNER)[0])
    assert runs(k)


def test_custom_subscriber_descendant_insert_and_delete(keyed, monkeypatch):
    k = keyed

    def walk(sid, step):
        chain = [sid]
        while (row := step(chain[-1])) and row[0] not in chain:
            chain.append(row[0])
        return chain

    # Exercise parent/end_reason traversal, not the identity-only fixture stub.
    monkeypatch.setitem(sys.modules, "hermes_state_compression", types.SimpleNamespace(
        _walk_compression_chain=walk,
        _CHAIN_STEP_SQL="SELECT child.id FROM sessions parent JOIN sessions child "
                        "ON child.parent_session_id=parent.id WHERE parent.id=? "
                        "AND parent.end_reason='compression' AND child.source!='tool' "
                        "ORDER BY child.started_at DESC LIMIT 1"))
    board(k, "UPDATE tasks SET session_id='missing-worker'")
    board(k, "INSERT INTO kanban_notify_subs VALUES ('t_test','desktop','session-r','default',0)")
    with sqlite3.connect(k.state) as c:
        c.execute("INSERT INTO sessions VALUES ('session-r',NULL,'compression','custom',0)")
        c.execute("UPDATE sessions SET parent_session_id='session-r',source='custom' WHERE id='session-a'")
    settle(k)
    assert k.mirror.tip('session-r') == 'session-a'
    assert len(k.store.requests(OWNER)) == 1
    with sqlite3.connect(k.state) as c:
        c.execute("UPDATE sessions SET end_reason='compression' WHERE id='session-a'")
    settle(k)  # descendant updates still have the bounded full-pass ceiling
    with sqlite3.connect(k.state) as c:
        c.execute("INSERT INTO sessions VALUES ('session-a2','session-a',NULL,'custom',1)")
    assert k.mirror.tip('session-r') == 'session-a2'
    assert runs(k)
    assert k.store.requests(OWNER) == []
    settle(k)
    with sqlite3.connect(k.state) as c:
        c.execute("DELETE FROM sessions WHERE id='session-a2'")
    assert k.mirror.tip('session-r') == 'session-a'
    assert runs(k)
    assert len(k.store.requests(OWNER)) == 1


def test_creator_source_does_not_key_worker_churn(keyed):
    k = keyed
    board(k, "UPDATE tasks SET session_id='worker-creator'")
    board(k, "INSERT INTO kanban_notify_subs VALUES ('t_test','desktop','session-a','default',0)")
    with sqlite3.connect(k.state) as c:
        c.execute("INSERT INTO sessions VALUES ('worker-creator',NULL,NULL,'kanban',0)")
    settle(k)
    assert len(k.store.requests(OWNER)) == 1
    with sqlite3.connect(k.state) as c:
        c.execute("INSERT INTO sessions VALUES ('worker-other',NULL,NULL,'kanban',1)")
    assert not runs(k)


def test_new_session_is_picked_up(keyed):
    k = keyed
    settle(k)
    with sqlite3.connect(k.state) as c:
        c.execute("INSERT INTO sessions VALUES ('session-c', NULL, NULL, 'desktop', 0)")
    assert runs(k)


def test_deleted_task_falls_back_to_a_full_pass(keyed):
    k = keyed
    settle(k)
    board(k, "DELETE FROM tasks")
    assert runs(k)
    assert k.store.requests(OWNER) == []


def test_deleted_session_falls_back_to_a_full_pass(keyed):
    k = keyed
    settle(k)
    with sqlite3.connect(k.state) as c:
        c.execute("DELETE FROM sessions WHERE id='session-a'")
    assert runs(k)
    assert k.store.requests(OWNER) == []  # its owner's session is gone: the ask is withdrawn


def test_board_rewound_to_an_older_file_falls_back(keyed, tmp_path):
    k = keyed
    shutil.copy(k.path, tmp_path / "older.db")  # the ask as it is now
    settle(k)
    board(k, "UPDATE tasks SET status='ready'")
    settle(k)
    assert k.store.requests(OWNER) == []
    (tmp_path / "older.db").replace(k.path)  # restore a backup: a new inode, the ask is blocked again
    assert runs(k)
    assert len(k.store.requests(OWNER)) == 1


def test_fallback_ceiling_runs_a_full_pass(keyed, monkeypatch):
    k = keyed
    settle(k)
    assert not runs(k)
    monkeypatch.setitem(k.mod, "FULL_EVERY_S", 0.0)
    assert runs(k) and runs(k)


def test_a_deferred_pass_is_not_memoized(keyed, monkeypatch):
    k = keyed
    settle(k)
    real = k.mirror._reconcile_binding
    monkeypatch.setattr(k.mirror, "_reconcile_binding", lambda *a: (_ for _ in ()).throw(OSError("busy")))
    monkeypatch.setitem(k.mod, "FULL_EVERY_S", 0.0)  # force one full pass on an unchanged key
    assert runs(k)
    monkeypatch.setitem(k.mod, "FULL_EVERY_S", 60.0)
    monkeypatch.setattr(k.mirror, "_reconcile_binding", real)
    assert runs(k)  # the deferred binding is retried although no input moved since
    assert not runs(k)


def test_rejected_publication_is_retried_with_unchanged_inputs(keyed, monkeypatch):
    k = keyed
    real = k.mirror._publish

    def reject(*args):
        raise core.ActionError("publication conflict", 409)

    before = k.mirror._key(OWNER, True)
    monkeypatch.setattr(k.mirror, "_publish", reject)
    assert runs(k)
    assert k.store.requests(OWNER) == []
    assert k.mirror._key(OWNER, True) == before

    monkeypatch.setattr(k.mirror, "_publish", real)
    assert runs(k)  # removing the failure changes no keyed input
    assert len(k.store.requests(OWNER)) == 1
    settle(k)
    assert not runs(k)


def test_receipt_on_a_bound_card_that_left_blocked_moves_the_key(keyed):
    k = keyed
    settle(k)
    board(k, "UPDATE tasks SET status='ready'")
    settle(k)
    before = k.mirror._key(OWNER, True)
    board(k, "INSERT INTO task_events(task_id,kind,payload) VALUES ('t_test','archived','{}')")  # _receipt reads it
    assert k.mirror._key(OWNER, True) != before


def test_injected_resolver_is_never_skipped(tmp_path):
    mirror, store, path, hr = fixture(tmp_path)  # session_exists and tip injected: no key covers them
    mod = type(mirror).reconcile.__globals__  # the module loaded under a test name, not in sys.modules
    assert mirror._key(OWNER, True) is None
    calls = []
    full = mirror._reconcile
    mirror._reconcile = lambda owner, fenced: calls.append(1) or full(owner, fenced)
    for _ in range(3):
        mirror.reconcile(OWNER)
    assert len(calls) == 3 and mod["_MEMO"] == {}


def test_connections_are_kept_across_passes(keyed, monkeypatch):
    k = keyed
    opened = []
    real = k.mod["_open_ro"]
    monkeypatch.setitem(k.mod, "_open_ro", lambda p: opened.append(str(p)) or real(p))
    settle(k)
    board(k, "UPDATE tasks SET title='changed'")
    for _ in range(5):
        k.mirror.reconcile(OWNER)
    assert sorted(set(opened)) == sorted(opened)  # one open per file
    assert set(opened) <= {str(k.path), str(k.state), str(k.store.path)}


def test_cold_http_returns_ask_without_constructing_change_key(keyed, monkeypatch, tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from test_withdrawal import load_module, ROOT
    k = keyed
    scheduled = []
    monkeypatch.setitem(k.mod, '_schedule', lambda m, o, passes: scheduled.append(set(passes)))
    def key_trap(*args):
        raise RuntimeError('cold poll constructed redundant key')
    monkeypatch.setattr(k.mirror, '_key', key_trap)
    api = load_module('cold_key_api', ROOT / 'dashboard/plugin_api.py')
    api.RETRY_INTERVAL_S = 0
    app = FastAPI()
    router = api.create_router(ledger_dir=tmp_path / 'ledger', state_db=k.state, action_store=k.store,
                               owner_resolver=lambda sid: dict(OWNER, storedSessionId=sid), kanban_mirror=k.mirror)
    app.include_router(router)
    try:
        with TestClient(app) as client:
            response = client.get('/requests', params={'session': OWNER['storedSessionId']})
        assert response.status_code == 200
        assert [r['title'] for r in response.json()['requests']] == ['Choose a color']
        assert len(k.passes) == 1  # actual full local resolver, not a fake local pass
        assert k.mod['encoded'](OWNER) not in k.mod['_MEMO']
        assert scheduled == [{'remote'}]
    finally:
        router.stop_delivery()


@pytest.mark.parametrize('error', [sqlite3.OperationalError, OSError])
def test_cold_http_reader_warmup_failure_still_returns_ask(keyed, monkeypatch, tmp_path, error):
    """An optional retained reader must never gate the real first-response work."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from test_withdrawal import load_module, ROOT
    k = keyed
    scheduled, failed_opens = [], []
    monkeypatch.setitem(k.mod, '_schedule', lambda m, o, passes: scheduled.append(set(passes)))
    original_open = k.mod['_open_ro']

    def fail_store_reader(path):
        if str(path) == str(k.store.path):
            failed_opens.append(str(path))
            raise error('fixture retained-reader open failure')
        return original_open(path)

    monkeypatch.setitem(k.mod, '_open_ro', fail_store_reader)
    api = load_module('cold_reader_failure_api', ROOT / 'dashboard/plugin_api.py')
    api.RETRY_INTERVAL_S = 0
    app = FastAPI()
    router = api.create_router(ledger_dir=tmp_path / 'ledger', state_db=k.state, action_store=k.store,
                               owner_resolver=lambda sid: dict(OWNER, storedSessionId=sid), kanban_mirror=k.mirror)
    app.include_router(router)
    try:
        with TestClient(app) as client:
            response = client.get('/requests', params={'session': OWNER['storedSessionId']})
        assert failed_opens, 'fault injection did not exercise retained-reader warming'
        assert response.status_code == 200
        assert [r['title'] for r in response.json()['requests']] == ['Choose a color']
        assert len(k.passes) == 1
        assert k.mod['_local_seen'](k.mod['_local_key'](k.mirror, OWNER))
        assert k.mod['encoded'](OWNER) not in k.mod['_MEMO']
        assert scheduled == [{'remote'}]
    finally:
        router.stop_delivery()


def test_cold_full_failure_clears_stale_memo_without_warming_and_schedules_remote(keyed, monkeypatch):
    k = keyed
    settle(k)  # the memo can outlive local-seen LRU eviction
    k.mod['_LOCAL_SEEN'].clear()
    k.mod['_REMOTE_FRESH'].clear()
    scheduled = []
    monkeypatch.setitem(k.mod, '_schedule', lambda m, o, passes: scheduled.append(set(passes)))
    def fail(*args):
        raise OSError('fixture local read failure')
    monkeypatch.setattr(k.mirror, '_reconcile', fail)
    # Existing memo would skip the old pass. The cold full path must actually fail.
    with pytest.raises(OSError, match='fixture local read failure'):
        k.mirror.reconcile_poll(OWNER, 60)
    assert not k.mod['_local_seen'](k.mod['_local_key'](k.mirror, OWNER))
    assert k.mod['encoded'](OWNER) not in k.mod['_MEMO']
    assert scheduled == [{'remote'}]


def test_cold_bypass_retains_older_withdrawal_and_background_prepass_key(keyed, monkeypatch):
    import json
    k = keyed
    settle(k)
    old = k.store.requests(OWNER)[0]
    # An unrelated cold owner still reconciles older-owner departures synchronously.
    other = dict(OWNER, storedSessionId='session-b')
    board(k, "UPDATE tasks SET status='ready'")
    queued = []
    schedule = k.mod['_schedule']
    monkeypatch.setitem(k.mod, '_schedule', lambda m, o, passes: queued.append((o, passes)))
    original_key = k.mirror._key
    monkeypatch.setattr(k.mirror, '_key', lambda *args: pytest.fail('cold key'))
    k.mirror.reconcile_poll(other, 60)
    assert not k.store.requests(OWNER)
    assert k.store.get(OWNER, old['requestId'])['state'] == 'withdrawn'
    assert k.mod['encoded'](other) not in k.mod['_MEMO']
    monkeypatch.setattr(k.mirror, '_key', original_key)
    # Next warm poll schedules ordinary local work. Only its PRE-pass key is memoized.
    k.mirror.reconcile_poll(other, 60)
    assert 'local' in queued[-1][1]
    monkeypatch.setitem(k.mod, '_schedule', schedule)
    monkeypatch.setattr(k.mirror.remote, 'reconcile', lambda owner: None)
    k.mirror.reconcile_poll(other, 60)
    assert k.mod['_background_idle'](5)
    assert k.mod['encoded'](other) in k.mod['_MEMO']
    # A changed ask is not hidden by the bypass; a cold original owner sees it now.
    k.mod['_LOCAL_SEEN'].clear()
    monkeypatch.setitem(k.mod, '_schedule', lambda m, o, passes: queued.append((o, passes)))
    with sqlite3.connect(k.path) as c:
        c.execute("UPDATE tasks SET status='blocked',human_request=?", (json.dumps(dict(
            action='Pick again', interaction='reply', why_human='User choice', scope='New scope',
            evidence='fixture', choices=[dict(label='New', value='new')])),))
        c.execute("INSERT INTO task_events(task_id,kind) VALUES ('t_test','blocked')")
    k.mirror.reconcile_poll(OWNER, 60)
    assert len(k.store.requests(OWNER)) == 1
    assert k.store.requests(OWNER)[0]['requestId'] != old['requestId']


def test_cold_full_keeps_store_reader_without_holding_its_lock(keyed, monkeypatch):
    k = keyed
    monkeypatch.setitem(k.mod, '_schedule', lambda *args: None)
    full = k.mirror._reconcile
    checked = []
    def check(owner, fenced):
        entry = k.mod['_POOL'].get(str(k.store.path))
        assert entry is not None and entry.conn is not None, 'cold pass lost retained store reader'
        assert entry.owner is None, 'must not hold a shared reader lock across the full pass'
        checked.append(True)
        return full(owner, fenced)
    monkeypatch.setattr(k.mirror, '_reconcile', check)
    k.mirror.reconcile_poll(OWNER, 60)
    assert checked == [True]
    assert len(k.store.requests(OWNER)) == 1
