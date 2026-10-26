"""Independent whole-change acceptance probes; isolated, no live services."""
import json
import sqlite3
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch
import pytest
from test_goal_ownership import owner, certify
from goal_consumer_fixture import consumer
from test_goal_proofs import gd, verdict, checked
from tests.agent.test_pre_final_gate import Run
pytest_plugins = ["tests.agent.test_pre_final_gate"]


def test_queued_receipt_cannot_certify_current_kanban_owner(owner, consumer):
    assert certify(owner)["disposition"] == "owned_continuing"
    with sqlite3.connect(owner[0].parent / "state.db") as conn:
        conn.execute("UPDATE messages SET display_metadata=? WHERE role='user'", (json.dumps({"_queued_prompt": True}),))
    with pytest.raises(ValueError):
        certify(owner)


@pytest.mark.parametrize("new_scope", [False, True])
def test_remediation_must_follow_latest_material_scope(new_scope):
    target = "https://example.com/health"
    rows = [dict(role="user", content="Repair " + target),
        dict(role="assistant", tool_calls=[dict(id="c", function=dict(name="fetch", arguments=json.dumps({"url": target})))]),
        dict(role="tool", tool_call_id="c", content={"url": target, "status": 200})]
    if new_scope:
        rows.append(dict(role="user", content="That check predates my latest deployment. Verify " + target + " again now before closing."))
    evidence = gd.evidence_for(rows, target + " is repaired and verified.")
    v = verdict(disposition="remediated_verified", unfinished=False, boundary_kind=None,
                proof={"target": target, "source_ref": "tool:c", "scope_ref": "user:0"})
    if new_scope:
        with pytest.raises(ValueError):
            checked(v, evidence)
    else:
        assert checked(v, evidence)["disposition"] == "remediated_verified"


@pytest.mark.parametrize("change", ["unchanged", "malformed", "missing", "unreadable", "invalid", "off", "shadow", "stop"])
def test_pending_hold_survives_unreadable_configuration(agent, tmp_path, monkeypatch, change):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    directory = tmp_path / "coach"
    directory.mkdir(exist_ok=True)
    config = directory / "goal-disposition.json"
    config.write_text('{"mode":"enforce"}')
    events = []
    gate = gd.build_gate(SimpleNamespace(log=events.append))
    monkeypatch.setattr(gd, "aux_judge", lambda *_: dict(goal="Repair example", actionable=True,
        unfinished=True, disposition="abandoned", evidence_refs=["user:0", "candidate"],
        boundary_kind=None, continuation_ref=None, confidence=.99))
    diagnosis = "The requested repair remains broken; prerequisite deferred."
    run = Run(agent, [diagnosis] * 3)
    original = gate
    def changing_gate(**kw):
        result = original(**kw)
        if kw["attempt"] == 0:
            if change == "malformed":
                config.write_text('{')
            elif change == "missing":
                config.unlink()
            elif change == "unreadable":
                read_text = Path.read_text
                def guarded_read(path, *args, **kwargs):
                    if path == config:
                        raise PermissionError("test config unavailable")
                    return read_text(path, *args, **kwargs)
                monkeypatch.setattr(Path, "read_text", guarded_read)
            elif change in ("invalid", "off", "shadow"):
                config.write_text(json.dumps({"mode": change}))
            elif change == "stop":
                config.unlink()
                agent.interrupt("user pressed stop")
        return result
    changing_gate.needs_buffer = True
    changing_gate.assess_before_holds = True
    with patch("hermes_cli.lifecycle.has_hook", side_effect=lambda name: name == "pre_final_gate"), \
         patch("hermes_cli.plugins.iter_hook_callbacks", side_effect=lambda name: (changing_gate,) if name == "pre_final_gate" else ()), \
         patch("hermes_cli.plugins.invoke_hook", return_value=[]):
        result = agent.run_conversation("Repair example")
    if change == "stop":
        assert result["interrupted"] and not result["completed"]
        assert "Unresolved goal" not in (result["final_response"] or "")
        assert diagnosis not in run.shown
        assert len(run.requests) == 1
        return
    assert "Unresolved goal" in result["final_response"], (result["final_response"], result["completed"], run.shown, events)
    assert diagnosis not in run.shown
    assert not result["completed"]


@pytest.mark.parametrize("mode", [None, "off", "shadow", "invalid"])
def test_fresh_non_enforcing_configuration_does_not_hold(tmp_path, monkeypatch, mode):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    if mode is not None:
        (tmp_path / "coach").mkdir()
        (tmp_path / "coach" / "goal-disposition.json").write_text(json.dumps({"mode": mode}))
    monkeypatch.setattr(gd, "aux_judge", lambda *_: dict(goal="Repair example", actionable=True,
        unfinished=True, disposition="abandoned", evidence_refs=["user:0", "candidate"],
        boundary_kind=None, continuation_ref=None, confidence=.99))
    gate = gd.build_gate(SimpleNamespace(log=lambda _: None))
    assert not gate.needs_buffer
    assert gate(turn_messages=[dict(role="user", content="Repair example")], final_response="Still broken") is None


@pytest.mark.parametrize("fresh", [False, True])
def test_post_steering_remediation_recheck_binding(tmp_path, monkeypatch, fresh):
    from agent.pre_final_gate import evaluate, UnresolvedFallback
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(gd, "settings", lambda: {"mode": "enforce"})
    target = "https://example.com/health"
    rows = [dict(role="user", content="Repair " + target),
        dict(role="assistant", tool_calls=[dict(id="c", function=dict(name="fetch", arguments={"url": target}))]),
        dict(role="tool", tool_call_id="c", content={"url": target, "status": 200}),
        dict(role="user", content="Verify " + target + " again after my deployment.")]
    if fresh:
        rows += [dict(role="assistant", tool_calls=[dict(id="d", function=dict(name="fetch", arguments={"url": target}))]),
                 dict(role="tool", tool_call_id="d", content={"url": target, "status": 200})]
    source = "tool:d" if fresh else "tool:c"
    v = verdict(disposition="remediated_verified", unfinished=False, boundary_kind=None,
        evidence_refs=["user:1", source, "candidate"],
        proof={"target": target, "source_ref": source, "scope_ref": "user:1"})
    monkeypatch.setattr(gd, "aux_judge", lambda *_: v)
    gate = gd.build_gate(SimpleNamespace(log=lambda _: None))
    gate.capture(session_id="review-recheck", user_message=rows[3]["content"],
                 conversation_history=rows[:3])
    monkeypatch.setattr("agent.pre_final_gate.eligible_callbacks", lambda agent: [gate])
    pending = UnresolvedFallback("Repair app", ("user:0", "candidate"), recheck_callback=gate)
    decision = evaluate(SimpleNamespace(session_id="review-recheck", platform="cli", model="test"),
                        target + " repaired", rows, 1, pending)
    assert decision.recheck_accepted is fresh
    if not fresh:
        assert decision.unresolved_fallback is not None


def test_new_canonical_user_after_queue_is_not_rejected(owner, consumer):
    from hermes_state import SessionDB
    from test_goal_ownership import SID, REQUEST
    db = SessionDB(owner[0].parent / "state.db")
    try:
        db.append_message(SID, "user", REQUEST, display_metadata={"_queued_prompt": True})
        with pytest.raises(ValueError):
            certify(owner)
        db.append_message(SID, "user", REQUEST)
        assert certify(owner)["disposition"] == "owned_continuing"
    finally:
        db.close()
