"""Retirement publication must not depend on ask-gate being enabled."""
import json

import pytest

from test_final_publication import ASK, Cards, Model, extraction, requests, retire


@pytest.mark.parametrize("harness", [["user-steps"]], indirect=True)
@pytest.mark.parametrize("verification", [False, True])
@pytest.mark.parametrize("framed", [False, True])
def test_standalone_retirement_never_streams(harness, monkeypatch, verification, framed):
    h = harness
    assert h.ask is None
    monkeypatch.setenv("HERMES_USER_STEPS_FRAME", "1" if framed else "0")
    cards = Cards()
    monkeypatch.setattr(h.steps.cards_mod, "HermesCards", lambda: cards)

    def extract(system, prompt):
        reply = prompt.split("ASSISTANT REPLY:\n", 1)[1]
        return json.dumps({"steps": [extraction()] if ASK in reply else [], "resolved": []})

    monkeypatch.setattr(h.steps, "aux_llm", extract)
    Model(h.agent, monkeypatch, [ASK])
    h.agent.run_conversation("Pick a case with me.")
    ledger = h.steps.core.Ledger()
    step = ledger.open_steps(h.agent.session_id)[0]
    retire(h, step)
    canonical_before = requests(h)
    cards.calls.clear()
    if verification:
        nudges = iter(["Verify before finishing.", None])
        monkeypatch.setattr("agent.turn_stop_gates._verify_on_stop_nudge", lambda *a: next(nudges, None))
    model = Model(h.agent, monkeypatch, [ASK, ASK])
    result = h.agent.run_conversation("Report current status.")
    assert len(model.requests) == (2 if verification else 1)
    assert result["final_response"] == h.steps.core.HELD_REPLY
    assert model.shown == h.steps.core.HELD_REPLY
    assert not model.interims
    assert not cards.calls
    assert not ledger.open_steps(h.agent.session_id)
    assert requests(h) == canonical_before
    for rows in (result["messages"], h.db.get_messages(h.agent.session_id)):
        start = next(i for i, row in enumerate(rows) if row.get("content") == "Report current status.")
        assert ASK not in str(rows[start:])
        assert rows[-1]["content"] == h.steps.core.HELD_REPLY
