"""Fail closed per card without making unrelated decisions disappear."""

import copy
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest
from test_kanban_mirror import fixture, decide, OWNER, core


def test_malformed_card_does_not_poison_other_cards(tmp_path, caplog):
    mirror, store, path, hr = fixture(tmp_path)
    hr["choices"] = ["legacy malformed choice"]
    with sqlite3.connect(path) as c:
        c.execute(
            "INSERT INTO tasks SELECT 't_bad','Bad','blocked',session_id,?,NULL FROM tasks",
            (json.dumps(hr),),
        )
    mirror.reconcile(OWNER)
    assert len(store.requests(OWNER)) == 1
    assert "t_bad" in caplog.text


def test_preexisting_different_request_cannot_acquire_binding(tmp_path, caplog):
    mirror, store, path, hr = fixture(tmp_path)
    snap = mirror._read_snapshot("default", "t_test", OWNER)
    expected = mirror._request(OWNER, "default", snap)
    forged = copy.deepcopy(expected)
    forged["scope"]["markdown"] = "Unrelated scope"
    store.create(OWNER, forged)
    mirror.reconcile(OWNER)
    assert mirror.binding(OWNER, expected["requestId"]) is None
    assert "conflict" in caplog.text.lower()
    with pytest.raises(ValueError, match="binding"):
        mirror.deliver(decide(store, forged))


def test_concurrent_projection_and_delivery_are_idempotent(tmp_path):
    mirror, store, path, hr = fixture(tmp_path)
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _: mirror.reconcile(OWNER), range(8)))
    assert len(store.requests(OWNER)) == 1
    op = decide(store, store.requests(OWNER)[0])
    with ThreadPoolExecutor(max_workers=4) as pool:
        receipts = list(pool.map(lambda _: mirror.deliver(op), range(8)))
    assert all(r == receipts[0] for r in receipts)
    with sqlite3.connect(path) as c:
        assert c.execute("SELECT count(*) FROM task_comments").fetchone()[0] == 1


def test_withdraw_race_is_benign_after_terminal_readback(tmp_path, monkeypatch):
    mirror, store, path, hr = fixture(tmp_path)
    mirror.reconcile(OWNER)
    original = store.withdraw

    def racing(*args, **kwargs):
        original(*args, **kwargs)
        raise core.ActionError("already closed", 409)

    monkeypatch.setattr(store, "withdraw", racing)
    with sqlite3.connect(path) as c:
        c.execute("UPDATE tasks SET status='archived'")
    mirror.reconcile(OWNER)
    assert not store.requests(OWNER)


def test_unavailable_board_preserves_request(tmp_path):
    mirror, store, path, hr = fixture(tmp_path)
    mirror.reconcile(OWNER)
    before = store.requests(OWNER)
    path.unlink()
    mirror.reconcile(OWNER)
    assert store.requests(OWNER) == before


def test_earliest_local_subscriber_owns_unowned_card(tmp_path):
    mirror, store, path, hr = fixture(tmp_path)
    with sqlite3.connect(path) as c:
        c.execute("UPDATE tasks SET session_id='worker-session'")
        c.executemany(
            "INSERT INTO kanban_notify_subs VALUES (?,?,?,?,?)",
            [
                ("t_test", "desktop", "session-b", OWNER["profile"], 2),
                ("t_test", "desktop", "session-a", OWNER["profile"], 1),
            ],
        )
    mirror.reconcile(dict(OWNER, storedSessionId="session-b"))
    mirror.reconcile(OWNER)
    assert len(store.requests(OWNER)) == 1
    assert not store.requests(dict(OWNER, storedSessionId="session-b"))


def test_real_session_tuple_source_filter(tmp_path, monkeypatch):
    mirror, store, path, hr = fixture(tmp_path)
    state = tmp_path / "state.db"
    with sqlite3.connect(state) as c:
        c.execute(
            "CREATE TABLE sessions(id TEXT,parent_session_id TEXT,end_reason TEXT,source TEXT)"
        )
        c.executemany(
            "INSERT INTO sessions VALUES (?,NULL,NULL,?)",
            [("session-a", "desktop"), ("worker", "kanban")],
        )
    monkeypatch.setattr(core, "_state_db", lambda: state)
    mirror.session_exists = mirror._user_session
    assert mirror._user_session("session-a")
    assert not mirror._user_session("worker")
    mirror.reconcile(OWNER)
    assert len(store.requests(OWNER)) == 1


@pytest.mark.parametrize("tamper", ["scope", "choice", "owner"])
def test_tampered_operation_leaves_card_unchanged(tmp_path, tamper):
    mirror, store, path, hr = fixture(tmp_path)
    mirror.reconcile(OWNER)
    op = copy.deepcopy(decide(store, store.requests(OWNER)[0]))
    if tamper == "scope":
        op["intent"]["request"]["scope"]["markdown"] = "other"
    elif tamper == "choice":
        op["decision"]["choiceId"] = "unknown"
    else:
        op["owner"]["storedSessionId"] = "session-b"
    with pytest.raises(ValueError):
        mirror.deliver(op)
    with sqlite3.connect(path) as c:
        assert c.execute("SELECT status FROM tasks").fetchone()[0] == "blocked"
        assert not c.execute("SELECT * FROM task_comments").fetchall()


def test_request_is_never_visible_without_receiver_binding(tmp_path, monkeypatch):
    mirror, store, path, hr = fixture(tmp_path)
    original = store.create

    def observe_after_commit(owner, request, **kwargs):
        result = original(owner, request, **kwargs)
        assert mirror.binding(owner, request["requestId"]) is not None
        return result

    monkeypatch.setattr(store, "create", observe_after_commit)
    mirror.reconcile(OWNER)
    assert len(store.requests(OWNER)) == 1


@pytest.mark.parametrize("payload", ["[]", "invalid-json"])
def test_bad_event_defers_delivery_not_entire_queue(tmp_path, payload):
    mirror, store, path, hr = fixture(tmp_path)
    mirror.reconcile(OWNER)
    op = decide(store, store.requests(OWNER)[0])
    with sqlite3.connect(path) as c:
        c.execute(
            "INSERT INTO task_events(task_id,kind,payload) VALUES ('t_test','unblocked',?)",
            (payload,),
        )
        c.execute("UPDATE tasks SET status='archived'")
    mirror.reconcile(OWNER)
    assert (
        store.operation(OWNER, op["operationId"])["operation"]["dispatchState"]
        == "pending"
    )
    with pytest.raises(ValueError):
        mirror.deliver(op)


def test_binding_write_failure_rolls_back_request(tmp_path, monkeypatch):
    mirror, store, path, hr = fixture(tmp_path)
    with store._tx() as c:
        c.execute(
            "CREATE TRIGGER reject_binding BEFORE INSERT ON kanban_projections BEGIN SELECT RAISE(ABORT, 'test rejection'); END"
        )
    mirror.reconcile(OWNER)
    assert store.requests(OWNER) == []


def test_projection_failure_does_not_hide_rest_queue(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from test_withdrawal import ROOT, load_module, complete_request

    mirror, store, path, hr = fixture(tmp_path)
    request = store.create(OWNER, complete_request())

    def fail(owner):
        raise sqlite3.OperationalError("projection unavailable")

    monkeypatch.setattr(mirror, "reconcile", fail)
    api = load_module("mirror_failure_api", ROOT / "dashboard" / "plugin_api.py")
    api.RETRY_INTERVAL_S = 0
    app = FastAPI()
    app.include_router(
        api.create_router(
            ledger_dir=tmp_path / "ledger",
            state_db=tmp_path / "missing.db",
            action_store=store,
            owner_resolver=lambda sid: dict(OWNER, storedSessionId=sid),
            kanban_boards=[("default", path)],
            kanban_mirror=mirror,
        )
    )
    response = TestClient(app).get("/requests", params={"session": "session-a"})
    assert response.status_code == 200
    assert [r["requestId"] for r in response.json()["requests"]] == [
        request["requestId"]
    ]


def test_legacy_custom_source_uses_explicit_user_subscription(tmp_path, monkeypatch):
    mirror, store, path, hr = fixture(tmp_path)
    state = tmp_path / "state.db"
    with sqlite3.connect(state) as c:
        c.execute(
            "CREATE TABLE sessions(id TEXT,parent_session_id TEXT,end_reason TEXT,source TEXT)"
        )
        c.execute(
            "INSERT INTO sessions VALUES ('session-a',NULL,NULL,'custom-legacy-source')"
        )
    monkeypatch.setattr(core, "_state_db", lambda: state)
    mirror.session_exists = mirror._user_session
    mirror.session_present = mirror._stored_session
    mirror.reconcile(OWNER)
    assert not store.requests(OWNER)
    with sqlite3.connect(path) as c:
        c.execute(
            "INSERT INTO kanban_notify_subs VALUES (?,?,?,?,?)",
            ("t_test", "tui", "session-a", OWNER["profile"], 1),
        )
    mirror.reconcile(OWNER)
    assert len(store.requests(OWNER)) == 1
    state.unlink()
    mirror.reconcile(OWNER)
    assert len(store.requests(OWNER)) == 1  # failed state read is not a departure


def test_retire_race_requires_terminal_readback(tmp_path, monkeypatch):
    mirror, store, path, hr = fixture(tmp_path)
    mirror.reconcile(OWNER)
    op = decide(store, store.requests(OWNER)[0])
    original = store.retire_operation

    def racing(*args, **kwargs):
        original(*args, **kwargs)
        raise core.ActionError("already cancelled", 409)

    monkeypatch.setattr(store, "retire_operation", racing)
    with sqlite3.connect(path) as c:
        c.execute("UPDATE tasks SET status='archived'")
    mirror.reconcile(OWNER)
    assert (
        store.operation(OWNER, op["operationId"])["operation"]["dispatchState"]
        == "cancelled"
    )


def test_tip_read_failure_does_not_withdraw(tmp_path):
    mirror, store, path, hr = fixture(tmp_path)
    mirror.reconcile(OWNER)
    before = store.requests(OWNER)

    def broken(sid):
        raise sqlite3.OperationalError("unavailable")

    mirror.tip = broken
    mirror.reconcile(OWNER)
    assert store.requests(OWNER) == before


def test_core_refusal_never_invents_receipt(tmp_path):
    mirror, store, path, hr = fixture(tmp_path)
    mirror.reconcile(OWNER)
    mirror.backend.unblock_task = lambda *args, **kwargs: False
    with pytest.raises(ValueError, match="refused"):
        mirror.deliver(decide(store, store.requests(OWNER)[0]))
    with sqlite3.connect(path) as c:
        assert not c.execute("SELECT * FROM task_comments").fetchall()
