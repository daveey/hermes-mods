"""Behavioral regressions for unkeyable resolvers and bounded retained state."""

import sqlite3
from types import SimpleNamespace

import pytest

from test_kanban_mirror import OWNER
from test_kanban_mirror_keyed import keyed, runs, settle


@pytest.mark.parametrize("resolver", ["session_exists", "session_present", "tip"])
def test_injected_resolver_bypasses_a_valid_settled_key(keyed, monkeypatch, resolver):
    k = keyed
    settle(k)
    assert not runs(k)  # All databases needed to compute a real key exist.
    original = getattr(k.mirror, resolver)
    monkeypatch.setattr(k.mirror, resolver, lambda sid: original(sid))
    assert k.mirror._key(OWNER, True) is None
    assert runs(k) and runs(k)


def test_injected_tip_change_without_database_write_withdraws_ask(keyed, monkeypatch):
    k = keyed
    settle(k)
    target = [OWNER["storedSessionId"]]
    monkeypatch.setattr(k.mirror, "tip", lambda sid: target[0])
    # Warm repeatedly: the broken guard would now have an unchanged memoized key.
    for _ in range(4):
        k.mirror.reconcile(OWNER)
    assert len(k.store.requests(OWNER)) == 1
    target[0] = "session-b"  # External resolver input, not any keyed database.
    k.mirror.reconcile(OWNER)
    assert k.store.requests(OWNER) == []


@pytest.fixture
def isolated_pool(keyed, monkeypatch):
    pool = {}
    monkeypatch.setitem(keyed.mod, "_POOL", pool)
    try:
        yield keyed.mod
    finally:
        for entry in pool.values():
            if entry.conn is not None:
                entry.conn.close()


def test_mid_read_sqlite_error_closes_then_reopens_connection(isolated_pool, tmp_path):
    mod = isolated_pool
    path = tmp_path / "reader.db"
    seed = sqlite3.connect(path)
    seed.execute("CREATE TABLE value(n)")
    seed.execute("INSERT INTO value VALUES (42)")
    seed.commit()
    seed.close()
    with mod["_kept"](path) as first:
        assert first.execute("SELECT n FROM value").fetchone()[0] == 42
    with pytest.raises(sqlite3.OperationalError, match="no such table"):
        with mod["_kept"](path) as failed:
            assert failed is first
            failed.execute("SELECT * FROM missing_table")
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        first.execute("SELECT 1")
    with mod["_kept"](path) as recovered:
        assert recovered is not first
        assert recovered.execute("SELECT n FROM value").fetchone()[0] == 42
    with mod["_kept"](path) as reused:
        assert reused is recovered


def test_pool_overflow_closes_idle_connections_and_reopens_evicted_path(isolated_pool, tmp_path):
    mod = isolated_pool
    connections = []
    paths = []
    # Exercise the production bound, not a reduced test-only limit.
    for i in range(mod["_POOL_MAX"] + 1):
        path = tmp_path / f"pool-{i}.db"
        sqlite3.connect(path).close()
        paths.append(path)
        with mod["_kept"](path) as conn:
            assert conn.execute("SELECT 1").fetchone()[0] == 1
            connections.append(conn)
        assert len(mod["_POOL"]) <= mod["_POOL_MAX"]
    assert str(paths[0]) not in mod["_POOL"]
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        connections[0].execute("SELECT 1")
    with mod["_kept"](paths[-1]) as newest:
        assert newest is connections[-1]
    with mod["_kept"](paths[0]) as reopened:
        assert reopened is not connections[0]
        assert reopened.execute("SELECT 1").fetchone()[0] == 1


def test_memo_overflow_evicts_oldest_full_pass_not_first_inserted(keyed, monkeypatch):
    k = keyed
    monkeypatch.setitem(k.mod, "_MEMO", {})
    clock = [1000.0]
    monkeypatch.setitem(k.mod, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    # No asks means each actual full pass is clean and makes no store writes.
    conn = sqlite3.connect(k.path)
    conn.execute("DELETE FROM tasks")
    conn.commit()
    conn.close()
    owners = [dict(OWNER, storedSessionId=f"owner-{i}") for i in range(k.mod["_MEMO_MAX"])]
    for owner in owners:
        k.mirror.reconcile(owner)
    memo = k.mod["_MEMO"]
    assert len(memo) == k.mod["_MEMO_MAX"]
    # Refresh the first-inserted owner's full-pass timestamp at the ceiling.
    clock[0] += k.mod["FULL_EVERY_S"]
    k.mirror.reconcile(owners[0])
    extra = dict(OWNER, storedSessionId="owner-overflow")
    k.mirror.reconcile(extra)
    assert len(memo) == k.mod["_MEMO_MAX"]
    assert k.mod["encoded"](owners[0]) in memo
    assert k.mod["encoded"](owners[1]) not in memo
    assert k.mod["encoded"](extra) in memo
    before = len(k.passes)
    k.mirror.reconcile(extra)
    assert len(k.passes) == before  # Retained owner still takes the fast path.
    k.mirror.reconcile(owners[1])
    assert len(k.passes) == before + 1  # Evicted owner recomputes successfully.
    assert len(memo) == k.mod["_MEMO_MAX"]
