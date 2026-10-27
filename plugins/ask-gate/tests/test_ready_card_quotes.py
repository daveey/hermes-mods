import importlib.util
import sys
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


def test_ready_card_goal_line_still_gated():
    assert core.needs_checked("kanban_create", {"title": "Needs David to approve the deploy", "body": "x"}, True)[0]
