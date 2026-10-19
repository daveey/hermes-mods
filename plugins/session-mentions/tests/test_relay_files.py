"""Regression: a cross_session send failed with "This chat is open in another Hermes window"
whenever ANOTHER Mac's Desktop drained this gateway's outbox first. That Desktop reaches this
gateway through a separate (SSH-isolated) serve process, which can never submit into a chat
this Mac's own Desktop holds open. While this gateway's own ("home") Desktop courier is alive,
remote drainers must get nothing; when it is gone they must still be served.
"""
from __future__ import annotations

import importlib.util
import json
import os
import time
from pathlib import Path

_spec = importlib.util.spec_from_file_location("sm_relay_files", Path(__file__).resolve().parent.parent / "relay_files.py")
rf = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rf)  # type: ignore[union-attr]


def _base(tmp_path: Path) -> Path:
    for sub in ("outbox", "claimed", "results"):
        (tmp_path / sub).mkdir(parents=True, exist_ok=True)
    return tmp_path


def _request(base: Path, rid: str = "req1") -> None:
    rf.atomic_write(base / "outbox" / f"{rid}.json", {"id": rid, "action": "send", "created_at": time.time(), "ttl": 600})


def _heartbeat(base: Path, age: float = 0.0) -> None:
    rf.write_result(rf.HOME_COURIER_ID, {"at": time.time()}, base)
    path = base / "results" / f"{rf.HOME_COURIER_ID}.json"
    then = time.time() - age
    os.utime(path, (then, then))


def test_remote_drain_waits_for_live_home_courier(tmp_path):
    base = _base(tmp_path)
    _heartbeat(base)
    _request(base)
    assert rf.claim_pending(base, home=False) == []
    assert (base / "outbox" / "req1.json").exists()
    claimed = rf.claim_pending(base, home=True)
    assert [r["id"] for r in claimed] == ["req1"]


def test_remote_drain_serves_when_no_home_courier(tmp_path):
    base = _base(tmp_path)
    _request(base)
    assert [r["id"] for r in rf.claim_pending(base, home=False)] == ["req1"]


def test_remote_drain_serves_when_home_courier_went_quiet(tmp_path):
    base = _base(tmp_path)
    _heartbeat(base, age=rf.HOME_FRESH_SECONDS + 5)
    _request(base)
    assert [r["id"] for r in rf.claim_pending(base, home=False)] == ["req1"]


def test_heartbeat_is_not_a_request_result_and_survives_status(tmp_path):
    base = _base(tmp_path)
    _heartbeat(base)
    assert rf.home_courier_alive(base)
    assert rf.request_status(rf.HOME_COURIER_ID, base) == {"state": "unknown"}
    assert rf.home_courier_alive(base)


def test_enqueue_status_and_cancel(tmp_path):
    base = _base(tmp_path)
    assert rf.enqueue({"id": "fwd1", "action": "send", "args": {"target": "@session:default/x", "message": "hi"}}, base)
    assert rf.request_status("fwd1", base) == {"state": "pending"}
    assert rf.cancel("fwd1", base) is True
    assert rf.request_status("fwd1", base) == {"state": "unknown"}

    assert rf.enqueue({"id": "fwd2", "action": "send"}, base)
    [req] = rf.claim_pending(base, home=True)
    assert req["id"] == "fwd2" and req["created_at"] > 0
    assert rf.cancel("fwd2", base) is False  # already claimed: too late to cancel
    assert rf.request_status("fwd2", base) == {"state": "claimed"}
    rf.write_result("fwd2", {"ok": True, "status": "queued"}, base)
    assert rf.request_status("fwd2", base) == {"state": "done", "result": {"ok": True, "status": "queued"}}
    assert rf.request_status("fwd2", base) == {"state": "unknown"}  # popped once


def test_enqueue_rejects_bad_ids_and_actions(tmp_path):
    base = _base(tmp_path)
    assert not rf.enqueue({"id": "../evil", "action": "send"}, base)
    assert not rf.enqueue({"id": rf.HOME_COURIER_ID, "action": "send"}, base)
    assert not rf.enqueue({"id": "ok", "action": "rm"}, base)
    assert not list((base / "outbox").glob("*.json"))
    json.dumps(rf.request_status("../evil", base))


def test_named_profile_shares_the_gateway_root_outbox(tmp_path, monkeypatch):
    """The Desktop courier drains only the gateway's root dashboard, so a request from a named
    profile (HERMES_HOME=<root>/profiles/<name>) must land in the root outbox."""
    prof = tmp_path / "profiles" / "alpha-follow"
    prof.mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(prof))
    import sys
    import types
    monkeypatch.setitem(sys.modules, "hermes_constants", types.SimpleNamespace(get_hermes_home=lambda: prof))
    assert rf.root() == tmp_path / "session_mentions"
    monkeypatch.setitem(sys.modules, "hermes_constants", types.SimpleNamespace(get_hermes_home=lambda: tmp_path))
    assert rf.root() == tmp_path / "session_mentions"
