"""cross_task reaches cards on gateways no Desktop courier lists (Board worker nodes, `worker-N`) through the
hermes-hub courier instead of waiting 30 s for a Desktop that never claims them (self-heal agent-e4fd46c7a7)."""
from __future__ import annotations

import importlib.util
import json
import os
import sqlite3
import stat
import sys
import threading
import time
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location("sm_plugin_hub_route", Path(__file__).resolve().parent.parent / "__init__.py")
sm = importlib.util.module_from_spec(_spec)
sys.modules["sm_plugin_hub_route"] = sm
_spec.loader.exec_module(sm)  # type: ignore[union-attr]

TASKS = [
    {"gw": "worker-2", "owner_profile": "default", "board": "default", "id": "t_aaaa1111", "status": "running",
     "assignee": "default", "title": "disk reclaim producer fix"},
    {"gw": "worker-2", "owner_profile": "ops", "board": "infra", "id": "t_bbbb2222", "status": "blocked",
     "assignee": "ops", "title": "runner cache"},
]

STUB = r'''#!{py}
import json, os, sys
log = os.environ["HUB_STUB_LOG"]
with open(log, "a") as f:
    f.write(json.dumps({{"argv": sys.argv[1:], "HERMES_AGENT": os.environ.get("HERMES_AGENT"),
                        "HERMES_SESSION_ID": os.environ.get("HERMES_SESSION_ID")}}) + "\n")
if sys.argv[1] == "request":
    print("req-0123456789abcdef")
    sys.exit(0)
if sys.argv[1] == "tasks":
    gw = sys.argv[sys.argv.index("--gw") + 1] if "--gw" in sys.argv else None
    print(json.dumps([t for t in json.loads(os.environ["HUB_STUB_TASKS"]) if not gw or t["gw"] == gw]))
    sys.exit(0)
sys.exit(2)
'''


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Relay dir with live Desktop couriers for mac-mini/macbook (reach lists without worker-N), a hermes-hub
    replica that knows worker-2, a stub `hermes-hub` on PATH, and a fake Desktop draining the outbox."""
    base = tmp_path / "sm"
    for sub in ("outbox", "claimed", "results", "couriers"):
        (base / sub).mkdir(parents=True)
    for dev, reach in (("mac-mini", ["hub", "macbook", "mini"]), ("macbook", ["hub", "mini"])):
        (base / "couriers" / f"{dev}.json").write_text(json.dumps(
            {"device": dev, "aliases": [dev], "reach": reach, "home": False, "at": time.time()}))
    state = tmp_path / "hub-state"
    state.mkdir()
    con = sqlite3.connect(state / "hub.db")
    con.execute("CREATE TABLE replica (gw TEXT, coll TEXT, key TEXT, version INTEGER, seq INTEGER, data TEXT, "
                "updated_at INTEGER, PRIMARY KEY (gw, coll, key))")
    for gw in ("hub", "mini", "macbook", "worker-2"):
        con.execute("INSERT INTO replica VALUES (?, 'gateway', ?, 1, 1, '{}', 0)", (gw, gw))
    con.commit()
    con.close()
    bindir = tmp_path / "bin"
    bindir.mkdir()
    stub = bindir / "hermes-hub"
    stub.write_text(STUB.format(py=sys.executable))
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    log = tmp_path / "hub-calls.jsonl"
    monkeypatch.setenv("HUB_STUB_LOG", str(log))
    monkeypatch.setenv("HUB_STUB_TASKS", json.dumps(TASKS))
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setenv("HERMES_HUB_STATE", str(state))
    monkeypatch.delenv("HERMES_KANBAN_TASK", raising=False)
    monkeypatch.delenv("HERMES_SESSION_ID", raising=False)
    monkeypatch.delenv("HERMES_AGENT", raising=False)
    monkeypatch.setattr(sm.rf, "root", lambda: base)
    monkeypatch.setattr(sm.rf, "gateway_name", lambda: "hub")
    monkeypatch.setattr(sm, "_sender_info", lambda sid: {"session_id": sid or ""})
    monkeypatch.setattr(sm.bt, "enabled", lambda: False)
    monkeypatch.setattr(sm, "PICKUP_TIMEOUT", 3.0)
    sent, stop = [], threading.Event()

    def desktop():  # claims only routes on its couriers' reach lists, like the real drain
        while not stop.is_set():
            for f in (base / "outbox").glob("*.json"):
                req = json.loads(f.read_text())
                if req.get("route") not in ("", None, "hub", "mini", "macbook", "mac-mini"):
                    continue
                f.unlink()
                sent.append(req)
                (base / "results" / f.name).write_text(json.dumps({"ok": True, "echo": req["action"]}))
            for c in (base / "couriers").glob("*.json"):
                os.utime(c)  # keep the couriers fresh
            time.sleep(0.05)

    t = threading.Thread(target=desktop, daemon=True)
    t.start()

    def calls():
        return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []

    yield {"base": base, "sent": sent, "calls": calls}
    stop.set()


def test_comment_on_worker_card_goes_through_hub_courier(env):
    t0 = time.time()
    out = json.loads(sm.handle_task({"action": "comment", "target": "@task:default@worker-2/t_aaaa1111",
                                     "message": "please rebase"}, session_id="20261113_000000_abcdef"))
    assert time.time() - t0 < 2.5, "must not wait for a Desktop pickup"
    assert out["ok"] is True, out
    assert out["transport"] == "hub_courier" and out["request_id"] == "req-0123456789abcdef"
    req = [c for c in env["calls"]() if c["argv"][0] == "request"]
    assert req and req[-1]["argv"] == ["request", "worker-2", "tasks", "default/default/t_aaaa1111", "comment",
                                       "--arg", "text=please rebase"]
    # the agent stamp: hermes-hub's agent_comment_stamp reads these from its own environment
    assert req[-1]["HERMES_AGENT"] == "1" and req[-1]["HERMES_SESSION_ID"] == "20261113_000000_abcdef"
    assert not list((env["base"] / "outbox").glob("*.json")) and not env["sent"]


def test_comment_uses_owner_profile_and_board_from_replica(env):
    out = json.loads(sm.handle_task({"action": "comment", "target": "worker-2:t_bbbb2222", "message": "hi"}))
    assert out["ok"] is True, out
    req = [c for c in env["calls"]() if c["argv"][0] == "request"][-1]
    assert req["argv"][1:5] == ["worker-2", "tasks", "ops/infra/t_bbbb2222", "comment"]


def test_read_and_list_on_worker_answer_from_replica(env):
    out = json.loads(sm.handle_task({"action": "read", "target": "@task:default@worker-2/t_aaaa1111"}))
    assert out["ok"] is True and out["source"] == "replica" and out["task"]["id"] == "t_aaaa1111"
    out = json.loads(sm.handle_task({"action": "list", "gateway": "worker-2", "status": "blocked"}))
    assert out["ok"] is True and out["source"] == "replica"
    assert [t["id"] for t in out["tasks"]] == ["t_bbbb2222"]
    assert not env["sent"]


def test_relayed_user_message_is_refused_on_hub_courier(env):
    out = json.loads(sm.handle_task({"action": "comment", "target": "@task:default@worker-2/t_aaaa1111",
                                     "message": "yes", "relays_user_message": True}, session_id="s1"))
    assert out["ok"] is False and "relays_user_message" in out["error"]
    assert not [c for c in env["calls"]() if c["argv"][0] == "request"]


def test_unknown_gateway_fails_fast_and_names_the_command(env, caplog):
    t0 = time.time()
    with caplog.at_level("WARNING"):
        out = json.loads(sm.handle_task({"action": "comment", "target": "@task:default@nowhere/t_aaaa1111",
                                         "message": "hi"}))
    assert time.time() - t0 < 2.0
    assert out["ok"] is False
    assert "hermes-hub request" in out["error"] and "reach" in out["error"]
    assert "cross_task: unroutable route=nowhere" in caplog.text
    assert not list((env["base"] / "outbox").glob("*.json"))


def test_route_a_courier_reaches_still_uses_desktop_relay(env):
    out = json.loads(sm.handle_task({"action": "comment", "target": "@task:default@mini/t_cccc3333", "message": "hi"}))
    assert out == {"ok": True, "echo": "task_comment"}
    assert env["sent"] and env["sent"][0]["route"] == "mini"
    assert not env["calls"]()
