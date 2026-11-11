"""Compression moves presentation, never changes the card ask's identity."""

import sqlite3

import pytest

from test_kanban_mirror import fixture, decide, OWNER, core


@pytest.mark.parametrize(
    "marker,source,reason,expected",
    [
        ({}, "desktop", "compression", "session-b"),
        ({"_branched_from": "session-a"}, "desktop", "compression", "session-a"),
        ({"_delegate_from": "session-a"}, "desktop", "compression", "session-a"),
        ({"_reset_from": "session-a"}, "desktop", "compression", "session-a"),
        ({}, "tool", "compression", "session-a"),
        ({}, "desktop", "auto_compressed_x", "session-a"),
    ],
)
def test_tip_uses_core_continuation_contract(
    tmp_path, monkeypatch, marker, source, reason, expected
):
    from core_runtime import require_isolated_core

    require_isolated_core()
    from hermes_state import SessionDB

    state = tmp_path / "state.db"
    db = SessionDB(db_path=state)
    try:
        db.create_session("session-a", source="desktop")
        db.create_session(
            "session-b",
            source=source,
            parent_session_id="session-a",
            model_config=marker,
        )
        db.end_session("session-a", end_reason=reason)
    finally:
        db.close()
    mirror, store, path, hr = fixture(tmp_path)
    monkeypatch.setattr(core, "_state_db", lambda: state)
    mirror.tip = mirror._tip
    assert mirror._tip("session-a") == expected
    mirror.reconcile(dict(OWNER, storedSessionId=expected))
    assert len(store.requests(dict(OWNER, storedSessionId=expected))) == 1
    other = "session-a" if expected == "session-b" else "session-b"
    mirror.reconcile(dict(OWNER, storedSessionId=other))
    assert not store.requests(dict(OWNER, storedSessionId=other))


def test_compression_moves_single_tile_to_tip(tmp_path):
    mirror, store, path, hr = fixture(tmp_path)
    mirror.tip = lambda sid: sid
    mirror.reconcile(OWNER)
    old = store.requests(OWNER)[0]
    tip_owner = dict(OWNER, storedSessionId="session-b")
    mirror.tip = lambda sid: "session-b"
    mirror.reconcile(tip_owner)
    mirror.reconcile(OWNER)
    assert not store.requests(OWNER)
    new = store.requests(tip_owner)
    assert len(new) == 1
    assert new[0]["owner"] == tip_owner
    assert new[0]["requestId"] != old["requestId"]


def test_recorded_decision_survives_compression_without_second_ask(tmp_path):
    mirror, store, path, hr = fixture(tmp_path)
    mirror.tip = lambda sid: sid
    mirror.reconcile(OWNER)
    op = decide(store, store.requests(OWNER)[0])
    mirror.tip = lambda sid: "session-b"
    mirror.reconcile(dict(OWNER, storedSessionId="session-b"))
    mirror.reconcile(OWNER)
    assert not store.requests(dict(OWNER, storedSessionId="session-b"))
    result = store.dispatch(OWNER, op["operationId"], mirror)
    assert result["operation"]["dispatchState"] == "delivered"
    with sqlite3.connect(path) as c:
        assert c.execute("SELECT status FROM tasks").fetchone()[0] == "ready"
        assert c.execute("SELECT count(*) FROM task_comments").fetchone()[0] == 1
