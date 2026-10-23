"""Fixture hub.db: gateway "laptop" (this one) plus replicas of "mini" and "cloud"."""
import importlib.util
import json
import sqlite3
import time
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
PLUGIN = HERE.parent
NOW = 1_700_000_000.0


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def build_db(path: Path, schema: str, last_ok=NOW - 30, offline_since=None):
    db = sqlite3.connect(str(path))
    db.execute("PRAGMA synchronous = OFF")  # fixture only; one transaction, no fsync per row
    db.executescript(schema)
    meta = {"gateway": "laptop", "last_ok": last_ok, "cursor": 42, "head": "42"}
    if offline_since:
        meta["offline_since"] = offline_since
    for k, v in meta.items():
        db.execute("INSERT INTO meta VALUES (?, ?)", (k, str(v)))

    def pub(coll, key, data, version=1):
        db.execute("INSERT INTO published (coll, key, sha, data, version, seq) VALUES (?,?,?,?,?,?)",
                   (coll, key, "x", json.dumps(data), version, 1))

    def rep(gw, coll, key, data, version=1, updated_s=NOW - 300):
        db.execute("INSERT INTO replica VALUES (?,?,?,?,?,?,?)",
                   (gw, coll, key, version, 1, json.dumps(data), int(updated_s * 1000)))

    own_s = {"profile": "default", "id": "s_local", "title": "Local chat", "activity": NOW - 10, "archived": False}
    pub("sessions", "default/s_local", own_s, 3)
    rep("laptop", "sessions", "default/s_local", own_s, 2)  # own record echoed back: must not appear twice
    rep("mini", "sessions", "default/s_mini", {"profile": "default", "id": "s_mini", "title": "Mini chat",
                                               "activity": NOW - 400, "archived": False, "running": True}, 5,
        updated_s=NOW - 400)
    rep("mini", "sessions", "bb/s_old", {"profile": "bb", "id": "s_old", "title": "Archived", "activity": NOW - 9000,
                                         "archived": True}, 2, updated_s=NOW - 900)
    rep("cloud", "sessions", "alpha/s_cloud", {"profile": "alpha", "id": "s_cloud", "title": "Cloud chat",
                                               "activity": NOW - 100}, 1, updated_s=NOW - 100)
    pub("inbox", "default/s_card", {"profile": "default", "session_id": "s_card", "title": "Own card", "state": "open",
                                    "updated_at": NOW - 50, "actions": [{"id": "approve", "label": "Approve",
                                                                         "kind": "resolve"}]}, 4)
    rep("mini", "inbox", "default/s_mcard", {"profile": "default", "session_id": "s_mcard", "title": "Mini card",
                                             "state": "open", "updated_at": NOW - 500,
                                             "actions": [{"id": "approve", "label": "Approve", "kind": "resolve"}]}, 7)
    rep("mini", "inbox", "default/s_done", {"profile": "default", "session_id": "s_done", "title": "Done card",
                                            "state": "resolved", "updated_at": NOW - 500}, 2)
    pub("tasks", "default/t_own", {"board": "default", "id": "t_own", "title": "Own task", "status": "running",
                                   "priority": 1, "created_at": NOW - 1000,
                                   "sessions": [{"session": "s_local", "profile": None, "rel": "created"}]}, 2)
    rep("mini", "tasks", "bb/t_mini", {"board": "bb", "id": "t_mini", "title": "Mini task", "status": "blocked",
                                       "priority": 2, "created_at": NOW - 2000, "assignee": "worker",
                                       "run": {"status": "running", "started_at": NOW - 60},
                                       "sessions": [{"session": "s_local", "profile": "default", "rel": "subscribed"},
                                                    {"session": "s_parent", "profile": None, "rel": "created"}]}, 9)
    rep("mini", "tasks", "bb/t_done", {"board": "bb", "id": "t_done", "title": "Done", "status": "done",
                                       "created_at": NOW - 3000,
                                       "sessions": [{"session": "s_local", "rel": "created"}]}, 3)
    rep("mini", "gateway", "info", {"gateway": "mini", "host": "mini.example.com", "os": "darwin",
                                    "profiles": ["default", "bb"], "boards": ["default", "bb"]})
    rep("cloud", "gateway", "info", {"gateway": "cloud", "host": "cloud.example.com", "os": "linux",
                                     "profiles": ["alpha"], "boards": []}, updated_s=NOW - 100)
    db.execute("INSERT INTO sent (id, body, state, created_at) VALUES (?,?,?,?)",
               ("hr-old", json.dumps({"id": "hr-old", "to_gw": "mini", "coll": "sessions", "key": "default/s_mini",
                                      "action": "pin", "args": {}}), "queued", NOW - 20))
    db.execute("INSERT INTO sent (id, body, state, created_at, done_at) VALUES (?,?,?,?,?)",
               ("hr-done", json.dumps({"id": "hr-done", "to_gw": "mini", "coll": "sessions", "key": "default/s_mini",
                                       "action": "rename", "args": {"title": "x"}}), "done", NOW - 200, NOW - 100))
    db.commit()
    db.close()


@pytest.fixture
def fake_client(monkeypatch):
    fake = _load(HERE / "fake_hermes_hub.py", "fake_hermes_hub")
    fake.HUB_UP = False
    fake.POSTED.clear()
    return fake


@pytest.fixture
def hub(tmp_path, monkeypatch, fake_client):
    """(hubview module, state dir) with HERMES_HUB_CLIENT -> the fake client and the fixture hub.db."""
    state = tmp_path / "hubstate"
    state.mkdir()
    build_db(state / "hub.db", fake_client.SCHEMA)
    monkeypatch.setenv("HERMES_HUB_STATE", str(state))
    monkeypatch.setenv("HERMES_HUB_CLIENT", str(HERE / "fake_hermes_hub.py"))
    hv = _load(PLUGIN / "hubview.py", "hubview_under_test")
    # the plugin loads the client by path; hand it the same module object the test controls
    monkeypatch.setattr(hv, "load_client", lambda: fake_client)
    monkeypatch.setattr(hv, "_now", lambda: NOW)
    return hv, state


@pytest.fixture
def api_client(hub, monkeypatch):
    fastapi = pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    hv, state = hub
    api = _load(PLUGIN / "dashboard" / "plugin_api.py", "hub_sync_api_under_test")
    monkeypatch.setattr(api, "_hv", lambda: hv)
    app = fastapi.FastAPI()
    app.include_router(api.router)
    return TestClient(app), hv, state

