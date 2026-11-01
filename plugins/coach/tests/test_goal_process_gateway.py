"""Gateway return consumer + canonical database + coach public gate, isolated."""
import importlib.util
import socket
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.gateway.test_process_consumer_health import owned

spec = importlib.util.spec_from_file_location("gateway_process_goal", Path(__file__).parents[1] / "goal_disposition.py")
gd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gd)


def args(owned):
    p, entry = owned[3], owned[4]
    v = dict(goal="Repair example app", actionable=True, unfinished=True, disposition="owned_continuing",
             evidence_refs=["candidate", "user:0"], boundary_kind=None, confidence=.99,
             continuation_ref=dict(gateway=socket.gethostname(), profile="default", process_id=p.id))
    evidence = gd.evidence_for([dict(role="user", content=v["goal"])], "The native process is continuing.")
    return v, evidence, entry.session_id


@pytest.mark.asyncio
async def test_gateway_public_gate_accepts_then_preserves_hold_on_stop(owned, monkeypatch):
    runner, adapter, registry, process, entry, event, _ = owned
    assert await runner._inject_watch_notification("fixture completed", event) is True
    v, evidence, sid = args(owned)
    assert gd.validate_disposition(v, evidence, session_id=sid)["disposition"] == "owned_continuing"
    events = []
    monkeypatch.setattr(gd, "settings", lambda: {"mode": "enforce"})
    monkeypatch.setattr(gd, "aux_judge", lambda *a: v)
    gate = gd.build_gate(SimpleNamespace(log=events.append))
    kw = dict(session_id=sid, final_response="The native process is continuing.",
              turn_messages=[dict(role="user", content=v["goal"])])
    assert gate(**kw) is None
    assert events[-1]["disposition"] == "owned_continuing"
    runner._invalidate_session_run_generation(entry.session_key, reason="stop")
    with pytest.raises(ValueError):
        gate(**kw, attempt=1)
    assert gate(**kw) is None
    assert events[-1]["event"] == "goal_disposition_unknown"


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["uid", "content", "profile", "home", "stop-during-read"])
async def test_gateway_exact_scope_and_final_read(owned, monkeypatch, tmp_path, change):
    runner, _, registry, process, entry, event, _ = owned
    assert await runner._inject_watch_notification("fixture completed", event) is True
    v, evidence, sid = args(owned)
    assert gd.validate_disposition(v, evidence, session_id=sid)["disposition"] == "owned_continuing"
    db = runner.session_store._db
    if change == "uid":
        db.append_message(sid, "user", v["goal"], message_uid="new-uid")
    elif change == "content":
        db._conn.execute("UPDATE messages SET content='other request' WHERE session_id=?", (sid,))
        db._conn.commit()
    elif change == "profile":
        v["continuation_ref"]["profile"] = "other"
    elif change == "home":
        monkeypatch.setattr("hermes_constants.get_hermes_home", lambda: tmp_path / "foreign")
    else:
        read = registry.ownership_snapshot
        calls = []
        def revoke_at_second_read(pid):
            calls.append(pid)
            snap = read(pid)
            if len(calls) == 2:
                runner._invalidate_session_run_generation(entry.session_key, reason="stop")
            return snap
        monkeypatch.setattr(registry, "ownership_snapshot", revoke_at_second_read)
    with pytest.raises(ValueError):
        gd.validate_disposition(v, evidence, session_id=sid)


@pytest.mark.asyncio
async def test_real_process_exit_watcher_batch_and_public_gate(owned, monkeypatch, tmp_path):
    """No forged completion/acceptance: real child exit -> watcher -> native queue -> guard."""
    import shlex
    import sys
    # The macOS production interpreter lives in "Hermes Python.app": unquoted, the shell runs
    # ".../Hermes" and exits 127 instead of printing 123456.
    # A live-armed watcher runs in an empty context (arm_process_watcher), so it cannot see the
    # fixture's context-local profile scope. A single-profile gateway resolves its launch profile
    # from the process env, so the env names the fixture home too; otherwise the completion
    # pre-flight reads the real ~/.hermes, finds no session DB and retries forever.
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    from gateway.session_context import scoped_current_session_id
    runner, adapter, registry, running, entry, _, _ = owned
    with scoped_current_session_id(entry.session_id):
        bootstrap = registry.spawn_local(f"{shlex.quote(sys.executable)} -c 'print(123456)'",
                                         session_key=entry.session_key)
        bootstrap.notify_on_complete = True
    try:
        # The unchanged native watcher builds the completion from the real Popen,
        # coalesces it, proves the route, and obtains the real base-adapter admission.
        import weakref
        from tools.terminal_tool_background import _register_completion_watcher
        from gateway.process_consumer_health import receipt_version
        from tests.waiting import async_wait_until
        monkeypatch.setattr("gateway.run._gateway_runner_ref", weakref.ref(runner))
        bootstrap.watcher_platform = "telegram"
        bootstrap.watcher_chat_id = "process-proof"
        _register_completion_watcher(registry, bootstrap, entry.session_key)
        await async_wait_until(lambda: receipt_version(registry.ownership_snapshot(running.id)) is not None,
                               timeout=15)
        assert bootstrap.exited and bootstrap.exit_code == 0
        queued = adapter.get_pending_message(entry.session_key)
        assert queued is not None and queued.internal and "123456" in queued.text
        assert queued.metadata["gateway_session_id"] == entry.session_id
        v, evidence, sid = args(owned)
        assert gd.validate_disposition(v, evidence, session_id=sid)["disposition"] == "owned_continuing"
    finally:
        if bootstrap.process.returncode is None:
            bootstrap.process.kill()
        bootstrap.process.wait(timeout=5)
        bootstrap._completion_event.wait(5)
