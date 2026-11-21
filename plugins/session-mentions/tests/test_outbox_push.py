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


def test_watcher_emits_on_new_request_and_heartbeats(tmp_path, monkeypatch, events):
    api = _api(tmp_path, monkeypatch)
    assert api.WATCH_ENABLED is False and api.ensure_watcher() is False  # tests/conftest.py: no threads in tests
    base = tmp_path / "session_mentions"
    state: dict = {}
    t = 1000.0
    assert api.watch_once(state, t) == "heartbeat"  # first step: tell the Desktop pushes reach it
    assert events[-1][2].get("heartbeat") is True
    assert api.watch_once(state, t + 1) == ""
    # A writer in another process (user-terminal, coach, `hermes gateway run`) never emits itself.
    rf_live = api._rf()
    rf_live.atomic_write(base / "outbox" / "x1.json", {"id": "x1", "action": "list"})
    assert api.watch_once(state, t + 2) == "outbox"
    assert events[-1][1] == "outbox" and "heartbeat" not in events[-1][2]
    assert events[-1][2]["feed"] == rf_live.feed_id()
    assert api.watch_once(state, t + 3) == ""  # unchanged dir: no repeat
    # A claim empties the dir: it changed, but there is nothing to drain.
    os.replace(base / "outbox" / "x1.json", base / "claimed" / "x1.json")
    os.utime(base / "outbox", ns=(time.time_ns(), time.time_ns() + 5_000_000_000))
    assert api.watch_once(state, t + 4) == ""
    assert api.watch_once(state, t + 2 + api.HEARTBEAT_S) == "heartbeat"


def test_watcher_survives_emit_failure(tmp_path, monkeypatch, broken):
    api = _api(tmp_path, monkeypatch)
    assert api.watch_once({}, 1000.0) == "heartbeat"  # notify_outbox swallowed the failure


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
