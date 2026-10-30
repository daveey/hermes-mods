"""Ordinary prose and legacy asks use the installed reconciliation registry."""
import time

import pytest
from conftest import llm_returning

ASK = "Could you approve the merge of https://example.com/project/pull/123?"
TARGET = {"authority": "example", "subject_kind": "merge", "subject": "project#123", "revision": "a" * 40}


@pytest.mark.parametrize("surface", ["final", "legacy"])
@pytest.mark.parametrize("state", ["approved", "vetoed", "unavailable"])
def test_reconciled_prose_never_reaches_judge(gate, surface, state):
    seen = []
    def resolve(context):
        seen.append(context)
        return TARGET if "approve the merge" in context["human_request"]["action"] else None
    def read(target):
        return {"target": target, "state": state, "verified": True, "observed_at": time.time()}
    dispose = gate.approval_reconciliation.register("example", resolve, read)
    gate.LLM = llm_returning([("ok", "unneeded ask")])
    gate.LOG.add("s1", "terminal", "inspect", "ready")
    try:
        if surface == "final":
            result = gate.pre_final_gate(session_id="s1", final_response=ASK, hold_budget=0)
            assert result and "replace" in result
            assert ASK not in result["replace"]
        else:
            result = gate.pre_tool_call(session_id="s1", tool_name="kanban_block", args={
                "kind": "needs_input", "reason": ASK + "\nChecked: terminal inspect — ready"})
            assert result and result["action"] == "block"
        assert seen
        assert not gate.LLM.seen
    finally:
        dispose()


@pytest.mark.parametrize("howto", [False, True])
def test_reconciled_final_retires_pending_even_in_howto_context(gate, howto):
    gate.ledger().set_open_asks("s1", [ASK])
    if howto:
        gate.LOG.set_users("s1", ["How do I merge this pull request?"])
    dispose = gate.approval_reconciliation.register("example", lambda context: TARGET,
        lambda target: {"target": target, "state": "vetoed", "verified": True, "observed_at": time.time()})
    gate.LLM = llm_returning([("ok", "not authoritative")])
    try:
        assert gate.pre_final_gate(session_id="s1", final_response=ASK).get("hold")
        assert gate.ledger().open_asks("s1") == []
        assert "replace" in gate.pre_final_gate(session_id="s1", final_response=ASK, attempt=2)
        assert gate.ledger().open_asks("s1") == []
        assert not gate.LLM.seen
    finally:
        dispose()


@pytest.mark.parametrize("state", ["pending", "new_scope"])
def test_pending_prose_still_uses_normal_judge(gate, state):
    dispose = gate.approval_reconciliation.register("example", lambda context: TARGET,
        lambda target: {"target": target, "state": state, "verified": True, "observed_at": time.time()})
    gate.LLM = llm_returning([("ok", "decision is needed")])
    try:
        assert gate.pre_final_gate(session_id="s1", final_response=ASK) is None
        assert gate.LLM.seen
    finally:
        dispose()
