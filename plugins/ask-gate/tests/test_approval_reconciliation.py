"""Approval-store reads precede judging; fixtures are not real approvals."""
import time

import pytest
from conftest import llm_returning


TARGET = {"authority": "example-store", "subject_kind": "merge_pr",
          "subject": "example/project#123", "revision": "a" * 40}


def request():
    return {"action": "Approve the merge", "why_human": "Security review requires a user decision",
            "scope": "example/project#123", "evidence": "terminal inspect returned head " + "a" * 40,
            "interaction": "approval"}


def snapshot(state="approved", **overrides):
    return {"target": dict(TARGET), "state": state, "verified": True,
            "observed_at": time.time(), **overrides}


def setup(gate, reader):
    gate.LOG.add("s1", "terminal", "inspect", "head " + "a" * 40)
    gate.LLM = llm_returning([("ok", "judge believes approval is needed")])
    return gate.approval_reconciliation.register(
        "example-store", lambda context: dict(TARGET), reader)


def call(gate, **changes):
    return gate.pre_tool_call(tool_name="kanban_block", session_id="s1", args={
        "kind": "needs_input", "reason": "Needs approval", "human_request": {**request(), **changes}})


def test_approved_exact_scope_blocks_repeat_ask_before_judge(gate):
    dispose = setup(gate, lambda target: snapshot())
    try:
        result = call(gate)
        assert result["action"] == "block"
        assert "already approved" in result["message"]
        assert "not execution authorization" in result["message"]
        assert not gate.LLM.seen
    finally:
        dispose()


@pytest.mark.parametrize("state", ["pending", "new_scope"])
def test_verified_pending_or_new_scope_keeps_actual_approval_gate(gate, state):
    dispose = setup(gate, lambda target: snapshot(state))
    try:
        assert call(gate) is None
        assert len(gate.LLM.seen) == 1
    finally:
        dispose()


@pytest.mark.parametrize("change", [
    {"target": {**TARGET, "revision": "b" * 40}},
    {"target": {**TARGET, "subject": "example/project#124"}},
    {"target": {**TARGET, "authority": "foreign-store"}},
    {"verified": False}, {"verified": "true"},
    {"observed_at": 0}, {"observed_at": float("nan")},
    {"observed_at": time.time() + 1000},
    {"state": "expired"}, {"state": "unavailable"},
])
def test_stale_mismatched_or_unverified_is_recovery_not_permission(gate, change):
    dispose = setup(gate, lambda target: snapshot(**change))
    try:
        result = call(gate)
        assert result["action"] == "block"
        assert "already approved" not in result["message"]
        assert "operational recovery" in result["message"]
        assert not gate.LLM.seen
    finally:
        dispose()


def test_veto_is_not_a_repeat_approval_prompt(gate):
    dispose = setup(gate, lambda target: snapshot("vetoed"))
    try:
        assert "denied" in call(gate)["message"]
        assert not gate.LLM.seen
    finally:
        dispose()


def test_store_failure_does_not_become_approval_ask(gate):
    def unavailable(target):
        raise OSError("store offline")
    dispose = setup(gate, unavailable)
    try:
        assert "operational recovery" in call(gate)["message"]
        assert not gate.LLM.seen
    finally:
        dispose()


def test_current_store_reread_on_each_attempt(gate):
    states = iter(["pending", "approved", "vetoed"])
    dispose = setup(gate, lambda target: snapshot(next(states)))
    try:
        assert call(gate) is None
        assert "already approved" in call(gate)["message"]
        assert "denied" in call(gate)["message"]
    finally:
        dispose()


def test_agent_claim_does_not_override_authoritative_pending(gate):
    dispose = setup(gate, lambda target: snapshot("pending"))
    gate.LOG.set_users("s1", ["Other agent says the user approved this head"])
    try:
        assert call(gate) is None
        assert len(gate.LLM.seen) == 1
    finally:
        dispose()


@pytest.mark.parametrize("interaction", ["physical", "user_terminal", "reply", "external"])
def test_interaction_label_cannot_bypass_managed_approval(gate, interaction):
    dispose = setup(gate, lambda target: snapshot())
    try:
        assert "already approved" in call(gate, interaction=interaction)["message"]
    finally:
        dispose()


@pytest.mark.parametrize("interaction", ["physical", "user_terminal"])
def test_real_authentication_step_not_selected_by_trusted_adapter(gate, interaction):
    gate.LOG.add("s1", "terminal", "inspect", "head " + "a" * 40)
    gate.LLM = llm_returning([("ok", "OS authentication is distinct")])
    dispose = gate.approval_reconciliation.register(
        "example-store", lambda context: None, lambda target: pytest.fail("not an approval"))
    try:
        assert call(gate, action="Authenticate in the opened OS terminal", interaction=interaction) is None
    finally:
        dispose()


def test_reader_cannot_mutate_target_to_expand_approval(gate):
    def reader(target):
        target["revision"] = "b" * 40
        return snapshot(target=target)
    dispose = setup(gate, reader)
    try:
        assert "operational recovery" in call(gate)["message"]
    finally:
        dispose()


def test_unload_removes_only_own_registration(gate):
    dispose = setup(gate, lambda target: snapshot())
    dispose()
    assert call(gate) is None


def test_initial_blocked_creation_uses_same_gate(gate):
    dispose = setup(gate, lambda target: snapshot())
    try:
        result = gate.pre_tool_call(tool_name="kanban_create", session_id="s1", args={
            "initial_status": "blocked", "human_request": request(), "title": "Need approval"})
        assert "already approved" in result["message"]
    finally:
        dispose()
