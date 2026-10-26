"""Native whole-turn steering and real compaction, scripted external boundaries."""
import copy
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from hermes_state import SessionDB
from test_goal_proofs import gd, verdict
from tests.agent.test_pre_final_gate import Run, _response

pytest_plugins = ["tests.agent.test_pre_final_gate"]
TARGET = "https://example.com/health"
STEER = "Verify " + TARGET + " again after my deployment. Do not publish."


@pytest.mark.parametrize("compact", ["none", "in_place", "rotation"])
@pytest.mark.parametrize("fresh", [False, True])
def test_whole_turn_steering_compaction(agent, tmp_path, monkeypatch, compact, fresh):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(gd, "settings", lambda: {"mode": "enforce"})
    db = SessionDB(tmp_path / "state.db")
    agent._session_db = db
    agent._ensure_db_session()
    db.set_session_title(agent.session_id, "Steering retention test")
    monkeypatch.setattr("agent.conversation_compression._refresh_agent_tool_definitions", lambda _: False)
    original_sid = agent.session_id
    events, evidence, compressions = [], [], []
    gate = gd.build_gate(SimpleNamespace(log=events.append))
    agent.valid_tool_names = {"fetch"}
    agent.tools = [{"type": "function", "function": {"name": "fetch", "description": "Read URL", "parameters": {"type": "object", "properties": {"url": {"type": "string"}}}}}]
    run = Run(agent, [])
    calls = []

    def tool(name, args, *a, **kw):
        calls.append(name)
        if len(calls) == 1:
            assert agent.steer(STEER)
        return json.dumps({"url": TARGET, "status": 200, "padding": "x" * 4000})

    def response(kw):
        run.requests.append(copy.deepcopy(kw))
        n = len(run.requests)
        if n == 1 or (fresh and n == 3):
            cid = "old" if n == 1 else "new"
            tc = SimpleNamespace(id=cid, type="function", function=SimpleNamespace(name="fetch", arguments=json.dumps({"url": TARGET})))
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=None, tool_calls=[tc]), finish_reason="tool_calls")], model="test/model", usage=None)
        return _response("Still broken" if n == 2 else TARGET + " repaired")

    agent._interruptible_api_call = response
    agent._interruptible_streaming_api_call = lambda kw, on_first_delta=None: response(kw)
    judged = []
    def judge(system, prompt):
        ev = json.loads(prompt)["evidence"]
        evidence.append(ev)
        judged.append(1)
        if len(judged) == 1:
            return verdict(disposition="abandoned", unfinished=True, boundary_kind=None, evidence_refs=["user:0", "candidate"])
        source = "tool:new" if fresh else "tool:old"
        return verdict(disposition="remediated_verified", unfinished=False, boundary_kind=None,
                       evidence_refs=["user:0", source, "candidate"],
                       proof={"target": TARGET, "source_ref": source, "scope_ref": "user:0"})
    monkeypatch.setattr(gd, "aux_judge", judge)

    # Compact immediately after the first confirmed hold, using the native
    # compaction transaction. Only the summarizer is scripted, not persistence.
    def checked_gate(**kw):
        result = gate(**kw)
        if kw["attempt"] == 0 and compact != "none":
            rows = agent._session_messages
            agent.compression_in_place = compact == "in_place"
            def fold(messages, **kwargs):
                # Retain the original request but omit mid-turn steering.
                return [copy.deepcopy(messages[0]), {"role": "assistant", "content": "Earlier tools summarized."}]
            with patch.object(agent.context_compressor, "compress", side_effect=fold):
                out, _ = agent._compress_context(rows, agent._cached_system_prompt, force=True)
            compressions.append(agent._last_compression_attempt_in_place)
            rows[:] = out
        return result
    checked_gate.needs_buffer = True
    checked_gate.assess_before_holds = True
    def invoke(name, **kw):
        if name == "pre_llm_call":
            gate.capture(**kw)
        elif name == "post_tool_call":
            gate.observe(**kw)
        return []
    try:
        with patch("hermes_cli.lifecycle.has_hook", side_effect=lambda name: name in {"pre_final_gate", "pre_llm_call", "post_tool_call"}), patch("hermes_cli.plugins.iter_hook_callbacks", side_effect=lambda name: (checked_gate,) if name == "pre_final_gate" else ()), patch("hermes_cli.plugins.invoke_hook", side_effect=invoke), patch("model_tools.handle_function_call", side_effect=tool):
            result = agent.run_conversation("Repair " + TARGET)
        assert len(calls) == (2 if fresh else 1), (result, events, calls)
        assert any(STEER in str(e) for e in evidence), evidence
        if compact != "none":
            durable_steers = [r for r in db.get_messages(original_sid, include_inactive=True) if r.get("display_kind") == "steer"]
            assert len(durable_steers) == 1 and STEER in durable_steers[0]["content"], durable_steers
            assert any(e.get("reason") == "scope_unknown_steering" for e in events), events
            assert compressions == [compact == "in_place"], compressions
            assert (agent.session_id == original_sid) is (compact == "in_place")
        if fresh and compact == "none":
            assert result["completed"] and result["final_response"] == TARGET + " repaired", (result, events)
        else:
            assert not result["completed"] and "Unresolved goal" in result["final_response"], (result, events, evidence)
    finally:
        db.close()


@pytest.mark.parametrize("rotation", [False, True])
def test_historical_steering_is_unknown_not_silently_omitted(tmp_path, monkeypatch, rotation):
    from agent.prompt_builder import steer_user_row
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr("hermes_cli.profiles.get_active_profile_name", lambda: "review")
    db = SessionDB(tmp_path / "state.db")
    try:
        db.create_session(session_id="parent", source="cli", profile_name="review")
        row = steer_user_row("Do not publish; analysis only.")
        db.append_message("parent", "user", row["content"], display_kind="steer", message_uid=row["message_uid"])
        sid = "parent"
        if rotation:
            db.end_session(sid, "compression")
            sid = "child"
            db.create_session(session_id=sid, source="cli", profile_name="review", parent_session_id="parent")
        db.append_message(sid, "user", "Continue")
        # SessionDB intentionally does not persist process-local attestations.
        with pytest.raises(ValueError, match="steer"):
            gd.recover_scope(sid, "Continue", [])
    finally:
        db.close()


@pytest.mark.parametrize("compact", ["in_place", "rotation"])
def test_native_post_tool_compaction_cannot_invent_scope(agent, tmp_path, monkeypatch, compact):
    fresh = False
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(gd, "settings", lambda: {"mode": "enforce"})
    db = SessionDB(tmp_path / "state.db")
    agent._session_db = db
    agent._ensure_db_session()
    db.set_session_title(agent.session_id, "Steering retention test")
    monkeypatch.setattr("agent.conversation_compression._refresh_agent_tool_definitions", lambda _: False)
    original_sid = agent.session_id
    events, evidence, compressions = [], [], []
    gate = gd.build_gate(SimpleNamespace(log=events.append))
    agent.valid_tool_names = {"fetch"}
    agent.tools = [{"type": "function", "function": {"name": "fetch", "description": "Read URL", "parameters": {"type": "object", "properties": {"url": {"type": "string"}}}}}]
    run = Run(agent, [])
    calls = []

    def tool(name, args, *a, **kw):
        calls.append(name)
        if len(calls) == 1:
            assert agent.steer(STEER)
        return json.dumps({"url": TARGET, "status": 200, "padding": "x" * 4000})

    def response(kw):
        run.requests.append(copy.deepcopy(kw))
        n = len(run.requests)
        if n == 1 or (fresh and n == 3):
            cid = "old" if n == 1 else "new"
            tc = SimpleNamespace(id=cid, type="function", function=SimpleNamespace(name="fetch", arguments=json.dumps({"url": TARGET})))
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=None, tool_calls=[tc]), finish_reason="tool_calls")], model="test/model", usage=None)
        return _response("Still broken" if n == 2 else TARGET + " repaired")

    agent._interruptible_api_call = response
    agent._interruptible_streaming_api_call = lambda kw, on_first_delta=None: response(kw)
    judged = []
    def judge(system, prompt):
        ev = json.loads(prompt)["evidence"]
        evidence.append(ev)
        judged.append(1)
        if len(judged) == 1:
            return verdict(disposition="abandoned", unfinished=True, boundary_kind=None, evidence_refs=["user:0", "candidate"])
        source = "tool:new" if fresh else "tool:old"
        return verdict(disposition="remediated_verified", unfinished=False, boundary_kind=None,
                       evidence_refs=["user:0", source, "candidate"],
                       proof={"target": TARGET, "source_ref": source, "scope_ref": "user:0"})
    monkeypatch.setattr(gd, "aux_judge", judge)

    agent.compression_enabled = True
    agent.compression_in_place = compact == "in_place"
    def should_compress(tokens):
        return bool(calls) and not compressions
    def fold(messages, **kw):
        compressions.append(compact == "in_place")
        return [copy.deepcopy(messages[0]), {"role": "assistant", "content": "Earlier tools summarized."}]
    monkeypatch.setattr(agent.context_compressor, "should_compress", should_compress)
    monkeypatch.setattr(agent.context_compressor, "compress", fold)
    checked_gate = gate
    def invoke(name, **kw):
        if name == "pre_llm_call":
            gate.capture(**kw)
        elif name == "post_tool_call":
            gate.observe(**kw)
        return []
    try:
        with patch("hermes_cli.lifecycle.has_hook", side_effect=lambda name: name in {"pre_final_gate", "pre_llm_call", "post_tool_call"}), patch("hermes_cli.plugins.iter_hook_callbacks", side_effect=lambda name: (checked_gate,) if name == "pre_final_gate" else ()), patch("hermes_cli.plugins.invoke_hook", side_effect=invoke), patch("model_tools.handle_function_call", side_effect=tool):
            result = agent.run_conversation("Repair " + TARGET)
        assert len(calls) == 1 and len(run.requests) == 2, (result, events)
        assert not evidence, evidence
        assert compressions == [compact == "in_place"], compressions
        assert (agent.session_id == original_sid) is (compact == "in_place")
        saved = [r for r in db.get_messages(original_sid, include_inactive=True) if r.get("display_kind") == "steer"]
        assert len(saved) == 1 and STEER in saved[0]["content"]
        assert any(e.get("reason") == "scope_unknown_steering" for e in events), events
        assert not any(e.get("disposition") == "abandoned" for e in events), events
    finally:
        db.close()


def test_cold_history_without_database_does_not_erase_steer(tmp_path, monkeypatch):
    from agent.prompt_builder import steer_user_row
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    row = steer_user_row("Analysis only; do not publish")
    row.pop("_native_steer")  # native storage/wire roundtrip, no historical attestation
    with pytest.raises(ValueError, match="steer"):
        gd.recover_scope("cold", "Continue", [row])
