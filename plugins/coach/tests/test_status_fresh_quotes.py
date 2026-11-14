"""Quoted historical status is not a fresh claim, even on an armed status turn."""
import pytest

from test_status_fresh import ASK, _events, _hooks, _turn, rc


CLAIM = "The card is blocked on review."
QUOTED = 'The earlier reply said "The card is blocked on review."'


@pytest.mark.parametrize("draft", [QUOTED, "The earlier reply said “The card is blocked on review.”"])
def test_quoted_status_is_not_a_current_claim(draft):
    assert rc.status_fresh_claim(draft) is None


def test_quote_does_not_hide_a_later_unquoted_claim():
    draft = QUOTED + "\nPR #123 is merged."
    assert rc.status_fresh_claim(draft) == rc.status_fresh_claim("PR #123 is merged.")
    assert rc.status_fresh_claim(draft) is not None


def test_quoted_claim_passes_armed_gate_without_consuming_hold(tmp_path, monkeypatch):
    monkeypatch.setenv("COACH_STATUS_FRESH", "on")
    hooks, log = _hooks(tmp_path, monkeypatch)
    gate = hooks["pre_final_gate:status_fresh"]
    session = "quoted-status"
    hooks["pre_llm_call"](session_id=session, user_message=ASK)
    assert gate.needs_buffer_for_session(session_id=session) is True

    # No tool result or prior hold can accidentally make this negative case pass.
    messages = _turn(ASK, {"role": "assistant", "content": QUOTED})
    assert rc.turn_tool_calls(messages) == 0
    assert gate(session_id=session, platform="desktop", final_response=QUOTED,
                turn_messages=messages, attempt=0) is None
    assert _events(log, "status_fresh") == []

    # Same armed turn, still zero tools: removing the quotation must hold. This
    # also detects a disabled/fail-open gate and a quote consuming the hold slot.
    messages = _turn(ASK, {"role": "assistant", "content": CLAIM})
    assert rc.turn_tool_calls(messages) == 0
    result = gate(session_id=session, platform="desktop", final_response=CLAIM,
                  turn_messages=messages, attempt=0)
    assert result and result["hold"] is True
    assert "status_fresh" in result["nudge"]
    events = _events(log, "status_fresh")
    assert len(events) == 1
    assert events[0]["action"] == "hold"
    assert events[0]["session_id"] == session
    assert events[0]["tools"] == 0
    assert events[0]["claim"] == rc.status_fresh_claim(CLAIM)
