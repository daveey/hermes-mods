"""Native producer + actual consumer + canonical latest row at the public gate."""
import importlib.util
import shlex
import socket
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.tui_gateway.test_process_consumer_health import consumer
from tests.tools.test_process_ownership_snapshot import capture
from tests.waiting import wait_until
from agent.subagent_lifecycle import bind_subagent_parent
from gateway.session_context import scoped_current_session_id

spec = importlib.util.spec_from_file_location("process_adapter_goal", Path(__file__).parents[1] / "goal_disposition.py")
gd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gd)
REQUEST = "Repair example app"


@pytest.fixture
def owned(consumer, tmp_path, monkeypatch):
    from hermes_state import SessionDB
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr("hermes_cli.profiles.get_active_profile_name", lambda: "default")
    monkeypatch.setattr("hermes_cli.lifecycle.invoke_hook", lambda *a, **k: [])
    health, registry, session, _, _, complete = consumer
    complete()
    wait_until(lambda: health.available(tmp_path, "origin", registry.ownership_generation), timeout=5)
    agent = session["agent"]
    agent.model = "example"
    agent._current_turn_id = "native-turn"
    with bind_subagent_parent(agent), scoped_current_session_id("origin"):
        uid = capture(agent)
        p = registry.spawn_local(f"exec {shlex.quote(sys.executable)} -c 'import threading; threading.Event().wait(60)'",
                                 session_key="origin")
        p.notify_on_complete = True
    state = SessionDB(tmp_path / "state.db")
    state.create_session(session_id="origin", source="desktop")
    state._conn.execute("UPDATE sessions SET profile_name='default' WHERE id='origin'")
    state.append_message("origin", "user", REQUEST, message_uid=uid)
    state.close()
    v = dict(goal=REQUEST, actionable=True, unfinished=True, disposition="owned_continuing",
        evidence_refs=["candidate", "user:0"], boundary_kind=None, confidence=.99,
        continuation_ref=dict(gateway=socket.gethostname(), profile="default", process_id=p.id))
    evidence = gd.evidence_for([dict(role="user", content=REQUEST)], "The native process is continuing.")
    try:
        yield v, evidence, p, consumer
    finally:
        p.notify_on_complete = False
        p.process.kill()
        p.process.wait(timeout=5)
        p._completion_event.wait(5)


def test_native_process_adapter_positive(owned):
    v, evidence, *_ = owned
    assert gd.validate_disposition(v, evidence, session_id="origin")["disposition"] == "owned_continuing"


@pytest.mark.parametrize("sql", [
    "UPDATE messages SET message_uid='different'",
    "UPDATE messages SET content='Stop repairing'",
    "UPDATE messages SET observed=1",
    "UPDATE messages SET display_kind='hidden'",
    "UPDATE messages SET display_metadata='{\"_queued_prompt\":true}'",
    "UPDATE sessions SET profile_name='other'",
])
def test_latest_canonical_scope_must_match(owned, tmp_path, sql):
    with sqlite3.connect(tmp_path / "state.db") as conn:
        conn.execute(sql)
    with pytest.raises(ValueError):
        gd.validate_disposition(owned[0], owned[1], session_id="origin")


def test_public_gate_accepts_then_keeps_existing_hold_on_stopped_consumer(owned, tmp_path, monkeypatch):
    events = []
    monkeypatch.setattr(gd, "settings", lambda: {"mode": "enforce"})
    monkeypatch.setattr(gd, "aux_judge", lambda *a: owned[0])
    gate = gd.build_gate(SimpleNamespace(log=events.append))
    before = {p: p.read_bytes() for p in tmp_path.glob("*.db")}
    args = dict(session_id="origin", final_response="The native process is continuing.",
                turn_messages=[dict(role="user", content=REQUEST)])
    assert gate(**args) is None
    assert events[-1]["disposition"] == "owned_continuing"
    assert all(p.read_bytes() == content for p, content in before.items())
    owned[3][4].set()
    with pytest.raises(ValueError):
        gate(**args, attempt=1)
    assert gate(**args) is None
    assert events[-1]["event"] == "goal_disposition_unknown"


@pytest.mark.parametrize("key,value", [("gateway", "foreign"), ("profile", "other"),
    ("process_id", "proc_012345"), ("process_id", " proc_0123456789ab")])
def test_exact_route_required(owned, key, value):
    owned[0]["continuation_ref"][key] = value
    with pytest.raises(ValueError):
        gd.validate_disposition(owned[0], owned[1], session_id="origin")
