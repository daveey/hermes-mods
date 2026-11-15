"""card_claim (self-heal agent-2bdd943d68): a card text (kanban_create title/body, kanban_comment body, kanban_complete
summary/result) that states a fleet-wide state claim ("every host has ...") or an absence claim ("stopped reporting")
gets a hint naming the readback the claim needs. Hint only, never a refusal.

Incident: a MacBook orchestrator chat filed a macOS test card saying the lane had "stopped reporting" (the workflow is
cron/dispatch-only, so a missing check-run on main was expected), and wrote on a sweep card that every host had the
newer PRs (it had compared host checkouts with a scratch clone's lagging origin/main). Both were wrong; the chat
admitted it at end of turn, after the cards were filed."""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


rules = load("coach_rules_card_claim_test", ROOT / "agent_rules.py")

OK = json.dumps({"ok": True})

# the incident's two claim shapes (names generalized)
ABSENCE = ("The macOS OS-test lane stopped reporting on main after the last merge; no check-run since. "
           "Investigate why the workflow is not running.")
FLEET = "Sweep done: every host has the newer PRs installed, nothing left to roll out."

FIRES = [
    ABSENCE,
    FLEET,
    "All 11 machines have a clean checkout at 3a9f2c1.",
    "All 8 workers have shape B now.",
    "Every host had the fix by 14:00.",
    "that card stopped reporting yesterday",
    "The nightly digest went silent two days ago.",
    "each gateway is running the new build",
    "All hosts are on the latest core.",
]

QUIET = [
    # conditional, target or future: a plan, not a claim of current state
    "Once every host has the newer PRs, close this card.",
    "Verify every host has the fix before reporting done.",
    "Every host should have the fix after the next sync pass.",
    "When all workers have shape B, retire the old path.",
    "If the lane stopped reporting, the alert would fire.",
    "every host will run it on the next 10-minute pass",
    # marked unverified
    "Every host has the PRs (unverified: read back per host).",
    "(hypothesis) the lane stopped reporting after the runner change",
    # code-path why-chains, not observations
    "so the reconcile step never runs on a clean tree",
    "Unrelated: 14 tests pass; 0 rows dropped.",
    "The reporter stops reporting when the queue is empty, by design.",
]


def coach():
    return rules.AgentCoach(None, signals=None)


@pytest.mark.parametrize("text", FIRES)
def test_unverified_claim_is_found(text):
    assert rules.card_claims(text)


@pytest.mark.parametrize("text", QUIET)
def test_conditional_marked_or_code_path_text_is_quiet(text):
    assert rules.card_claims(text) == []


def test_kanban_create_absence_incident_gets_hint():
    c = coach()
    c.observe("s1", "kanban_create", {"title": "macOS tests: lane down", "body": ABSENCE}, OK)
    hint = c.take("s1") or ""
    assert "stopped reporting" in hint and "trigger" in hint and "unverified" in hint


def test_kanban_comment_fleet_incident_gets_hint():
    c = coach()
    c.observe("s1", "kanban_comment", {"task_id": "t_x", "body": FLEET}, OK)
    hint = c.take("s1") or ""
    assert "every host has" in hint.lower() and "per host" in hint and "ls-remote" in hint


@pytest.mark.parametrize("tool,args", [
    ("mcp__kanban_complete", {"summary": FLEET}),
    ("kanban_create", {"title": FLEET}),
    ("tool_call", {"calls": [{"name": "kanban_comment", "arguments": {"task_id": "t_x", "body": ABSENCE}}]}),
])
def test_other_card_tools_get_hint(tool, args):
    c = coach()
    c.observe("s1", tool, args, OK)
    assert "readback" in (c.take("s1") or "")


def test_closed_card_hint_asks_for_a_comment():
    c = coach()
    c.check_card_claim("s1", "kanban_complete", {"summary": FLEET})
    assert "kanban_comment" in (c.take("s1") or "")


def test_non_card_tools_are_quiet():
    c = coach()
    c.observe("s1", "terminal", {"command": f"echo '{FLEET}'"}, OK)
    c.observe("s1", "write_file", {"path": "/tmp/x", "content": ABSENCE}, OK)
    assert "readback" not in (c.take("s1") or "")


def test_hint_lands_on_the_same_call_and_fires_once_per_claim():
    c = coach()
    c.check_card_claim("s1", "kanban_create", {"body": ABSENCE})
    assert "stopped reporting" in (c.take("s1") or "")
    c.check_card_claim("s1", "kanban_comment", {"body": ABSENCE})
    c.observe("s1", "kanban_comment", {"body": ABSENCE}, OK)
    assert c.take("s1") is None


def test_transform_hook_wires_the_rule():
    src = (ROOT / "__init__.py").read_text()
    body = src[src.index("def transform_tool_result"):]
    body = body[:body.index("\n    def ", 10)]
    assert "check_card_claim" in body


def test_readme_documents_the_rule():
    assert "`card_claim`" in (ROOT / "README.md").read_text()
