"""A stale Desktop must not prevent local owner delivery or turn uncertainty into a resend."""
import asyncio
from contextlib import asynccontextmanager
import importlib.util
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

spec = importlib.util.spec_from_file_location("sm_local_tests", Path(__file__).resolve().parents[1] / "__init__.py")
sm = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = sm
spec.loader.exec_module(sm)
lo = sm.lo


@pytest.fixture
def rpc_error(monkeypatch):
    try:
        from hermes_cli.gateway_client import GatewayRPCError
    except ImportError:
        class GatewayRPCError(ValueError):
            pass
        monkeypatch.setitem(sys.modules, "hermes_cli.gateway_client", SimpleNamespace(GatewayRPCError=GatewayRPCError))
    return GatewayRPCError


@pytest.fixture
def env(tmp_path, monkeypatch):
    for sub in ("outbox", "claimed", "results", "couriers"):
        (tmp_path / sub).mkdir()
    stale = tmp_path / "couriers" / "desktop.json"
    stale.write_text('{"device":"desktop"}')
    os.utime(stale, (0, 0))
    monkeypatch.setattr(sm.rf, "root", lambda: tmp_path)
    monkeypatch.setattr(sm, "_sender_info", lambda sid: {"session_id": sid, "profile": "default"})
    monkeypatch.setattr(sm.rf, "worker_send_refusal", lambda target: None)
    monkeypatch.setattr(sm.rf, "gateway_name", lambda: "owner")
    monkeypatch.setattr(lo, "prepare", lambda action, args, rf: (tmp_path, None, "default", "chat"))
    monkeypatch.setattr(sm.rf, "notify_outbox", lambda base: None)
    return tmp_path


def request(action="send", wait=0):
    return {"id": "a" * 32, "action": action, "args": {"message": "synthetic message"},
            "sender": {"session_id": "sender", "profile": "default"}, "wait_for_reply": wait}


class Client:
    def __init__(self, failure=None):
        self.calls = []
        self.failure = failure

    async def rpc(self, method, **params):
        self.calls.append((method, params))
        if self.failure and method == self.failure[0]:
            raise self.failure[1]
        if method == "session.transcript":
            return {"rows": [{"row_id": 1, "role": "assistant", "text": "fixture reply", "timestamp": 1}], "next_cursor": None}
        if method == "session.resume":
            return {"session_id": "live-chat"}
        return {"status": "terminal", "admission_id": "admission", "result": {"final_response": "fixture reply"}}

    @asynccontextmanager
    async def connect(self, home, endpoint):
        yield self


def deliver(client, req):
    return asyncio.run(lo.deliver((Path("unused"), None, "default", "chat"), req, connect=client.connect))


@pytest.mark.parametrize("action", ["read", "send"])
def test_stale_desktop_local_entry_succeeds(env, monkeypatch, rpc_error, action):
    client = Client()
    monkeypatch.setattr(lo, "run", lambda target, req: deliver(client, req))
    out = json.loads(sm.handle({"action": action, "target": "@session:default/chat", "message": "synthetic"}, "sender"))
    assert out["ok"] and out["transport"] == "local_owner", out
    assert not list((env / "outbox").iterdir())
    if action == "read":
        assert out["messages"][0]["content"] == "fixture reply"
        assert [c[0] for c in client.calls] == ["session.transcript"]
        assert client.calls[0][1]["order"] == "latest"
    else:
        assert out["delivered"] and out["admission_id"] == "admission"
        assert client.calls[-1][1]["session_id"] == "live-chat"
        assert client.calls[-1][1]["text"].startswith("[Message from another Hermes session (@session:default/sender)]")


@pytest.mark.parametrize("failure,unknown", [("session.resume", False), ("prompt.submit", True)])
def test_transport_failure_never_falls_through(env, monkeypatch, rpc_error, failure, unknown):
    client = Client((failure, TimeoutError("synthetic")))
    monkeypatch.setattr(lo, "run", lambda target, req: deliver(client, req))
    out = json.loads(sm.handle({"action": "send", "target": "@session:default/chat", "message": "synthetic"}, "sender"))
    assert not out["ok"] and out["retry_safe"] is not unknown
    assert out["status"] == ("unknown" if unknown else "refused")
    assert not list((env / "outbox").iterdir())


def test_received_owner_refusal_is_retry_safe(rpc_error):
    out = deliver(Client(("prompt.submit", rpc_error("session_busy"))), request())
    assert out["retry_safe"] and out["status"] == "refused"


def test_admission_conflict_is_not_resendable(rpc_error):
    out = deliver(Client(("prompt.submit", rpc_error("admission_conflict"))), request())
    assert not out["retry_safe"] and out["status"] == "unknown"


def test_reply_uses_exact_admission_and_transcript(rpc_error):
    out = deliver(Client(), request(wait=1))
    assert out["status"] == "replied" and out["reply"] == "fixture reply"


def test_duplicate_native_send_reuses_reservation(env, monkeypatch, rpc_error):
    client = Client()
    monkeypatch.setattr(lo, "run", lambda target, req: deliver(client, req))
    args = {"action": "send", "target": "@session:default/chat", "message": "synthetic", "dedupe_key": "decision"}
    assert json.loads(sm.handle(args, "one"))["delivered"]
    assert json.loads(sm.handle(args, "two"))["status"] == "duplicate"
    assert sum(method == "prompt.submit" for method, _ in client.calls) == 1


def test_unknown_native_send_keeps_reservation(env, monkeypatch, rpc_error):
    client = Client(("prompt.submit", TimeoutError()))
    monkeypatch.setattr(lo, "run", lambda target, req: deliver(client, req))
    args = {"action": "send", "target": "@session:default/chat", "message": "synthetic", "dedupe_key": "decision"}
    assert json.loads(sm.handle(args, "one"))["status"] == "unknown"
    assert json.loads(sm.handle(args, "two"))["status"] == "duplicate_pending"


def test_desktop_winning_claim_prevents_native_send(env, monkeypatch):
    real = sm.rf.atomic_write
    def desktop(path, payload):
        real(path, payload)
        if path.parent.name == "outbox":
            path.rename(env / "claimed" / path.name)
            sm.rf.write_result(payload["id"], {"ok": True, "transport": "desktop"}, env)
    monkeypatch.setattr(sm.rf, "atomic_write", desktop)
    monkeypatch.setattr(lo, "run", lambda *args: pytest.fail("double delivery"))
    out = json.loads(sm.handle({"action": "send", "target": "@session:default/chat", "message": "synthetic"}, "sender"))
    assert out["transport"] == "desktop"


def test_worker_refusal_still_precedes_native_route(env, monkeypatch):
    monkeypatch.setattr(sm.rf, "worker_send_refusal", lambda target: "worker cannot send")
    monkeypatch.setattr(lo, "prepare", lambda *args: pytest.fail("must refuse first"))
    assert "worker cannot send" in json.loads(sm.handle({"action": "send", "target": "chat", "message": "synthetic"}))["error"]


@pytest.mark.parametrize("target", ["@session:default@remote/chat", "remote:chat", "@session:default@local/chat", "@session:../chat", "@session:default@owner@owner/chat"])
def test_foreign_ambiguous_or_malformed_routes_keep_desktop(target):
    rf = SimpleNamespace(device_info=lambda: {"device": "owner", "gateway": "owner", "aliases": ["host"]},
                         norm_name=lambda s: s.lower(), LOCAL_ALIASES={"local", "this-device"})
    assert lo.prepare("send", {"target": target}, rf) is None


def test_route_discovery_uses_exact_profile_and_no_start(monkeypatch, rpc_error):
    # Supply the production client's required symbols in core-free CI too.
    monkeypatch.setitem(sys.modules, "hermes_cli.gateway_client", SimpleNamespace(
        GatewayClient=object, GatewayRPCError=rpc_error, GATEWAY_WS_PROTOCOL="fixture",
        _session_ticket=lambda *args: None, gateway_ws_target=lambda *args: None))
    observed = []
    monkeypatch.setitem(sys.modules, "hermes_cli.profiles", SimpleNamespace(
        validate_profile_name=lambda p: observed.append(("validate", p)), get_profile_dir=lambda p: Path("fixture") / p))
    endpoint = object()
    monkeypatch.setitem(sys.modules, "hermes_cli.gateway_runtime", SimpleNamespace(
        discover_gateway_endpoint=lambda home: (observed.append(("discover", home)) or SimpleNamespace(state="ready", endpoint=endpoint))))
    rf = SimpleNamespace(device_info=lambda: {"device": "owner", "gateway": "owner"}, norm_name=lambda s: s, LOCAL_ALIASES={"local"})
    assert lo.prepare("read", {"target": "@session:research@owner/chat"}, rf) == (Path("fixture/research"), endpoint, "research", "chat")
    assert observed == [("validate", "research"), ("discover", Path("fixture/research"))]


@pytest.mark.parametrize("reason", ["storage_unavailable", "internal_error", "admission_conflict"])
def test_post_commit_capable_rpc_error_keeps_dedupe(env, monkeypatch, rpc_error, reason):
    client = Client(("prompt.submit", rpc_error(reason)))
    monkeypatch.setattr(lo, "run", lambda target, req: deliver(client, req))
    args = {"action": "send", "target": "@session:default/chat", "message": "synthetic", "dedupe_key": "decision"}
    first = json.loads(sm.handle(args, "one"))
    assert first["status"] == "unknown" and not first["retry_safe"]
    assert json.loads(sm.handle(args, "two"))["status"] == "duplicate_pending"


@pytest.mark.parametrize("action,method", [("read", "session.transcript"), ("send", "session.resume")])
def test_ready_old_owner_hands_back_before_submit(env, monkeypatch, rpc_error, action, method):
    client = Client((method, rpc_error("unknown_method")))
    monkeypatch.setattr(lo, "run", lambda target, req: deliver(client, req))
    def desktop(base):
        for path in (base / "outbox").glob("*.json"):
            path.rename(base / "claimed" / path.name)
            sm.rf.write_result(path.stem, {"ok": True, "transport": "desktop"}, base)
    monkeypatch.setattr(sm.rf, "notify_outbox", desktop)
    out = json.loads(sm.handle({"action": action, "target": "@session:default/chat", "message": "synthetic"}, "sender"))
    assert out["transport"] == "desktop"
    assert all(method != "prompt.submit" for method, _ in client.calls)


def test_known_admission_survives_reply_read_failure(rpc_error):
    out = deliver(Client(("prompt.receipt", rpc_error("storage_unavailable"))), request(wait=1))
    assert out["ok"] and out["delivered"] and out["admission_id"] == "admission"
    assert out["reply"] is None and "do not resend" in out["note"]


def test_missing_native_client_symbols_keeps_desktop(monkeypatch):
    monkeypatch.setitem(sys.modules, "hermes_cli.gateway_client", SimpleNamespace())
    rf = SimpleNamespace(device_info=lambda: {"gateway": "owner"}, norm_name=lambda s: s, LOCAL_ALIASES={"local"})
    assert lo.prepare("read", {"target": "@session:default@owner/chat"}, rf) is None


@pytest.mark.parametrize("after_submit", [False, True])
def test_stop_or_steering_never_cancels_or_repeats_admission(monkeypatch, rpc_error, after_submit):
    checks = iter([False, True] if after_submit else [True])
    monkeypatch.setattr(lo, "_stop_waiting", lambda: next(checks))
    client = Client()
    out = deliver(client, request(wait=1))
    assert out["delivered"] is after_submit
    assert sum(method == "prompt.submit" for method, _ in client.calls) == int(after_submit)
    if after_submit:
        assert out["ok"] and "admission remains live" in out["note"]
    else:
        assert out["retry_safe"]


def test_hung_reply_receipt_is_bounded_and_keeps_admission(rpc_error):
    import time
    class Hung(Client):
        async def rpc(self, method, **params):
            if method == "prompt.receipt":
                await asyncio.Future()
            return await super().rpc(method, **params)
    started = time.monotonic()
    out = deliver(Hung(), request(wait=1))
    assert time.monotonic() - started < 2
    assert out["ok"] and out["delivered"] and out["reply_timed_out"]

