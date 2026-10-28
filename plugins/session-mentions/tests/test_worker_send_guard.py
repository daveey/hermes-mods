"""A Kanban worker must not inject a turn into a human chat.

Two entry paths queue a cross_session send: the tool (handle) and POST /enqueue.
Both refuse a worker → desktop/cli target before any outbox file is written, allow a
worker → worker session, and still allow a non-worker to message a human chat.
"""
from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("sm_guard_plugin", _ROOT / "__init__.py")
sm = importlib.util.module_from_spec(_spec)
sys.modules["sm_guard_plugin"] = sm
_spec.loader.exec_module(sm)  # type: ignore[union-attr]

DESKTOP = "@session:default@macbook/20261027_133954_1b6eae"
CLI = "@session:default/cli-chat"
WORKER = "@session:default@hub/20261028_035724_f6cfc3"


@pytest.fixture(autouse=True)
def _no_live_subscriptions(monkeypatch):
    """A refusal subscribes the chat to the worker's card; never let a test write the real board."""
    monkeypatch.setattr(sm.rf, "_default_add_sub", lambda **_kw: None)
    monkeypatch.setattr(sm.rf, "_this_gateway", lambda: "")


@pytest.fixture
def relay(tmp_path, monkeypatch):
    sent = []
    real_write = sm.rf.atomic_write
    for sub in ("outbox", "claimed", "results"):
        (tmp_path / sub).mkdir()
    monkeypatch.setattr(sm.rf, "root", lambda: tmp_path)
    monkeypatch.setattr(sm, "_sender_info", lambda sid: {"session_id": sid or ""})

    def write(path, data):
        real_write(path, data)
        if Path(path).parent.name == "outbox":
            sent.append(data)
            real_write(tmp_path / "results" / f"{data['id']}.json", {"ok": True, "echo": data["args"]})

    monkeypatch.setattr(sm.rf, "atomic_write", write)
    return sent


def _sources(mapping):
    def lookup(target):
        sid = sm.rf.session_id_of(target)
        return mapping.get(sid, mapping.get(target))
    return lookup


def test_tool_worker_to_user_chat_is_refused(relay, monkeypatch):
    monkeypatch.setenv("HERMES_KANBAN_TASK", "t_fixture")
    monkeypatch.setattr(sm.rf, "session_source", _sources({"20261027_133954_1b6eae": "desktop", "cli-chat": "cli"}))
    for target, source in ((DESKTOP, "desktop"), (CLI, "cli")):
        out = json.loads(sm.handle({"action": "send", "target": target, "message": "run this check"}))
        assert out["ok"] is False, out
        err = out["error"]
        assert "HERMES_KANBAN_TASK=t_fixture" in err
        assert f"source={source}" in err
        assert "hermes-hub create-task" in err
        assert "kanban_create" in err
        assert "kanban_block" in err and "blocker_id" in err
        assert "hermes-desktop-gateway-unavailable" in err
    assert relay == []


def test_tool_worker_to_worker_session_is_allowed(relay, monkeypatch):
    monkeypatch.setenv("HERMES_KANBAN_TASK", "t_fixture")
    monkeypatch.setattr(sm.rf, "session_source", _sources({"20261028_035724_f6cfc3": "kanban"}))
    out = json.loads(sm.handle({"action": "send", "target": WORKER, "message": "handoff"}))
    assert out["ok"] is True, out
    assert relay[-1]["args"]["target"] == WORKER
    assert relay[-1]["args"]["message"] == "handoff"


def test_tool_nonworker_to_user_chat_is_allowed(relay, monkeypatch):
    monkeypatch.delenv("HERMES_KANBAN_TASK", raising=False)
    monkeypatch.setattr(sm.rf, "session_source", _sources({"20261027_133954_1b6eae": "desktop"}))
    out = json.loads(sm.handle({"action": "send", "target": DESKTOP, "message": "from a chat"}))
    assert out["ok"] is True, out
    assert relay[-1]["args"]["message"] == "from a chat"


def test_tool_worker_unknown_target_fails_closed(relay, monkeypatch):
    monkeypatch.setenv("HERMES_KANBAN_TASK", "t_fixture")
    monkeypatch.setattr(sm.rf, "session_source", lambda _t: None)
    out = json.loads(sm.handle({"action": "send", "target": DESKTOP, "message": "maybe a chat"}))
    assert out["ok"] is False
    assert "could not be confirmed" in out["error"]
    assert relay == []


def _sessions(path: Path, rows: dict) -> None:
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY, source TEXT)")
    con.executemany("INSERT INTO sessions (id, source) VALUES (?, ?)", list(rows.items()))
    con.commit()
    con.close()


def _replica(path: Path, gw: str, sid: str, source: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE replica (gw TEXT, coll TEXT, key TEXT, data TEXT)")
    con.execute(
        "INSERT INTO replica (gw, coll, key, data) VALUES (?, 'sessions', ?, ?)",
        (gw, f"default/{sid}", json.dumps({"source": source, "id": sid})),
    )
    con.commit()
    con.close()


def test_session_source_reads_local_db_then_replica(tmp_path, monkeypatch):
    home = tmp_path / "hermes"
    home.mkdir()
    _sessions(home / "state.db", {"local-worker": "kanban"})
    hub = tmp_path / "hub.db"
    _replica(hub, "macbook", "remote-desk", "desktop")
    monkeypatch.setattr(sm.rf, "_hermes_home", lambda: home)
    monkeypatch.setattr(sm.rf, "replica_db", lambda: hub)
    assert sm.rf.session_source("@session:default/local-worker") == "kanban"
    assert sm.rf.session_source("@session:default@macbook/remote-desk") == "desktop"
    assert sm.rf.session_source("@session:default@macbook/missing") is None


def _api(tmp_path, monkeypatch):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    import types
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    home = tmp_path / "hermes"
    home.mkdir()
    _sessions(home / "state.db", {"desk": "desktop", "worker-sess": "kanban", "cli-chat": "cli"})
    monkeypatch.setitem(sys.modules, "hermes_constants", types.SimpleNamespace(get_hermes_home=lambda: home))
    monkeypatch.delitem(sys.modules, "_session_mentions_relay_files", raising=False)
    spec = importlib.util.spec_from_file_location("sm_guard_api", _ROOT / "dashboard" / "plugin_api.py")
    api = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(api)  # type: ignore[union-attr]
    app = FastAPI()
    app.include_router(api.router)
    return TestClient(app), home


def _post(client, rid, target, *, task=""):
    sender = {"kanban_task": task} if task else {}
    return client.post("/enqueue", json={
        "request": {
            "id": rid,
            "action": "send",
            "args": {"target": target, "message": "check"},
            "sender": sender,
        }
    })


def test_rest_worker_to_user_chat_is_refused(tmp_path, monkeypatch):
    client, home = _api(tmp_path, monkeypatch)
    for rid, target, source in (("w-desk", "@session:default/desk", "desktop"), ("w-cli", "@session:default/cli-chat", "cli")):
        res = _post(client, rid, target, task="t_fixture")
        body = res.json()
        assert body["ok"] is False, body
        assert f"source={source}" in body["error"]
        assert "kanban_block" in body["error"] and "blocker_id" in body["error"]
        assert "hermes-hub create-task" in body["error"]
    assert not list((home / "session_mentions" / "outbox").glob("*.json"))


def test_rest_worker_to_worker_session_is_allowed(tmp_path, monkeypatch):
    client, home = _api(tmp_path, monkeypatch)
    body = _post(client, "w-ok", "@session:default/worker-sess", task="t_fixture").json()
    assert body["ok"] is True, body
    assert (home / "session_mentions" / "outbox" / "w-ok.json").is_file()


def test_rest_nonworker_to_user_chat_is_allowed(tmp_path, monkeypatch):
    client, home = _api(tmp_path, monkeypatch)
    body = _post(client, "human", "@session:default/desk").json()
    assert body["ok"] is True, body
    assert (home / "session_mentions" / "outbox" / "human.json").is_file()
