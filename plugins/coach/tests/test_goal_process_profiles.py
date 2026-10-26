"""Real scoped profiles, native child/watch/queue receipts, and public ownership gate.

No transport, model, config loader, identity resolver or ownership read is mocked.
"""
import asyncio
from contextlib import asynccontextmanager
import importlib.util
from pathlib import Path
import shlex
import socket
import sys
from types import SimpleNamespace

import pytest

from agent.subagent_lifecycle import bind_subagent_parent
from gateway.config import GatewayConfig, Platform
from gateway.process_consumer_health import receipt_version
from gateway.run import GatewayRunner, _profile_runtime_scope
from gateway.session_context import scoped_current_session_id
from hermes_cli.profiles import get_active_profile_name, get_profile_dir, profiles_to_serve
from hermes_constants import get_hermes_home
from tests.gateway.test_completion_admission_compression_race import _QueuedTransport
from tests.tools.test_process_ownership_snapshot import capture
from tests.waiting import async_wait_until
from tools.process_registry import ProcessRegistry

spec = importlib.util.spec_from_file_location(
    "profile_process_goal", Path(__file__).parents[1] / "goal_disposition.py")
gd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gd)


@pytest.fixture
def profile_tree(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    for name in ("default", "alpha", "beta", "main"):
        target = home if name == "default" else home / "profiles" / name
        target.mkdir(parents=True, exist_ok=True)
        # Suppress only transport-facing status receipts, not native completion admission.
        (target / "config.yaml").write_text(
            "display:\n  background_process_notifications: error\n")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr("hermes_cli.lifecycle.invoke_hook", lambda *a, **k: [])
    registry = ProcessRegistry()
    monkeypatch.setattr("tools.process_registry.process_registry", registry)
    assert dict(profiles_to_serve(True))["alpha"] == home / "profiles" / "alpha"
    return home, registry


async def no_model(event):
    raise AssertionError("native admission must queue, never start a model")


def spawn(registry, lane, code):
    with _profile_runtime_scope(lane.home), bind_subagent_parent(lane.agent), \
            scoped_current_session_id(lane.entry.session_id):
        process = registry.spawn_local(
            f"exec {shlex.quote(sys.executable)} -c {shlex.quote(code)}",
            session_key=lane.entry.session_key)
        process.notify_on_complete = True
        return process


def watch(runner, lane, process):
    return asyncio.create_task(runner._run_process_watcher(dict(
        session_id=process.id, session_key=lane.entry.session_key,
        parent_session_id=lane.entry.session_id, check_interval=0.01,
        platform="telegram", chat_id="same-chat", notify_on_complete=True)))


@asynccontextmanager
async def rig(profile_tree, names, multiplex):
    root, registry = profile_tree
    launch_home = root if multiplex else get_profile_dir(names[0])
    tasks, processes, lanes = [], [], []
    with _profile_runtime_scope(launch_home):
        runner = GatewayRunner(GatewayConfig(
            sessions_dir=launch_home / "sessions", multiplex_profiles=multiplex))
    runner._running = True
    runner._gateway_loop = asyncio.get_running_loop()
    try:
        for name in names:
            home = get_profile_dir(name)
            with _profile_runtime_scope(home):
                assert get_active_profile_name() == name
                adapter = _QueuedTransport()
                adapter._running = True
                adapter.gateway_runner = runner
                adapter.set_owner_profile(name)
                adapter.set_message_handler(no_model)
                if name == runner._primary_profile_name:
                    runner.adapters[Platform.TELEGRAM] = adapter
                else:
                    runner._profile_adapters[name] = {Platform.TELEGRAM: adapter}
                source = adapter.build_source(chat_id="same-chat", chat_type="dm")
                adapter._canonicalize(source)
                entry = runner.session_store.get_or_create_session(source)
                adapter._active_sessions[entry.session_key] = asyncio.Event()
                agent = SimpleNamespace(session_id=entry.session_id, model="example",
                                        _current_turn_id="turn")
                with bind_subagent_parent(agent), scoped_current_session_id(entry.session_id):
                    uid = capture(agent)
                db = runner.session_store._db
                db.append_message(entry.session_id, "user", "Repair example app", message_uid=uid)
                lane = SimpleNamespace(name=name, home=home, adapter=adapter, entry=entry,
                                       agent=agent, db=db, uid=uid)
                lanes.append(lane)
                lane.process = spawn(registry, lane, "import threading; threading.Event().wait(60)")
                processes.append(lane.process)
                tasks.append(watch(runner, lane, lane.process))
        # Let all real native watcher contexts bind before any completion arrives.
        await asyncio.sleep(0)
        for lane in lanes:
            assert receipt_version(registry.ownership_snapshot(lane.process.id)) is None
            child = spawn(registry, lane, f"print('completed-{lane.name}')")
            processes.append(child)
            lane.completion_task = watch(runner, lane, child)
            tasks.append(lane.completion_task)
            lane.child = child
        await async_wait_until(
            lambda: all(receipt_version(registry.ownership_snapshot(lane.process.id))
                        is not None for lane in lanes), timeout=15)
        for lane in lanes:
            await asyncio.wait_for(lane.completion_task, timeout=5)
            assert lane.child.exited and lane.child.exit_code == 0
            queued = lane.adapter.get_pending_message(lane.entry.session_key)
            assert queued and queued.internal and f"completed-{lane.name}" in queued.text
            assert queued.metadata["gateway_session_id"] == lane.entry.session_id
            assert runner._resolve_profile_home_for_source(queued.source) == lane.home
            assert runner._resolve_injection_adapter("telegram", queued.source) is lane.adapter
        yield runner, registry, lanes
    finally:
        runner._running = False
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        for process in processes:
            process.notify_on_complete = False
            if process.process.poll() is None:
                process.process.kill()
            process.process.wait(timeout=5)
            assert process._completion_event.wait(5)
        runner.session_store.close_all_db_handles()
        runner.close_all_session_db_handles()


def validate(lane, *, process_id=None, profile=None, session_id=None):
    value = dict(goal="Repair example app", actionable=True, unfinished=True,
                 disposition="owned_continuing", evidence_refs=["candidate", "user:0"],
                 boundary_kind=None, confidence=.99,
                 continuation_ref=dict(gateway=socket.gethostname(),
                                       profile=profile or lane.name,
                                       process_id=process_id or lane.process.id))
    evidence = gd.evidence_for([dict(role="user", content=value["goal"])],
                               "The native process is continuing.")
    return gd.validate_disposition(value, evidence,
                                   session_id=session_id or lane.entry.session_id)


def assert_owned(registry, lane):
    with _profile_runtime_scope(lane.home):
        snapshot = registry.ownership_snapshot(lane.process.id)
        assert snapshot.home == str(lane.home.resolve())
        assert snapshot.message_uid == lane.uid
        assert snapshot.session_key == lane.entry.session_key
        assert lane.db._conn.execute("SELECT profile_name FROM sessions WHERE id=?",
                                     (lane.entry.session_id,)).fetchone()[0] == lane.name
        assert validate(lane)["disposition"] == "owned_continuing"


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["alpha", "main"])
async def test_named_launch_real_native_completion(profile_tree, name):
    async with rig(profile_tree, [name], False) as (_, registry, lanes):
        lane = lanes[0]
        assert_owned(registry, lane)
        # Real scope switch, not a continuation-field mutation on a default fixture.
        with _profile_runtime_scope(profile_tree[0]):
            assert get_active_profile_name() == "default"
            with pytest.raises(ValueError, match="owner_process_route_mismatch"):
                validate(lane)
        assert_owned(registry, lane)


@pytest.mark.asyncio
@pytest.mark.parametrize("loss", ["stop", "new-user", "adapter"])
async def test_simultaneous_profiles_isolate_current_scope_and_revocation(profile_tree, loss):
    async with rig(profile_tree, ["default", "alpha", "main"], True) as (runner, registry, lanes):
        assert len({lane.entry.session_key for lane in lanes}) == len(lanes)
        assert len({lane.db for lane in lanes}) == len(lanes)
        for lane in lanes:
            assert_owned(registry, lane)
            with _profile_runtime_scope(lane.home):
                for other in lanes:
                    if other is lane:
                        continue
                    with pytest.raises(ValueError, match="owner_process_route_mismatch"):
                        validate(other)
                    with pytest.raises(ValueError, match="owner_process_scope_mismatch"):
                        validate(lane, process_id=other.process.id)
                    assert lane.db._conn.execute("SELECT id FROM sessions WHERE id=?",
                                                 (other.entry.session_id,)).fetchone() is None
        stopped = lanes[1]
        # Boundary targets its native route even with a foreign ambient scope.
        with _profile_runtime_scope(lanes[2].home):
            if loss == "stop":
                runner._invalidate_session_run_generation(stopped.entry.session_key, reason="stop")
            elif loss == "new-user":
                from gateway.platforms.event import MessageEvent, MessageType
                await stopped.adapter.handle_message(MessageEvent(
                    text="new instruction", message_type=MessageType.TEXT,
                    source=stopped.entry.origin))
            else:
                stopped.adapter._running = False
        with _profile_runtime_scope(stopped.home):
            with pytest.raises(ValueError):
                validate(stopped)
        for lane in (lanes[0], lanes[2]):
            assert_owned(registry, lane)
        assert get_hermes_home() == profile_tree[0]


@pytest.mark.asyncio
async def test_concurrent_tasks_keep_their_current_profile_across_await(profile_tree):
    async with rig(profile_tree, ["default", "alpha", "beta"], True) as (_, registry, lanes):
        barrier = asyncio.Barrier(len(lanes))

        async def check(lane):
            with _profile_runtime_scope(lane.home):
                await asyncio.wait_for(barrier.wait(), timeout=5)
                # Do not re-enter the scope here: the suspended task must retain it.
                assert get_hermes_home() == lane.home
                assert get_active_profile_name() == lane.name
                assert validate(lane)["disposition"] == "owned_continuing"
                await asyncio.wait_for(barrier.wait(), timeout=5)
                for other in lanes:
                    if other is not lane:
                        with pytest.raises(ValueError, match="owner_process_route_mismatch"):
                            validate(other)
                assert receipt_version(registry.ownership_snapshot(lane.process.id)) is not None
        await asyncio.gather(*(check(lane) for lane in lanes))
        assert get_hermes_home() == profile_tree[0]
