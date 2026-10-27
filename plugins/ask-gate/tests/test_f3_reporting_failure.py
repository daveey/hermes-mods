"""Positive rejection must survive unrelated verdict-cache corruption."""
import pytest

ASK = "Could you approve the Example deployment?"


@pytest.mark.parametrize("reason", ["workflow", "retired", "authority", "judge"])
@pytest.mark.parametrize("cache", ['[]', '{"old":{"t":null}}'])
def test_rejection_survives_corrupt_reporting_cache(gate, monkeypatch, reason, cache):
    if reason == "workflow":
        gate.pre_llm_call(session_id="origin", user_message="Stop this workflow.")
    elif reason == "retired":
        gate.ledger().add(ASK, "The step was completed.", session="origin")
    elif reason == "authority":
        monkeypatch.setattr(gate.lifecycle, "preflight", lambda _: {"action": "block"})
    else:
        monkeypatch.setattr(gate, "run_judge", lambda *args: [
            {"i": 0, "verdict": "unverified", "reason": "Missing evidence"}])
    path = gate._verdict_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(cache)
    result = gate.pre_final_gate(session_id="origin", final_response=ASK, attempt=100)
    assert result and result.get("replace")
    assert ASK not in result["replace"]


def test_unrelated_reporting_failure_does_not_invent_rejection(gate, monkeypatch):
    monkeypatch.setattr(gate, "run_judge", lambda *args: [
        {"i": 0, "verdict": "ok", "reason": "Verified"}])
    path = gate._verdict_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('[]')
    assert gate.pre_final_gate(session_id="origin", final_response=ASK) is None
