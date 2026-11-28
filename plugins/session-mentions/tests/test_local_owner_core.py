"""Optional installed-core proof: actual authority admission and stored readback, no LLM."""
from contextlib import asynccontextmanager
import importlib.util
import os
from pathlib import Path

import pytest

if not os.environ.get("HERMES_CORE_CHECKOUT"):
    pytest.skip("installed-core authority fixture", allow_module_level=True)

spec = importlib.util.spec_from_file_location("sm_local_core", Path(__file__).resolve().parents[1] / "local_owner.py")
lo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lo)


@pytest.mark.asyncio
@pytest.mark.parametrize("post_commit_failure", [False, True])
async def test_real_owner_admits_once_without_desktop_and_reads_its_stored_rows(tmp_path, monkeypatch, post_commit_failure):
    from tests.gateway.test_session_end_delegations import _owner
    from gateway.session_controls import AuthorityConnection
    from gateway.session_local import create_local_session
    from gateway.session_authority import SessionAuthority
    from hermes_cli.gateway_client import rpc_error
    from hermes_state_runtime import list_session_admissions
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(SessionAuthority, "_schedule", lambda self, ref: None)
    db, authority = await _owner(tmp_path, monkeypatch)
    class Transport:
        def write(self, frame):
            pass
    conn = AuthorityConnection(authority, Transport(), {"user_id": "fixture", "profile_id": str(tmp_path)})
    ref = create_local_session(authority, conn.actor, {"request_id": "fixture", "source": "cli", "model": "fixture", "toolsets": []})
    db.append_message(ref.session_id, "user", "stored fixture question")
    db.append_message(ref.session_id, "assistant", "stored fixture answer")
    calls = []
    class Client:
        async def rpc(self, method, **params):
            calls.append(method)
            reply = await conn.dispatch({"id": len(calls), "method": method, "params": params})
            if "error" in reply:
                raise rpc_error(reply["error"])
            return reply["result"]
    @asynccontextmanager
    async def connect(home, endpoint):
        yield Client()
    target = (tmp_path, None, "default", ref.session_id)
    req = {"id": "b" * 32, "action": "read", "args": {}, "sender": {"session_id": "fixture-sender"}}
    original = conn
    try:
        read = await lo.deliver(target, req, connect=connect)
        assert read["ok"], read
        assert [row["content"] for row in read["messages"]] == ["stored fixture question", "stored fixture answer"]
        req = dict(req, action="send", args={"message": "fixture native send"})
        if post_commit_failure:
            import sqlite3
            def broken_publication(*args, **kwargs):
                raise sqlite3.OperationalError("fixture post-commit read failure")
            monkeypatch.setattr(authority, "_publish_pending", broken_publication)
        sent = await lo.deliver(target, req, connect=connect)
        if post_commit_failure:
            assert sent["status"] == "unknown" and not sent["retry_safe"], sent
            rows = list_session_admissions(db, session_id=ref.session_id, pending_only=False)
            assert len(rows) == 1 and rows[0]["payload"]["text"].endswith("fixture native send")
            return
        assert sent["ok"] and sent["delivered"], sent
        again = await lo.deliver(target, req, connect=connect)
        assert again["admission_id"] == sent["admission_id"], again
        rows = list_session_admissions(db, session_id=ref.session_id, pending_only=False)
        assert len(rows) == 1
        assert rows[0]["payload"]["text"].endswith("fixture native send")
        conn = AuthorityConnection(authority, Transport(), {"user_id": "outsider", "profile_id": str(tmp_path)})
        refused = await lo.deliver(target, dict(req, id="c" * 32), connect=connect)
        assert not refused["ok"] and refused["retry_safe"]
        assert len(list_session_admissions(db, session_id=ref.session_id, pending_only=False)) == 1
    finally:
        for task in getattr(original, "_notice_tasks", ()):
            await task
        db.close()


@pytest.mark.asyncio
async def test_native_socket_uses_bound_ticket_and_real_gateway_client(tmp_path, monkeypatch):
    import json
    from types import SimpleNamespace
    from websockets.asyncio.server import serve
    from hermes_cli import gateway_client as gc
    minted, calls = [], []
    def ticket(home, endpoint, **policy):
        minted.append((home, endpoint, policy))
        return "fixture-ticket"
    monkeypatch.setattr(gc, "_session_ticket", ticket)
    async def owner(ws):
        assert ws.request.path == "/api/ws"
        assert "hermes-gateway-ticket.fixture-ticket" in ws.request.headers["Sec-WebSocket-Protocol"]
        async for raw in ws:
            frame = json.loads(raw)
            calls.append(frame)
            await ws.send(json.dumps({"jsonrpc": "2.0", "id": frame["id"], "result": {
                "rows": [{"role": "assistant", "text": "socket fixture", "row_id": 1}]}}))
    async with serve(owner, "127.0.0.1", 0, subprotocols=[gc.GATEWAY_WS_PROTOCOL]) as server:
        endpoint = SimpleNamespace(api_origin=f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}")
        result = await lo.deliver((tmp_path, endpoint, "default", "chat"),
                                  {"id": "d" * 32, "action": "read", "args": {}})
    assert result["ok"] and result["messages"][0]["content"] == "socket fixture", result
    assert minted == [(tmp_path, endpoint, {"refuse_restart_wait": True})]
    assert calls[0]["method"] == "session.transcript"

