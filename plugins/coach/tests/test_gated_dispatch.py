"""gated_dispatch (agent-376c3d93de): a background helper whose goal is conditional on admission is not a started fix.

2026-11-16 14:40Z hub session 20261113_195433_8985eb dispatched a background delegate_task whose goal read "Obtain
supported sole-writer reservation before editing; if pacing expressly bars execution report exact wall instead",
got back `{"status": "dispatched", "mode": "background"}` and told David "The correction is started". Four minutes
later the helper reported no reservation and no change; the agent had to retract the claim. Nothing on the dispatch
result said the helper's start was conditional, so the coach now attaches one hint to that result.
"""
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


rules = load("coach_rules_test_gated_dispatch", ROOT / "agent_rules.py")
core = load("coach_core_test_gated_dispatch", ROOT / "core.py")

# Replay of the incident call (hub state.db msg 1332474) and its result (msg 1332475), goal cut after the gate.
INCIDENT_GOAL = (
    "Take over bounded correction on existing Self-work B card t_01a1ddd4ef21616d, without new cards or bypassing "
    "admission. First load relevant Hermes skills and inspect live claim/admission. Obtain supported sole-writer "
    "reservation before editing; if pacing expressly bars execution report exact wall instead. Fix worker "
    "producer/install mismatch: template home/skills/devops/kanban-orchestrator/templates/post-merge-check-card.md "
    "invokes python3 ~/.hermes/bin/hermes-selfwork-policy.py but worker installer puts only sibling "
    "~/.hermes/lib/core-tools/hermes-selfwork-policy.py.")
INCIDENT_ARGS = {"tasks": [{"goal": INCIDENT_GOAL, "context": "Origin chat has standing fix approval."}]}
INCIDENT_RESULT = json.dumps({
    "status": "dispatched", "mode": "background", "count": 1, "delegation_id": "deleg_665ec55c",
    "goals": [INCIDENT_GOAL],
    "note": "Subagent is running in the background; its full result re-enters the conversation as a new message "
            "when it finishes.",
    "subagent_ids": ["sa-0-16a164d6"]})


def hooks(tmp_path):
    plugin = load("coach_plugin_test_gated_dispatch", ROOT / "__init__.py")
    _, h = plugin.build(store=core.Store(tmp_path / "coach"), checkout=tmp_path)
    return h


def test_incident_dispatch_result_gets_the_hint(tmp_path):
    h = hooks(tmp_path)
    out = h["transform_tool_result"](session_id="s1", tool_name="delegate_task", args=INCIDENT_ARGS,
                                     result=INCIDENT_RESULT)
    assert out is not None
    payload = json.loads(out)
    assert payload["status"] == "dispatched"
    hint = payload["coach_hint"]
    assert "not started" in hint and "deleg_665ec55c" in hint
    # once per delegation
    assert h["transform_tool_result"](session_id="s1", tool_name="delegate_task", args=INCIDENT_ARGS,
                                      result=INCIDENT_RESULT) is None


def test_gate_phrase_detected():
    assert rules.gated_dispatch("delegate_task", INCIDENT_ARGS, INCIDENT_RESULT) == "deleg_665ec55c"


@pytest.mark.parametrize("goal", [
    "Fix the producer path, run the tests and open a PR.",
    "Review the admission gate code in hermes_cli/kanban_admission.py and report bugs.",
    "Check whether the reservation table has stale rows.",
])
def test_ungated_goal_passes(goal):
    res = INCIDENT_RESULT.replace(INCIDENT_GOAL, goal)
    assert rules.gated_dispatch("delegate_task", {"tasks": [{"goal": goal}]}, res) is None


@pytest.mark.parametrize("goal", [
    "Only once admitted by the dispatcher, apply the fix.",
    "Acquire an execution slot before any edit, then fix it.",
    "If the quota pacing hold refuses execution, stop and report the wall.",
])
def test_other_gate_phrasings(goal):
    res = INCIDENT_RESULT.replace(INCIDENT_GOAL, goal)
    assert rules.gated_dispatch("delegate_task", {"tasks": [{"goal": goal}]}, res)


def test_foreground_or_non_spawn_passes():
    done = json.dumps({"status": "completed", "results": [{"summary": "no reservation"}]})
    assert rules.gated_dispatch("delegate_task", INCIDENT_ARGS, done) is None
    assert rules.gated_dispatch("delegate_task", {"action": "list"}, INCIDENT_RESULT) is None
    assert rules.gated_dispatch("terminal", {"command": INCIDENT_GOAL}, INCIDENT_RESULT) is None
