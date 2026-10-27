"""Delivery must not reuse a stale capability verdict or emit rejected asks."""
import pytest

ASK = "Could you update the Example DNS record?"


def test_same_draft_rechecks_capabilities_before_render(gate, monkeypatch):
    caps = {"text": "No relevant scope"}
    seen = []
    def capability_sheet(**kw):
        assert kw.get("force") is True
        return caps["text"]
    monkeypatch.setattr(gate.core, "capability_sheet", capability_sheet)
    def judge(asks, **kw):
        seen.append(kw["caps"])
        verdict = "agent_can_do" if "example-dns" in kw["caps"] else "ok"
        return [{"i": 0, "verdict": verdict, "reason": "scope discovery", "how": "token-broker exec example-dns"}]
    monkeypatch.setattr(gate.judge_mod, "judge", judge)
    assert gate.pre_final_gate(session_id="origin", final_response=ASK) is None
    caps["text"] = "example-dns scope available"
    result = gate.pre_final_gate(session_id="origin", final_response=ASK, attempt=100)
    assert seen == ["No relevant scope", "example-dns scope available"]
    assert result and result.get("replace")
    assert ASK not in result["replace"]


@pytest.mark.parametrize("verdict", ["agent_can_do", "already_answered", "already_done", "unverified"])
def test_rejected_draft_never_delivered_when_budget_exhausted(gate, monkeypatch, verdict):
    monkeypatch.setattr(gate, "run_judge", lambda *a: [
        {"i": 0, "verdict": verdict, "reason": "sanitized evidence", "how": "read exact current status"}])
    result = gate.pre_final_gate(session_id="origin", final_response=ASK, attempt=100)
    assert result and result.get("replace")
    assert not result.get("append")
    assert ASK not in result["replace"]
    assert gate.ledger().open_asks("origin") == []


def test_updated_evidence_can_release_same_draft(gate, monkeypatch):
    state = {"verdict": "unverified"}
    monkeypatch.setattr(gate, "run_judge", lambda *a: [
        {"i": 0, "verdict": state["verdict"], "reason": "check state", "how": "read status"}])
    assert gate.pre_final_gate(session_id="origin", final_response=ASK)["hold"]
    state["verdict"] = "ok"
    assert gate.pre_final_gate(session_id="origin", final_response=ASK, attempt=1) is None
    assert gate.ledger().open_asks("origin") == [ASK]


def test_rejection_survives_pending_store_failure(gate, monkeypatch):
    monkeypatch.setattr(gate, "run_judge", lambda *a: [
        {"i": 0, "verdict": "unverified", "reason": "unchecked", "how": "check status"}])
    def unavailable(*args):
        raise OSError("store unavailable")
    monkeypatch.setattr(gate.ledger(), "set_open_asks", unavailable)
    result = gate.pre_final_gate(session_id="origin", final_response=ASK, attempt=gate.MAX_NUDGES)
    assert result and result.get("replace")


def test_mixed_withheld_draft_clears_previous_pending(gate, monkeypatch):
    gate.ledger().set_open_asks("origin", ["Could you approve the old action?"])
    monkeypatch.setattr(gate, "run_judge", lambda *a: [
        {"i": 0, "verdict": "unverified", "reason": "unchecked", "how": "check status"},
        {"i": 1, "verdict": "ok", "reason": "valid decision", "how": ""}])
    result = gate.pre_final_gate(session_id="origin", final_response=ASK + "\nCould you choose the color?",
                                 attempt=gate.MAX_NUDGES)
    assert result and result.get("replace")
    assert gate.ledger().pending_snapshot("origin") == []
