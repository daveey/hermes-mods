"""A refused ask is first held with a plain reason, then (budget spent) only the ask lines are dropped.

Regression: a reply whose one approval ask could not be reconciled was replaced whole with
"I withheld a request after checking authoritative approval or lifecycle state." The agent got no
chance to resend without the ask, and the user lost the findings and never learned why.
"""
import time

import pytest

ASK = "Please click Approve on the Hermes prompt to edit the merge queue."
FINDINGS = "I patched the merge-queue config and ran its tests: 12 passed."
BLOCKER = "The Hermes approval prompt for the write timed out after 60 seconds."
DRAFT = f"{FINDINGS}\n{BLOCKER}\n{ASK}"
TARGET = {"authority": "example", "subject_kind": "merge", "subject": "project#123", "revision": "a" * 40}


def _bare(text):
    return text.lstrip().startswith(("I withheld a", "The workflow remains stopped", "I suppressed a repeated"))


@pytest.fixture
def unreconcilable(gate):
    def resolve(context):
        if "merge queue" in context["human_request"]["action"]:
            raise RuntimeError("adapter could not parse")
        return None
    dispose = gate.approval_reconciliation.register("example", resolve, lambda t: None)
    yield gate
    dispose()


def test_unreconciled_ask_is_held_with_named_ask_and_reason(unreconcilable):
    gate = unreconcilable
    result = gate.pre_final_gate(session_id="s1", final_response=DRAFT, attempt=0)
    assert result and result.get("hold") is True and "replace" not in result
    nudge = result["nudge"]
    assert "edit the merge queue" in nudge          # names the refused ask
    assert "could not be reconciled" in nudge        # says why, in words
    assert "without" in nudge.lower()                # tells the agent to resend without it


def test_unreconciled_ask_after_budget_keeps_findings(unreconcilable):
    gate = unreconcilable
    result = gate.pre_final_gate(session_id="s1", final_response=DRAFT, attempt=2)
    assert result and "replace" in result
    text = result["replace"]
    assert FINDINGS in text and BLOCKER in text
    assert "edit the merge queue" not in text
    assert not _bare(text)
    assert "approval" in text.lower()               # one plain sentence on why


def test_zero_budget_replaces_at_once_but_keeps_findings(unreconcilable):
    result = unreconcilable.pre_final_gate(session_id="s1", final_response=DRAFT, hold_budget=0)
    assert result and FINDINGS in result["replace"] and "edit the merge queue" not in result["replace"]


def test_resent_draft_without_the_ask_is_delivered(unreconcilable, monkeypatch):
    gate = unreconcilable
    assert gate.pre_final_gate(session_id="s1", final_response=DRAFT, attempt=0).get("hold")
    resent = f"{FINDINGS}\n{BLOCKER} I will retry the write once the approval channel is back."
    assert gate.pre_final_gate(session_id="s1", final_response=resent, attempt=1) is None


def test_approved_scope_hold_says_it_is_already_approved(gate):
    ask = "Could you approve the merge of https://example.com/project/pull/123?"
    dispose = gate.approval_reconciliation.register("example", lambda c: TARGET,
        lambda t: {"target": t, "state": "approved", "verified": True, "observed_at": time.time()})
    try:
        held = gate.pre_final_gate(session_id="s1", final_response=f"{FINDINGS}\n{ask}", attempt=0)
        assert held.get("hold") and "already approved" in held["nudge"] and "pull/123" in held["nudge"]
        final = gate.pre_final_gate(session_id="s1", final_response=f"{FINDINGS}\n{ask}", attempt=2)
        assert FINDINGS in final["replace"] and "pull/123" not in final["replace"]
        assert not _bare(final["replace"])
    finally:
        dispose()


def test_stopped_workflow_holds_then_keeps_findings(gate):
    ask = "Could you approve the Example deployment?"
    gate.pre_llm_call(session_id="origin", user_message="Stop this workflow.")
    held = gate.pre_final_gate(session_id="origin", final_response=f"{FINDINGS}\n{ask}", attempt=0)
    assert held.get("hold") and "stopped" in held["nudge"] and "Example deployment" in held["nudge"]
    final = gate.pre_final_gate(session_id="origin", final_response=f"{FINDINGS}\n{ask}", attempt=2)
    assert FINDINGS in final["replace"] and "Example deployment" not in final["replace"]
    assert not _bare(final["replace"])


def test_retired_ask_holds_then_keeps_findings(gate):
    ask = "Could you approve the Example deployment?"
    gate.ledger().add(ask, "The step was completed.", session="origin")
    held = gate.pre_final_gate(session_id="origin", final_response=f"{FINDINGS}\n{ask}", attempt=0)
    assert held.get("hold") and "already" in held["nudge"] and "Example deployment" in held["nudge"]
    final = gate.pre_final_gate(session_id="origin", final_response=f"{FINDINGS}\n{ask}", attempt=2)
    assert FINDINGS in final["replace"] and "Example deployment" not in final["replace"]
    assert not _bare(final["replace"])


def test_ask_only_draft_still_never_leaks_the_ask(unreconcilable):
    result = unreconcilable.pre_final_gate(session_id="s1", final_response=ASK, attempt=2)
    assert result["replace"].strip() and "edit the merge queue" not in result["replace"]


def test_judge_rejected_ask_after_budget_keeps_findings(gate):
    from conftest import llm_returning
    gate.LLM = llm_returning([("unverified", "unchecked premise", "run the check")])
    ask = "Can you confirm the token was revoked?"
    result = gate.pre_final_gate(session_id="s1", platform="tui", final_response=f"{FINDINGS}\n{ask}", attempt=2)
    assert FINDINGS in result["replace"] and ask not in result["replace"]
    assert not _bare(result["replace"]) and "current-state check" in result["replace"]
