"""Outbox push: a write to the outbox emits `plugin.session-mentions.outbox` {feed, seq} so the Desktop courier
drains at once instead of polling every gateway every 3 s. The notice never carries the requests; an emit failure
never breaks the write; a watcher in the serving process covers writers in other processes and sends a heartbeat;
/ping with courier params refreshes the courier's liveness record without claiming anything."""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import time
import types
from pathlib import Path

import pytest

_DIR = Path(__file__).resolve().parent.parent

_spec = importlib.util.spec_from_file_location("sm_relay_files_push", _DIR / "relay_files.py")
rf = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rf)  # type: ignore[union-attr]

_pspec = importlib.util.spec_from_file_location("sm_plugin_push", _DIR / "__init__.py")
sm = importlib.util.module_from_spec(_pspec)
sys.modules["sm_plugin_push"] = sm
_pspec.loader.exec_module(sm)  # type: ignore[union-attr]


def _base(tmp_path: Path) -> Path:
    for sub in ("outbox", "claimed", "results"):
        (tmp_path / sub).mkdir(parents=True, exist_ok=True)
    return tmp_path


@pytest.fixture
def events(monkeypatch):
    got = []

    def broadcast(plugin_id, event, payload=None):
        got.append((plugin_id, event, payload))

    monkeypatch.setitem(sys.modules, "hermes_cli.plugin_events", types.SimpleNamespace(broadcast_plugin_event=broadcast))
    return got


@pytest.fixture
def broken(monkeypatch):
    def broadcast(*_a, **_k):
        raise RuntimeError("no gateway transport")

    monkeypatch.setitem(sys.modules, "hermes_cli.plugin_events", types.SimpleNamespace(broadcast_plugin_event=broadcast))


def test_enqueue_emits_a_notice_without_the_request(tmp_path, events):
    base = _base(tmp_path)
    assert rf.enqueue({"id": "r1", "action": "list", "args": {"secret": "x"}}, base=base)
    assert (base / "outbox" / "r1.json").exists()
    assert len(events) == 1
    plugin_id, event, payload = events[0]
    assert (plugin_id, event) == ("session-mentions", "outbox")
    assert set(payload) == {"feed", "seq"}  # a notice, never the items
    assert payload["feed"] == rf.feed_id(base)
    assert rf.enqueue({"id": "r2", "action": "list"}, base=base)
    assert events[1][2]["seq"] > payload["seq"]


def test_refused_enqueue_emits_nothing(tmp_path, events):
    base = _base(tmp_path)
    assert not rf.enqueue({"id": "r1", "action": "nope"}, base=base)
    assert events == []


def test_emit_failure_does_not_break_enqueue(tmp_path, broken):
    base = _base(tmp_path)
    assert rf.enqueue({"id": "r1", "action": "list"}, base=base) is True
    assert (base / "outbox" / "r1.json").exists()
    assert rf.notify_outbox(base) is False


def test_no_core_is_a_noop(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "hermes_cli.plugin_events", None)  # import raises ImportError
    base = _base(tmp_path)
    assert rf.enqueue({"id": "r1", "action": "list"}, base=base) is True
    assert rf.notify_outbox(base) is False


def test_tool_write_emits(tmp_path, events, monkeypatch):
    base = _base(tmp_path)
    monkeypatch.setattr(sm.rf, "root", lambda: base)
    monkeypatch.setitem(sys.modules, "hermes_cli.plugin_events",
                        types.SimpleNamespace(broadcast_plugin_event=lambda p, e, d=None: events.append((p, e, d))))
    out = json.loads(sm._relay("list", {"query": "x"}, None, 0, timeout_seconds=0))
    assert "error" in out  # nobody drained in zero seconds; the request was withdrawn
    assert [(p, e) for p, e, _ in events] == [("session-mentions", "outbox")]
    assert events[0][2]["feed"] == sm.rf.feed_id(base)


def test_tool_write_survives_emit_failure(tmp_path, broken, monkeypatch):
    base = _base(tmp_path)
    monkeypatch.setattr(sm.rf, "root", lambda: base)
    out = json.loads(sm._relay("list", {"query": "x"}, None, 0, timeout_seconds=0))
    assert "error" in out and "Timed out" in out["error"]


def test_feed_id_names_the_outbox_not_the_process(tmp_path):
    a, b = _base(tmp_path / "a"), _base(tmp_path / "b")
    assert rf.feed_id(a) == rf.feed_id(a)
    assert rf.feed_id(a) != rf.feed_id(b)
    assert str(os.getpid()) not in rf.feed_id(a).split(":", 1)[1]


# ── dashboard: watcher + ping heartbeat ────────────────────────────────────


def _api(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "hermes_constants", types.SimpleNamespace(get_hermes_home=lambda: tmp_path))
    spec = importlib.util.spec_from_file_location("sm_api_push", _DIR / "dashboard" / "plugin_api.py")
    api = importlib.util.module_from_spec(spec)
    pytest.importorskip("fastapi")
    spec.loader.exec_module(api)  # type: ignore[union-attr]
    monkeypatch.delitem(sys.modules, "_session_mentions_relay_files", raising=False)
    return api


def _write(base: Path, rid: str, age: float = 0.0) -> Path:
    path = base / "outbox" / f"{rid}.json"
    rf.atomic_write(path, {"id": rid, "action": "list", "created_at": time.time() - age, "ttl": 600})
    if age:
        os.utime(path, (time.time() - age, time.time() - age))
    return path


def test_watcher_pushes_new_requests_and_heartbeats_with_pending(tmp_path, monkeypatch, events):
    api = _api(tmp_path, monkeypatch)
    assert api.WATCH_ENABLED is False and api.ensure_watcher() is False  # tests/conftest.py: no threads in tests
    base = tmp_path / "session_mentions"
    api._rf()  # creates the dirs
    state: dict = {}
    t = time.time()
    assert api.watch_once(state, t) == "heartbeat"  # first step: tell the Desktop pushes reach it
    assert events[-1][2] == {"feed": api._rf().feed_id(), "seq": events[-1][2]["seq"], "heartbeat": True, "pending": False}
    # A writer in another process (user-terminal, coach, `hermes gateway run`) never emits itself.
    _write(base, "x1")
    assert api.watch_once(state, t + 1) == "outbox"
    assert events[-1][1] == "outbox" and set(events[-1][2]) == {"feed", "seq"}
    # Claimed: nothing to push, and the next heartbeat says nothing waits.
    os.replace(base / "outbox" / "x1.json", base / "claimed" / "x1.json")
    assert api.watch_once(state, t + 2) == ""
    assert api.watch_once(state, t + 1 + api.HEARTBEAT_S) == "heartbeat"
    assert events[-1][2]["pending"] is False


def test_lost_push_is_resent_every_5s_while_the_request_is_young(tmp_path, monkeypatch, events):
    """The first push is lost (socket reconnect): the watcher re-pushes every REPUSH_S while the request is
    unclaimed and younger than REPUSH_WINDOW_S, so a push-live Desktop drains it inside the 15 s / 30 s pickup
    timeouts instead of at its 60 s safety drain (the Desktop half: outbox-push.test.mjs)."""
    api = _api(tmp_path, monkeypatch)
    base = tmp_path / "session_mentions"
    api._rf().root()
    state: dict = {}
    t = time.time()
    api.watch_once(state, t)  # heartbeat
    _write(base, "r1")
    sent = [(dt, api.watch_once(state, t + dt)) for dt in range(1, 16)]
    pushes = [dt for dt, kind in sent if kind in ("outbox", "repush")]
    assert pushes[0] == 1 and sent[0][1] == "outbox"
    assert pushes[1:] == [6, 11]  # re-sent every 5 s: a Desktop that missed the first drains within 15 s
    os.replace(base / "outbox" / "r1.json", base / "claimed" / "r1.json")
    after = [api.watch_once(state, t + dt) for dt in range(16, 40)]
    assert not {"outbox", "repush"} & set(after)  # claimed: no more pushes (heartbeats only)


def test_old_unclaimed_request_is_not_repushed_but_heartbeat_says_pending(tmp_path, monkeypatch, events):
    api = _api(tmp_path, monkeypatch)
    base = tmp_path / "session_mentions"
    api._rf().root()
    t = time.time()
    state: dict = {}
    assert api.watch_once(state, t) == "heartbeat"
    _write(base, "stuck", age=api.REPUSH_WINDOW_S + 5)  # routed to a Mac nobody here serves
    assert api.watch_once(state, t + 1) == "outbox"  # first sighting
    assert [api.watch_once(state, t + dt) for dt in range(2, int(api.HEARTBEAT_S))] == [""] * (int(api.HEARTBEAT_S) - 2)
    assert api.watch_once(state, t + api.HEARTBEAT_S) == "heartbeat"
    assert events[-1][2]["pending"] is True


def test_watcher_lists_an_unsettled_dir_even_when_its_stat_key_did_not_change(tmp_path, monkeypatch, events):
    """ext4/HFS+ race: a temp-file create and its rename in one timestamp tick can leave the dir's stat key the
    same after a request lands. The key is trusted only once the dir is DIR_SETTLE_SECONDS old."""
    api = _api(tmp_path, monkeypatch)
    base = tmp_path / "session_mentions"
    api._rf().root()
    t = time.time()
    frozen = (1, 2, int(t * 1e9), int(t * 1e9))  # the same key at every step, dir modified "just now"
    monkeypatch.setattr(api, "_outbox_key", lambda _p: frozen)
    state: dict = {}
    assert api.watch_once(state, t) == "heartbeat"
    _write(base, "raced")
    assert api.watch_once(state, t + 0.5) == "outbox"
    # Once settled with nothing young, an unchanged key skips the listing.
    old = (1, 2, int((t - 100) * 1e9), int((t - 100) * 1e9))
    monkeypatch.setattr(api, "_outbox_key", lambda _p: old)
    os.replace(base / "outbox" / "raced.json", base / "claimed" / "raced.json")
    assert api.watch_once(state, t + 1) == ""
    calls = []
    monkeypatch.setattr(api, "_pending", lambda p: calls.append(p) or {})
    assert api.watch_once(state, t + 2) == ""
    assert calls == []


def test_watcher_survives_emit_failure_and_logs_once_at_warning(tmp_path, monkeypatch, broken, caplog):
    import logging
    api = _api(tmp_path, monkeypatch)
    live = api._rf()
    caplog.set_level(logging.DEBUG, logger="session-mentions")
    assert api.watch_once({}, time.time()) == "heartbeat"  # notify_outbox swallowed the failure
    assert live.notify_outbox() is False
    recs = [r for r in caplog.records if "outbox push not sent" in r.getMessage()]
    assert [r.levelno for r in recs] == [logging.WARNING, logging.DEBUG]


def test_watcher_thread_stops_on_unload_and_stays_stopped_until_register(tmp_path, monkeypatch):
    api = _api(tmp_path, monkeypatch)
    monkeypatch.setattr(api, "WATCH_ENABLED", True)
    monkeypatch.setattr(api, "WATCH_TICK_S", 0.01)
    monkeypatch.delitem(sys.modules, api.SHARED_KEY, raising=False)
    monkeypatch.setitem(sys.modules, "hermes_cli.plugin_events",
                        types.SimpleNamespace(broadcast_plugin_event=lambda *a, **k: None))
    try:
        assert api.ensure_watcher() is True
        assert api.ensure_watcher() is False  # one per process
        thread = sys.modules[api.SHARED_KEY].thread
        assert thread.is_alive()
        # The plugin module's on_unload hook (a separate import of __init__.py) stops it.
        unloads = []
        sm.register(types.SimpleNamespace(register_tool=lambda **k: None, register_hook=lambda *a: None,
                                          on_unload=unloads.append))
        assert unloads == [sm.stop_watcher]
        unloads[0]()
        thread.join(2)
        assert not thread.is_alive()
        assert api.ensure_watcher() is False  # routes stay mounted after unload; a /ping must not restart it
        sm.register(types.SimpleNamespace(register_tool=lambda **k: None, register_hook=lambda *a: None,
                                          on_unload=unloads.append))
        assert api.ensure_watcher() is True  # enabled again
    finally:
        api.stop(join_s=2)
        monkeypatch.delitem(sys.modules, api.SHARED_KEY, raising=False)


def test_ping_with_courier_refreshes_liveness_and_claims_nothing(tmp_path, monkeypatch, events):
    pytest.importorskip("httpx")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    api = _api(tmp_path, monkeypatch)
    app = FastAPI()
    app.include_router(api.router)
    client = TestClient(app)
    base = tmp_path / "session_mentions"
    plain = client.get("/ping").json()
    assert plain["push"] is True and plain["feed"] == api._rf().feed_id()
    assert not (base / "couriers").exists()  # a bare ping registers nobody
    api._rf().atomic_write(base / "outbox" / "r1.json", {"id": "r1", "action": "list", "created_at": time.time(), "ttl": 600})
    got = client.get("/ping", params={"courier": "macbook", "aliases": "macbook,laptop-1", "reach": "hub"}).json()
    assert got["ok"] is True and "requests" not in got
    assert (base / "outbox" / "r1.json").exists()  # nothing claimed
    rec = json.loads((base / "couriers" / "macbook.json").read_text())
    assert rec["aliases"] == ["laptop-1", "macbook"] and rec["reach"] == ["hub"]
    assert [c["device"] for c in api._rf().live_couriers(base)] == ["macbook"]
    assert client.get("/outbox", params={"courier": "macbook"}).json()["feed"] == plain["feed"]


def test_unload_before_any_route_still_keeps_the_watcher_off(tmp_path, monkeypatch):
    api = _api(tmp_path, monkeypatch)
    monkeypatch.setattr(api, "WATCH_ENABLED", True)
    monkeypatch.delitem(sys.modules, api.SHARED_KEY, raising=False)
    try:
        sm.stop_watcher()
        assert api.ensure_watcher() is False
    finally:
        api.stop(join_s=2)
        monkeypatch.delitem(sys.modules, api.SHARED_KEY, raising=False)
