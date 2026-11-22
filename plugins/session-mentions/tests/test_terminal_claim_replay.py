"""Renderer death after pickup must not strand an idempotent terminal start."""
import importlib.util
import inspect
from pathlib import Path

import pytest


def replay(base, courier="desk"):
    # The old HTTP handler ignores the capability query; model that mixed-version path.
    kwargs = {"replay_user_terminal": True} if "replay_user_terminal" in inspect.signature(rf.claim_pending).parameters else {}
    return rf.claim_pending(base, courier=courier, **kwargs)

_spec = importlib.util.spec_from_file_location("terminal_claim_rf", Path(__file__).parents[1] / "relay_files.py")
rf = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rf)


def seed(base, now, action="user_terminal", args=None):
    rid = "a" * 32
    for sub in ("outbox", "claimed", "results"):
        (base / sub).mkdir(exist_ok=True)
    req = {"id": rid, "action": action, "route": "desk", "args": args or {"command": "echo ok"},
           "created_at": now, "ttl": 20}
    rf.atomic_write(base / "outbox" / f"{rid}.json", req)
    assert rf.claim_pending(base, courier="desk") == [req]
    return req


def test_renderer_restart_recovers_claim_after_pickup_ttl(tmp_path, monkeypatch):
    clock = [1000.25]
    monkeypatch.setattr(rf.time, "time", lambda: clock[0])
    req = seed(tmp_path, clock[0])
    clock[0] += 25  # renderer restarted; the original response/continuation was lost
    assert replay(tmp_path) == [req]
    assert rf.claim_pending(tmp_path, courier="other", replay_user_terminal=True) == []
    assert rf.claim_pending(tmp_path, courier="desk") == []  # legacy client: unchanged
    rf.write_result(req["id"], {"ok": True, "job_id": "job"}, tmp_path)
    (tmp_path / "results" / f'{req["id"]}.json').unlink()  # gateway consumed it
    assert replay(tmp_path) == []


def test_replay_is_bounded_and_only_for_terminal_starts(tmp_path, monkeypatch):
    clock = [1000.25]
    monkeypatch.setattr(rf.time, "time", lambda: clock[0])
    seed(tmp_path, clock[0])
    clock[0] += 61
    assert replay(tmp_path) == []
    clock[0] = 999  # future claim stamps cannot grant execution
    assert replay(tmp_path) == []


def test_non_idempotent_actions_are_never_replayed(tmp_path, monkeypatch):
    monkeypatch.setattr(rf.time, "time", lambda: 1000.25)
    for i, (action, args) in enumerate([
        ("send", {"message": "hello"}), ("user_terminal", {"job_id": "job", "enter": True}),
        ("user_terminal", {"withdraw_request": "a" * 32}), ("user_terminal", {"job_id": "job"}),
    ]):
        base = tmp_path / str(i)
        base.mkdir()
        seed(base, 1000.25, action, args)
        assert replay(base) == []


def test_result_ack_failure_never_exposes_a_replayable_outcome(tmp_path, monkeypatch):
    monkeypatch.setattr(rf.time, "time", lambda: 1000.25)
    req = seed(tmp_path, 1000.25)
    original = rf.atomic_write
    def fail_marker(path, payload):
        if path.parent.name == "claimed":
            raise OSError("fixture marker unavailable")
        return original(path, payload)
    monkeypatch.setattr(rf, "atomic_write", fail_marker)
    with pytest.raises(OSError):
        rf.write_result(req["id"], {"ok": False, "error": "pre-start failure"}, tmp_path)
    assert not (tmp_path / "results" / f'{req["id"]}.json').exists()
    monkeypatch.setattr(rf, "atomic_write", original)
    assert rf.write_result(req["id"], {"ok": False}, tmp_path)
    (tmp_path / "results" / f'{req["id"]}.json').unlink()
    assert replay(tmp_path) == []


def test_http_outbox_replay_capability(tmp_path, monkeypatch):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    spec = importlib.util.spec_from_file_location("terminal_replay_api", Path(__file__).parents[1] / "dashboard/plugin_api.py")
    api = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(api)
    monkeypatch.setattr(api, "ensure_watcher", lambda: None)
    monkeypatch.setattr(api, "_rf", lambda: rf)
    monkeypatch.setattr(rf, "root", lambda: tmp_path)
    now = [1000.25]
    monkeypatch.setattr(rf.time, "time", lambda: now[0])
    req = seed(tmp_path, now[0])  # first response lost
    app = FastAPI()
    app.include_router(api.router)
    with TestClient(app) as client:
        now[0] += 25
        assert client.get("/outbox?courier=desk").json()["requests"] == []
        got = client.get("/outbox?courier=desk&replay_user_terminal=1").json()
        assert got["requests"] == [req]
        assert client.post("/result", json={"id": req["id"], "result": {"ok": True}}).json() == {"ok": True}
        assert client.get("/outbox?courier=desk&replay_user_terminal=1").json()["requests"] == []
