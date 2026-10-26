"""Native registry terminal dispatch -> loop arm -> routed busy admission -> guard.

Isolated runner/agent fixtures and fake transport; native terminal and scheduling.
Watchers are observed, never hand-built or accelerated.
"""
import asyncio
import json
import shlex
import sys
import threading
import weakref
from types import SimpleNamespace

import pytest

from agent.subagent_lifecycle import bind_subagent_parent
from gateway.config import GatewayConfig, Platform
from gateway.profile_routing import parse_profile_routes
from gateway.run import GatewayRunner, _profile_runtime_scope
from gateway.session_context import clear_session_vars, set_session_vars
from gateway.session_identity import identity_of
from tests.gateway.test_completion_admission_compression_race import _QueuedTransport
from tests.tools.test_process_ownership_snapshot import capture
from tests.waiting import async_wait_until
from tools import terminal_tool  # noqa: F401 -- register the actual builtin handler
from tools.registry import registry as tools
from test_goal_process_profiles import assert_owned, no_model, profile_tree, validate  # noqa: F401


HOLD = "import threading; threading.Event().wait(90)"
DONE = "import os; print('native-terminal-completed'); print(os.environ['HERMES_HOME'])"


def command(code):
    return f"exec {shlex.quote(sys.executable)} {shlex.quote(str(code))}"


def dispatch(lane, code):
    """Run the registered tool on a real worker thread with native session scopes."""
    with _profile_runtime_scope(lane.home), bind_subagent_parent(lane.agent):
        tokens = set_session_vars(
            platform="telegram", chat_id="same-chat", chat_type="dm", user_id="human",
            session_key=lane.entry.session_key, session_id=lane.entry.session_id,
            profile=lane.name, async_delivery=True)
        try:
            result = json.loads(tools.dispatch("terminal", dict(
                command=command(lane.home / ("hold.py" if code == HOLD else "done.py")),
                workdir=str(lane.home), background=True, notify=True),
                task_id=lane.entry.session_id))
            assert result.get("error") is None, result
            assert result["notify_on_complete"] is True, result
            return result, threading.get_ident()
        finally:
            clear_session_vars(tokens)


@pytest.mark.asyncio
@pytest.mark.parametrize("transport,runtime,own_bot", [
    ("default", "default", False), ("alpha", "alpha", True),
    ("default", "alpha", False), ("alpha", "default", True),
    ("default", "alpha", True),
])
async def test_native_terminal_arm_and_shared_bot_ownership(profile_tree, monkeypatch, transport, runtime, own_bot):
    root, registry = profile_tree
    home = root if runtime == "default" else root / "profiles" / runtime
    # Fixture policy only. Real terminal backend/config loader and all command guards remain enabled.
    for target in (root, root / "profiles" / "alpha"):
        (target / "hold.py").write_text(HOLD)
        (target / "done.py").write_text(DONE)
        with (target / "config.yaml").open("a") as f:
            f.write(f"terminal:\n  env_type: local\n  cwd: {target}\n")
            f.write("approvals:\n  mode: manual\ncommand_allowlist: " +
                    json.dumps([command(target / "hold.py"), command(target / "done.py")]) + "\n")
    runner = GatewayRunner(GatewayConfig(sessions_dir=root / "sessions", multiplex_profiles=True))
    runner.config.profile_routes = parse_profile_routes([
        dict(name="native-route", platform="telegram", profile=runtime,
             chat_id="same-chat", bot_profile=transport)])
    runner._running = True
    runner._gateway_loop = asyncio.get_running_loop()
    monkeypatch.setattr("gateway.run._gateway_runner_ref", weakref.ref(runner))
    receiver, other = _QueuedTransport(), _QueuedTransport()
    for adapter, name in ((receiver, transport), (other, "alpha" if transport == "default" else "default")):
        adapter._running = True
        adapter.gateway_runner = runner
        adapter.set_owner_profile(name)
        adapter.set_message_handler(no_model)
    runner.adapters[Platform.TELEGRAM] = receiver if transport == "default" else other
    runner._profile_adapters["alpha"] = (
        {Platform.TELEGRAM: receiver} if transport == "alpha" else
        {Platform.TELEGRAM: other} if own_bot else {})
    source = receiver.build_source(chat_id="same-chat", chat_type="dm", user_id="human")
    receiver._canonicalize(source)
    identity = identity_of(source)
    assert (identity.transport_profile, identity.runtime_profile) == (transport, runtime)
    assert identity.runtime_home == home
    tasks, descriptors, processes = {}, {}, []
    original = runner._run_process_watcher

    async def observed(watcher):
        pid = watcher["session_id"]
        tasks[pid] = asyncio.current_task()
        descriptors[pid] = dict(watcher)
        await original(watcher)

    monkeypatch.setattr(runner, "_run_process_watcher", observed)
    try:
        with _profile_runtime_scope(home):
            entry = runner.session_store.get_or_create_session(source)
            receiver._active_sessions[entry.session_key] = asyncio.Event()
            agent = SimpleNamespace(session_id=entry.session_id, model="example", _current_turn_id="turn")
            with bind_subagent_parent(agent):
                uid = capture(agent)
            db = runner.session_store._db
            db.append_message(entry.session_id, "user", "Repair example app", message_uid=uid)
            lane = SimpleNamespace(name=runtime, home=home, adapter=receiver, entry=entry,
                                   agent=agent, db=db, uid=uid)
        result, thread = await asyncio.to_thread(dispatch, lane, HOLD)
        assert thread != threading.get_ident()
        lane.process = registry.get(result["session_id"])
        processes.append(lane.process)
        await async_wait_until(lambda: lane.process.id in tasks, timeout=10)
        assert not registry.pending_watchers
        assert descriptors[lane.process.id]["check_interval"] == 5
        assert descriptors[lane.process.id]["session_key"] == entry.session_key
        assert descriptors[lane.process.id]["parent_session_id"] == entry.session_id
        assert lane.process.watcher_platform == "telegram"
        assert lane.process.watcher_chat_id == "same-chat"
        with _profile_runtime_scope(home), pytest.raises(ValueError):
            validate(lane)  # A live arm alone is not proof of successful delivery.
        result, _ = await asyncio.to_thread(dispatch, lane, DONE)
        child = registry.get(result["session_id"])
        processes.append(child)
        await async_wait_until(lambda: child.id in tasks, timeout=10)
        await asyncio.wait_for(asyncio.shield(tasks[child.id]), timeout=25)
        assert child.exited and child.exit_code == 0
        assert not registry.pending_watchers
        queued = receiver.get_pending_message(entry.session_key)
        assert queued and queued.internal and "native-terminal-completed" in queued.text
        assert str(home) in queued.text
        assert queued.metadata["gateway_session_id"] == entry.session_id
        assert runner._resolve_profile_home_for_source(queued.source) == home
        assert runner._resolve_injection_adapter("telegram", queued.source) is receiver
        assert other.get_pending_message(entry.session_key) is None
        assert entry.transport_profile == transport
        assert db._conn.execute("SELECT transport_profile FROM sessions WHERE id=?",
                                (entry.session_id,)).fetchone()[0] == transport
        restored = runner._restored_source(entry)
        assert runner._delivery_adapter_for(restored) is receiver
        assert_owned(registry, lane)
        # The receiving bot's disappearance revokes authority, even when another bot lives.
        receiver._running = False
        with _profile_runtime_scope(home), pytest.raises(ValueError):
            validate(lane)
    finally:
        runner._running = False
        for task in tasks.values():
            task.cancel()
        await asyncio.gather(*tasks.values(), return_exceptions=True)
        for process in processes:
            process.notify_on_complete = False
            if process.process.poll() is None:
                process.process.kill()
            process.process.wait(timeout=5)
            assert process._completion_event.wait(5)
        runner.session_store.close_all_db_handles()
        runner.close_all_session_db_handles()
