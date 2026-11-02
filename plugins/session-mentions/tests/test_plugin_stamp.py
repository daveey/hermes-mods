"""Loaded plugin version per Desktop (t_01a199f0b0f218b0): on 2026-11-02 the MacBook Desktop answered
`unknown action authority_read` for 35 min after a git pull had landed the action in its plugin.js;
nothing showed that it was running old code. Couriers now send git blob shas of the plugin.js they
RUN and the one on their disk with every drain; the gateway keeps them in couriers/<device>.json and
`relay_files.py couriers` flags a stale renderer."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("sm_relay_files_stamp", _HERE.parent / "relay_files.py")
rf = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rf)  # type: ignore[union-attr]

OLD = "a" * 40
NEW = "b" * 40


def test_git_blob_sha_matches_git():
    data = b"export default {}\n"
    assert rf.git_blob_sha(data) == hashlib.sha1(b"blob 18\0" + data).hexdigest()
    assert rf.git_blob_sha(b"") == "e69de29bb2d1d6434b8b29ae775ad8c2e48c5391"  # git's empty blob


def test_plugin_stamp_validates_shas():
    assert rf.plugin_stamp("", NEW) is None
    assert rf.plugin_stamp("not-a-sha", NEW) is None
    assert rf.plugin_stamp(OLD.upper(), "junk", "17") == {"loaded": OLD, "disk": "", "loaded_at": 17.0}


def test_courier_versions_flags_stale_behind_ok_unknown(tmp_path):
    base = tmp_path
    rf.register_courier(base, "macbook", home=True, plugin=rf.plugin_stamp(OLD, NEW, 1))   # missed reload
    rf.register_courier(base, "mac-mini", plugin=rf.plugin_stamp(OLD, OLD, 1))             # not pulled
    rf.register_courier(base, "studio", plugin=rf.plugin_stamp(NEW, NEW, 1))               # current
    rf.register_courier(base, "old-desktop")                                              # pre-stamp
    rows = {r["device"]: r for r in rf.courier_versions(base, expected=NEW)}
    assert {d: r["status"] for d, r in rows.items()} == {
        "macbook": "stale", "mac-mini": "behind", "studio": "ok", "old-desktop": "unknown"}
    assert rows["macbook"]["live"] is True and rows["macbook"]["loaded"] == OLD
    assert json.loads((base / "couriers" / "macbook.json").read_text())["plugin"]["disk"] == NEW


def test_outbox_route_records_the_stamp_and_the_claimer(tmp_path, monkeypatch):
    """Entry path: the Desktop's GET /outbox?courier=..&plugin=..&plugin_disk=.. ."""
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    import types
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    monkeypatch.setitem(sys.modules, "hermes_constants", types.SimpleNamespace(get_hermes_home=lambda: tmp_path))
    monkeypatch.setitem(sys.modules, "hermes_cli.kanban_capability_registry", types.SimpleNamespace(self_gateway=lambda: "hub"))
    spec = importlib.util.spec_from_file_location("sm_api_stamp", _HERE.parent / "dashboard" / "plugin_api.py")
    api = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(api)  # type: ignore[union-attr]
    monkeypatch.delitem(sys.modules, "_session_mentions_relay_files", raising=False)
    app = FastAPI()
    app.include_router(api.router)
    client = TestClient(app)
    base = tmp_path / "session_mentions"
    for sub in ("outbox", "claimed", "results"):
        (base / sub).mkdir(parents=True, exist_ok=True)
    rf.atomic_write(base / "outbox" / "r1.json", {"id": "r1", "action": "authority_read", "route": "",
                                                  "created_at": time.time(), "ttl": 600})
    got = client.get("/outbox", params={"courier": "macbook", "aliases": "macbook", "reach": "hub",
                                        "plugin": OLD, "plugin_disk": NEW, "plugin_at": "1762048000"}).json()
    assert [r["id"] for r in got["requests"]] == ["r1"]
    rec = json.loads((base / "couriers" / "macbook.json").read_text())
    assert rec["plugin"] == {"loaded": OLD, "disk": NEW, "loaded_at": 1762048000.0}
    claimed = json.loads((base / "claimed" / "r1.json").read_text())
    assert claimed["claimed_by"] == "macbook" and claimed["claimed_plugin"] == OLD
    # A Desktop that predates the stamp still drains and registers, with no plugin field.
    client.get("/outbox", params={"courier": "mac-mini", "aliases": "mac-mini", "reach": "hub"})
    assert "plugin" not in json.loads((base / "couriers" / "mac-mini.json").read_text())


def test_cli_couriers_exits_1_on_a_live_stale_desktop(tmp_path):
    """Entry path: `python3 relay_files.py couriers [--json]`."""
    home = tmp_path / ".hermes"  # HOME and HERMES_HOME agree, whether or not hermes_constants imports
    base = home / "session_mentions"
    base.mkdir(parents=True)
    rf.register_courier(base, "macbook", plugin=rf.plugin_stamp(OLD, NEW, 1))
    env = {"HERMES_HOME": str(home), "HOME": str(tmp_path), "PATH": "/usr/bin:/bin"}
    run = subprocess.run([sys.executable, str(_HERE.parent / "relay_files.py"), "couriers", "--json"],
                         capture_output=True, text=True, env=env, timeout=60)
    assert run.returncode == 1, run.stderr
    rows = json.loads(run.stdout)
    assert rows[0]["device"] == "macbook" and rows[0]["status"] == "stale"
    rf.register_courier(base, "macbook", plugin=rf.plugin_stamp(NEW, NEW, 2))
    run = subprocess.run([sys.executable, str(_HERE.parent / "relay_files.py"), "couriers"],
                         capture_output=True, text=True, env=env, timeout=60)
    assert run.returncode == 0, run.stderr
    assert "macbook" in run.stdout
