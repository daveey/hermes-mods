"""Gateway-wide send idempotency through the real relay/outbox boundary."""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("sm_dedupe_tests", Path(__file__).resolve().parents[1] / "__init__.py")
sm = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = sm
spec.loader.exec_module(sm)


@pytest.fixture
def env(tmp_path, monkeypatch):
    for name in ("outbox", "claimed", "results"):
        (tmp_path / name).mkdir()
    monkeypatch.setattr(sm.rf, "root", lambda: tmp_path)
    monkeypatch.setattr(sm.rf, "worker_send_refusal", lambda target: None)
    monkeypatch.setattr(sm, "_sender_info", lambda sid: {"session_id": sid})
    monkeypatch.setattr(sm, "_interrupted", lambda: False)
    monkeypatch.setattr(sm, "_yield_requested", lambda: False)
    requests = []
    results = [{"ok": True, "status": "queued"}]

    def courier(base):
        for path in (base / "outbox").glob("*.json"):
            req = json.loads(path.read_text())
            requests.append(req)
            path.rename(base / "claimed" / path.name)
            if results:
                sm.rf.write_result(req["id"], results[0], base)
    monkeypatch.setattr(sm.rf, "notify_outbox", courier)
    return tmp_path, requests, results


def send(sender="one", key="decision", target="@session:default@remote/scratch"):
    args = {"action": "send", "target": target, "message": "synthetic decision"}
    if key is not None:
        args["dedupe_key"] = key
    return json.loads(sm.handle(args, session_id=sender))


def test_two_senders_one_outbox_request(env):
    _, requests, _ = env
    assert send()["ok"]
    duplicate = send("two")
    assert duplicate["status"] == "duplicate"
    assert duplicate["delivered"] is False
    assert duplicate["original"]["request_id"] == requests[0]["id"]
    assert duplicate["original"]["sender"]["session_id"] == "one"
    assert len(requests) == 1


def test_different_key_two_requests(env):
    send()
    send("two", "other")
    assert len(env[1]) == 2


def test_different_target_two_requests(env):
    send()
    send("two", target="@session:default@remote/other")
    assert len(env[1]) == 2


def test_refused_send_can_retry(env):
    env[2][0] = {"ok": False, "status": "refused", "delivered": False, "retry_safe": True}
    assert not send()["ok"]
    env[2][0] = {"ok": True, "status": "queued"}
    assert send("two")["status"] == "queued"
    assert len(env[1]) == 2


def test_taken_back_can_retry(env, monkeypatch):
    published = []
    write = sm.rf.atomic_write
    def spy(path, payload):
        if path.parent.name == "outbox":
            published.append(payload["id"])
        return write(path, payload)
    monkeypatch.setattr(sm.rf, "atomic_write", spy)
    monkeypatch.setattr(sm.rf, "notify_outbox", lambda base: None)
    monkeypatch.setattr(sm, "PICKUP_TIMEOUT", -1)
    assert "error" in send()
    assert "error" in send("two")
    assert len(published) == 2
    assert not list((env[0] / "outbox").glob("*.json"))
    assert not json.loads((env[0] / "send-dedupe.json").read_text())


def test_pending_returns_duplicate_pending(env, monkeypatch):
    env[2].clear()
    monkeypatch.setattr(sm, "_yield_requested", lambda: True)
    first = send()
    assert first["status"] == "pending"
    second = send("two")
    assert second["status"] == "duplicate_pending"
    assert second["original"]["request_id"] == first["request_id"]
    assert len(env[1]) == 1


def test_no_key_unchanged(env):
    assert send(key=None)["status"] == "queued"
    assert send("two", key=None)["status"] == "queued"
    assert len(env[1]) == 2


def test_seven_day_pruning(env, monkeypatch):
    send()
    now = sm.time.time()
    monkeypatch.setattr(sm.time, "time", lambda: now + 7 * 86400 + 1)
    send("two")
    assert len(env[1]) == 2
    ledger = json.loads((env[0] / "send-dedupe.json").read_text())
    assert len(ledger) == 1
    assert next(iter(ledger.values()))["sender"]["session_id"] == "two"


def test_normalized_target_matches(env):
    send()
    assert send("two", target="remote:scratch")["status"] == "duplicate"
    assert len(env[1]) == 1


def test_interrupted_before_pickup_can_retry(env, monkeypatch):
    monkeypatch.setattr(sm.rf, "notify_outbox", lambda base: None)
    monkeypatch.setattr(sm, "_interrupted", lambda: True)
    assert "nothing was sent" in send()["error"]
    assert "nothing was sent" in send("two")["error"]
    assert not json.loads((env[0] / "send-dedupe.json").read_text())


def test_late_success_after_yield_finishes_receipt(env, monkeypatch):
    env[2].clear()
    monkeypatch.setattr(sm, "_yield_requested", lambda: True)
    first = send()
    sm.rf.write_result(first["request_id"], {"ok": True, "delivered": True, "status": "streaming"}, env[0])
    assert send("two")["status"] == "duplicate"
    assert len(env[1]) == 1


def test_unknown_delivery_keeps_pending(env):
    env[2][0] = {"ok": False, "delivered": None, "status": "unknown", "retry_safe": False}
    send()
    assert send("two")["status"] == "duplicate_pending"
    assert len(env[1]) == 1


def test_concurrent_processes_publish_once(env):
    import multiprocessing
    ctx = multiprocessing.get_context("fork")
    start = ctx.Event()
    def child(i):
        start.wait(5)
        req = {"id": str(i), "sender": {"session_id": str(i)}, "created_at": sm.time.time(),
               "args": {"target": "@session:default@remote/scratch"}}
        sm.rf.queue_send(env[0], req, "race")
    children = [ctx.Process(target=child, args=(i,)) for i in range(8)]
    try:
        for process in children:
            process.start()
        start.set()
        for process in children:
            process.join(10)
            assert process.exitcode == 0
        assert len(list((env[0] / "outbox").glob("*.json"))) == 1
    finally:
        for process in children:
            if process.is_alive():
                process.terminate()
                process.join()


def test_key_schema_and_validation(env):
    assert sm.SCHEMA["parameters"]["properties"]["dedupe_key"]["maxLength"] == 200
    assert "error" in send(key="x" * 201)
    assert not env[1]
