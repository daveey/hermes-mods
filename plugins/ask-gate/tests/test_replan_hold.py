"""replan_hold: an approved goal whose first plan ran out is replanned, not handed back to David
(self-heal agent-866a12f54f).

On 2026-11-02 the babybear CI-cost card t_c21c170e (goal: cut Actions spend >=50% without losing coverage) blocked
needs_input with "decide whether to end ... with the target unproven, or expand scope to a ... test" and the hub chat
relayed that question to David three times. A new approach to the same approved goal is not new scope; only a
changed limit (spend, permission, acceptance, safety) or a human-only step needs him. Nothing at the ask surface
checked that: the judge passes every "choice between options" as ok.
"""
import json

import pytest
from conftest import llm_returning

# The exact request text t_c21c170e filed (hub state.db row 673362 relays it verbatim).
CI_COST_ACTION = ("Decide whether to end the capacity project with its >=50% equal-workload target explicitly unproven "
                  "(recommended), or expand scope to a separately reviewed test")
CI_COST_REQUEST = {
    "action": CI_COST_ACTION + " redesign. Unblock = continue with the redesign; archive = end the project.",
    "why_human": "The original optimization scope is exhausted; a test-execution redesign is new scope.",
    "interaction": "approval",
    "scope": "CI cost: cut babybear GitHub Actions spend (~$955 this month, mostly macOS minutes) <= 50% without "
             "losing coverage",
    "evidence": "terminal: ci-cost ledger shows 31% reduction after the shipped caching and sharding changes",
}
CI_COST_RELAY = ("⏸ Kanban t_c21c170e blocked: " + CI_COST_ACTION + " — CI cost: cut babybear GitHub Actions spend "
                 "(~$955 this month, mostly macOS minutes) ≤ 50% without losing coverage. Your call: which way?")

# Real asks that must keep passing: they name the limit that changes (t_01a1a44d9e230f03, t_01a1a2760c813b53).
GPU_REQUEST = {
    "action": "Approve up to $6 more GPU (one L40S/A100, ~2 h) to diagnose why the 5K LoRA SFT collapsed. That brings "
              "the Agent-World repro total to ~$29.15, over the $25 standing cap. Unblock = approve and run the "
              "diagnosis; archive = drop it and keep the reported FAIL.",
    "why_human": "The standing GPU budget is $25 total per job and $23.15 is already spent; going over needs David's "
                 "own number.",
    "interaction": "approval",
    "scope": "One single-GPU Vast/RunPod rental, ≤$6, auto-teardown; no retraining.",
    "evidence": "results.md: BFCL non-live base 88.92 vs SFT 18.87; SFT output p90 = 4096 tokens; spend ledger $23.15 "
                "(aw_repro_watchdog.log).",
}
EBS_REQUEST = {
    "action": "Approve growing hermes-hub's root EBS volume vol-0a2aa9699635c3d5d from 400 to 600 GiB gp3 (~+$16/month, "
              "online, no downtime). Unblock = an agent does the resize and verifies df; archive = keep 400 GiB and "
              "rely on retention tiers.",
    "why_human": "It adds recurring AWS spend in account 767406518141. The disk-guard policy reserves volume-size "
                 "changes for David.",
    "interaction": "approval",
    "scope": "One EBS modify-volume on vol-0a2aa9699635c3d5d (hermes-hub root) plus an online growpart/resize2fs. "
             "Nothing else changes.",
    "evidence": "Root 95% (368/387 GiB) at 17:25 UTC 2026-11-03, up from 84% at 06:05.",
}


def _events(tmp_path, kind):
    p = tmp_path / "logs" / "ask-gate.jsonl"
    if not p.exists():
        return []
    return [r for r in map(json.loads, p.read_text().splitlines()) if r.get("kind") == kind]


def _block(gate, hr, session="s1"):
    # Today's path: the evidence matches a tool call this turn and the judge passes a "choice between options" as ok.
    gate.LOG.add(session, "terminal", "ledger check", hr["evidence"])
    if getattr(gate.LLM, "__name__", "") == "boom":
        gate.LLM = llm_returning([("ok", "a choice between options")])
    return gate.pre_tool_call(tool_name="kanban_block", session_id=session,
                              args={"kind": "needs_input", "reason": "see human_request", "human_request": hr})


def test_ci_cost_request_is_held_without_a_model_call(gate, tmp_path):
    r = _block(gate, CI_COST_REQUEST)
    assert r and r["action"] == "block"
    assert gate.LLM.seen == []  # deterministic: held before the judge
    assert "replan" in r["message"].lower()
    assert "spend, permission, acceptance" in r["message"]
    held = _events(tmp_path, "replan_hold")
    assert held and held[0]["tool"] == "kanban_block"


def test_ci_cost_request_is_held_in_a_kanban_worker(gate, monkeypatch):
    monkeypatch.setenv("HERMES_KANBAN_TASK", "t_c21c170e")  # the real path: the worker files the block
    r = _block(gate, CI_COST_REQUEST)
    assert r and r["action"] == "block" and "replan" in r["message"].lower()


def test_ci_cost_blocked_card_creation_is_held(gate):
    gate.LOG.add("s1", "terminal", "ledger check", CI_COST_REQUEST["evidence"])
    gate.LLM = llm_returning([("ok", "a choice between options")])
    r = gate.pre_tool_call(tool_name="kanban_create", session_id="s1", args={
        "title": "CI cost: decide end vs redesign", "initial_status": "blocked", "human_request": CI_COST_REQUEST})
    assert r and r["action"] == "block" and "replan" in r["message"].lower()


def test_ci_cost_legacy_prose_block_is_held(gate):
    r = gate.pre_tool_call(tool_name="kanban_block", session_id="s1", args={
        "kind": "needs_input", "reason": CI_COST_ACTION + ". Checked: terminal ledger — 31% reduction"})
    assert r and r["action"] == "block" and "replan" in r["message"].lower()


def test_ci_cost_relay_in_a_chat_reply_is_held(gate, tmp_path):
    r = gate.pre_final_gate(session_id="s1", platform="tui", final_response=CI_COST_RELAY, turn_messages=[])
    assert r and r.get("hold") is True
    assert "replan" in r["nudge"].lower()
    assert _events(tmp_path, "replan_hold")


def test_clarify_with_the_same_choice_is_held(gate):
    r = gate.pre_tool_call(tool_name="clarify", session_id="s1", args={"questions": [{"question": CI_COST_ACTION}]})
    assert r and r["action"] == "block" and "replan" in r["message"].lower()


@pytest.mark.parametrize("hr", [GPU_REQUEST, EBS_REQUEST], ids=["gpu-over-cap", "ebs-resize"])
def test_real_limit_asks_are_not_replan_held(gate, tmp_path, hr):
    gate.LOG.add("s1", "terminal", "ledger check", hr["evidence"])
    gate.LLM = llm_returning([("ok", "a real spend decision")])
    r = _block(gate, hr)
    assert r is None or "replan" not in r.get("message", "").lower()
    assert not _events(tmp_path, "replan_hold")


def test_naming_the_changed_limit_releases_the_hold(gate, tmp_path):
    hr = dict(CI_COST_REQUEST, why_human="Ending now changes the approved acceptance criteria: the >=50% target "
                                         "would be dropped, which only David can decide.")
    gate.LOG.add("s1", "terminal", "ledger check", hr["evidence"])
    gate.LLM = llm_returning([("ok", "acceptance change named")])
    r = _block(gate, hr)
    assert r is None or "replan" not in r.get("message", "").lower()
    assert not _events(tmp_path, "replan_hold")


def test_hold_is_bounded_then_released(gate, tmp_path):
    for _ in range(2):
        assert "replan" in _block(gate, CI_COST_REQUEST)["message"].lower()
    gate.LOG.add("s1", "terminal", "ci-cost ledger", CI_COST_REQUEST["evidence"])
    gate.LLM = llm_returning([("ok", "fine")])
    r = _block(gate, CI_COST_REQUEST)
    assert r is None or "replan" not in r.get("message", "").lower()
    assert _events(tmp_path, "replan_released")


@pytest.mark.parametrize("text", [
    "Should I redesign the login page or keep the current one?",          # a design preference in chat
    "Want me to keep going on the CI cost work?",
    "I ended the old capacity probe; the new runner design is live.",       # report, not an ask
    "Approve up to $6 more GPU over the $25 cap, or drop the diagnosis?",
])
def test_chat_negative_controls(gate, text):
    assert gate.replan.chat_hits([text]) == []


def test_judge_no_longer_passes_every_choice_between_options(gate):
    assert "new approach to the same approved goal is not new scope" in gate.judge_mod.SYSTEM
