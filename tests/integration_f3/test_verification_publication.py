"""Verification continuations must not publish before source reconciliation."""
import pytest

from test_final_publication import ASK, Model, wire_models


@pytest.mark.parametrize("gate_name", ["_verify_on_stop_nudge", "_pre_verify_nudge"])
@pytest.mark.parametrize("exhausted", [False, True])
def test_retired_candidate_stays_private_during_verification(harness, monkeypatch, gate_name, exhausted):
    h = harness
    events = []
    cards = wire_models(h, monkeypatch, events)
    Model(h.agent, monkeypatch, [ASK])
    h.agent.run_conversation("Pick a case with me.")
    ledger = h.steps.core.Ledger()
    root = ledger.root(h.agent.session_id)
    step = ledger.open_steps(h.agent.session_id)[0]
    h.steps.core.resolve(ledger, root, [step["id"]], "done", "test")
    cards.calls.clear()
    events.clear()
    nudges = iter(["Verify the result before finishing.", None])
    monkeypatch.setattr("agent.turn_stop_gates." + gate_name, lambda *args: next(nudges, None))
    if exhausted:
        h.agent.max_iterations = 1
    safe = "The previous choice remains completed."
    model = Model(h.agent, monkeypatch, [ASK, safe])
    result = h.agent.run_conversation("Report the current status.")
    assert ASK not in model.shown
    assert not model.interims
    # Earlier seed is legitimate history; this turn must not append the stale ask.
    current = result["messages"]
    user_index = next(i for i, row in enumerate(current) if row.get("content") == "Report the current status.")
    assert ASK not in str(current[user_index:])
    stored = h.db.get_messages(h.agent.session_id)
    user_index = next(i for i, row in enumerate(stored) if row.get("content") == "Report the current status.")
    assert ASK not in str(stored[user_index:])
    assert cards.calls == []
    assert ledger.open_steps(h.agent.session_id) == []
    assert result["final_response"] == (h.steps.core.HELD_REPLY if exhausted else safe)
    assert current[-1]["content"] == stored[-1]["content"] == result["final_response"]
    # Budget fallback is delivered through the returned final, not a delta.
    assert model.shown == ("" if exhausted else result["final_response"])
