import importlib.util
import sys

import pytest
from pathlib import Path

spec = importlib.util.spec_from_file_location("ask_gate_core_rq", Path(__file__).resolve().parents[1] / "core.py")
core = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = core
spec.loader.exec_module(core)

BODY_A = (
    "Goal: add a report_only rule to the example.com checker.\n\n"
    "## Spec\nIf it truly needs David, propose the exact fix and file the blocked card.\n"
    "```\nneeds David -> blocked card\n```\n"
)
BODY_B = (
    "Goal: tighten automated-turn prompts for example.com.\n\n"
    "## Spec\nList as open only what needs David (with its blocked card).\n"
)


def test_ready_cards_quoting_phrases_not_gated():
    for body in (BODY_A, BODY_B):
        assert core.needs_checked("kanban_create", {"title": "Spec work", "body": body}, True)[0] is False


def test_blocked_card_still_gated_on_full_body():
    for body in (BODY_A, BODY_B):
        hit = core.needs_checked("kanban_create", {"title": "x", "body": body, "initial_status": "blocked"}, True)
        assert hit[0] is True


CONDITIONAL_SPEC = (
    "Agent-owned guard reconciliation, not a test ask. "
    "The origin directive says to measure the supported route; "
    "if only David can submit, file a blocked card after verifying the premise. "
    "Investigate the refusal without resubmitting the request."
)


@pytest.mark.parametrize("body", [
    CONDITIONAL_SPEC,
    "If it truly needs David, investigate the access requirement.",
    "Unless David must sign, proceed with the automated check.",
])
def test_ready_conditional_antecedent_is_not_a_present_ask(body):
    args = {"title": "Investigate guard classification", "body": body}
    assert core.needs_checked("kanban_create", args, True)[0] is False
    # Explicit blocks remain conservative, even with conditional language.
    assert core.needs_checked("kanban_create", dict(args, initial_status="blocked"), True)[0] is True
    assert core.needs_checked("kanban_block", {"reason": body}, True)[0] is True


@pytest.mark.parametrize("body", [
    "Goal: David must approve the spend; if he declines, archive.",
    "If the build passes, David needs to merge the change.",
    "If tests fail, David should decide.",
    "If ready, this needs your approval.",
    "Waiting on David for the passkey.",
    "If only David can submit file a card",  # no bounded antecedent
    "If only David can submit, ask David to send it now.",
    CONDITIONAL_SPEC + " David must approve the deploy now.",
])
def test_ready_conditional_preserves_actual_obligations(body):
    assert core.needs_checked("kanban_create", {"title": "Work", "body": body}, True)[0] is True


def test_ready_title_ask_survives_conditional_body():
    assert core.needs_checked("kanban_create", {
        "title": "Needs David to approve the deploy", "body": CONDITIONAL_SPEC,
    }, True)[0] is True


def test_ready_conditional_hook_does_not_request_checked_evidence(gate):
    args = {"title": "Investigate guard classification", "body": CONDITIONAL_SPEC}
    assert gate.pre_tool_call(tool_name="kanban_create", args=args, session_id="s1") is None
    blocked = gate.pre_tool_call(tool_name="kanban_create", args=dict(args, initial_status="blocked"), session_id="s1")
    assert blocked["action"] == "block" and "This blocked card" in blocked["message"]
    actual = gate.pre_tool_call(tool_name="kanban_create", args={
        **args, "body": CONDITIONAL_SPEC + " David must approve the deploy now.",
    }, session_id="s1")
    assert actual["action"] == "block" and "This card" in actual["message"]
    assert "This blocked card" not in actual["message"]


def test_ready_card_goal_line_still_gated():
    assert core.needs_checked("kanban_create", {"title": "Needs David to approve the deploy", "body": "x"}, True)[0]
