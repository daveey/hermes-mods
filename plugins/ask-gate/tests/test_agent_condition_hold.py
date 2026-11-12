"""agent_condition_hold: an "ask first" condition an agent set is not a reason to ask David
(self-heal agent-ad97832ad7).

On 2026-11-12 an orchestrator applied the exact-head-pinned bb-size-ok label at David's instruction, then steered the
worker card with its own rule "operator says ask before any new head". The worker blocked needs_input asking David to
authorize a test-only CI repair, citing that rule as why_human; the structured judge passed it, and the orchestrator
lifted its own rule 23 s later. The exact request below is the hub relay of that block (state.db message 1200802).
"""
import json

import pytest
from conftest import llm_returning

INCIDENT_REQUEST = {
    "action": "Authorize the narrow CI repair and a new head for Baby Bear PR #3574. Unblock = implement/test the "
              "repair and use the normal train; archive = abandon this cleanup without claiming it landed.",
    "why_human": "The operator explicitly prohibited pushing any different head under David's ffdbc3be2611 size "
                 "approval and required asking before a new head. CI now proves a source change is needed, not a "
                 "retry.",
    "interaction": "approval",
    "scope": "Restore the synthetic documentation SQL validator, allow that exact retained script in the asset "
             "regression, and correct its manifest classification; no runtime or Swift changes, no gate bypass.",
    "evidence": "gh run view 43148908244: job 130957027760 test_location_store.SchemaTests."
                "test_documentation_validator_passes invokes deleted docs/plans/assets/2026-10-23-unified-locations/"
                "validate_contract.py",
}

# The same ask, but the requirement is David's own and the request quotes him.
DAVID_QUOTED_REQUEST = dict(
    INCIDENT_REQUEST,
    why_human="David wrote in chat (19:52 PST): \"no new head on #3574 without asking me first\". The orchestrator "
              "relayed that, and CI now proves a source change is needed.")

# An ordinary human-only ask: no agent condition anywhere.
CREDENTIAL_REQUEST = {
    "action": "Run `gh auth login` in Terminal on the MacBook and finish the device-code sign-in with your GitHub "
              "account. Unblock = an agent re-runs the release; archive = skip the release.",
    "why_human": "The GitHub device flow signs in to David's own account; no agent holds that login or a token for it.",
    "interaction": "user_terminal",
    "scope": "One gh auth login on the MacBook; nothing else.",
    "evidence": "terminal: gh auth status -> You are not logged into any GitHub hosts",
}


def _events(tmp_path, kind):
    p = tmp_path / "logs" / "ask-gate.jsonl"
    if not p.exists():
        return []
    return [r for r in map(json.loads, p.read_text().splitlines()) if r.get("kind") == kind]


def _block(gate, hr, session="s1", verdict=("ok", "a real human step")):
    gate.LOG.add(session, "terminal", "evidence check", hr["evidence"])
    gate.LLM = llm_returning([verdict])
    return gate.pre_tool_call(tool_name="kanban_block", session_id=session,
                              args={"kind": "needs_input", "reason": "see human_request", "human_request": hr})


def test_incident_request_is_held_without_a_model_call(gate, tmp_path):
    r = _block(gate, INCIDENT_REQUEST)
    assert r and r["action"] == "block"
    assert "agent_condition_hold" in r["message"]
    assert "not David" in r["message"]
    assert gate.LLM.seen == []  # deterministic: held before the judge
    held = _events(tmp_path, "agent_condition_hold")
    assert held and held[0]["tool"] == "kanban_block" and "operator" in held[0]["quote"]


def test_incident_request_is_held_in_a_kanban_worker(gate, monkeypatch):
    monkeypatch.setenv("HERMES_KANBAN_TASK", "t_01a1cd306c1e1c2b")
    r = _block(gate, INCIDENT_REQUEST)
    assert r and "agent_condition_hold" in r["message"]


def test_blocked_card_creation_with_the_incident_request_is_held(gate):
    gate.LOG.add("s1", "terminal", "evidence check", INCIDENT_REQUEST["evidence"])
    gate.LLM = llm_returning([("ok", "fine")])
    r = gate.pre_tool_call(tool_name="kanban_create", session_id="s1", args={
        "title": "PR #3574 new head", "initial_status": "blocked", "human_request": INCIDENT_REQUEST})
    assert r and "agent_condition_hold" in r["message"]


@pytest.mark.parametrize("why", [
    "The orchestrator's condition on this card is to ask before any push.",
    "Per the operator steer in the card comment, I must get approval before a new head.",
    "The creator card told me to ask before re-labelling.",
    "This is my own condition: confirm before pushing.",
])
def test_prose_variants_are_held(gate, why):
    assert gate.agent_condition.hit(dict(INCIDENT_REQUEST, why_human=why)) is not None


def test_request_quoting_david_passes(gate, tmp_path):
    r = _block(gate, DAVID_QUOTED_REQUEST)
    if r is not None:  # the PR-approval wording check holds this wording once; a retry passes it
        assert "agent_condition_hold" not in r["message"]
        r = _block(gate, DAVID_QUOTED_REQUEST)
    assert r is None
    assert not _events(tmp_path, "agent_condition_hold")


def test_user_step_id_counts_as_david(gate):
    hr = dict(INCIDENT_REQUEST, evidence=INCIDENT_REQUEST["evidence"] + "; David's answer us_4e904be48c")
    assert gate.agent_condition.hit(hr) is None


def test_ordinary_credential_ask_passes(gate, tmp_path):
    r = _block(gate, CREDENTIAL_REQUEST)
    assert r is None
    assert gate.LLM.seen  # reached the judge
    assert not _events(tmp_path, "agent_condition_hold")


@pytest.mark.parametrize("why", [
    "The orchestrator's token lacks admin permission on the org; only the org owner can grant it.",
    "It adds recurring AWS spend; the disk-guard policy reserves volume-size changes for David.",
])
def test_negative_controls(gate, why):
    assert gate.agent_condition.hit(dict(CREDENTIAL_REQUEST, why_human=why)) is None


def test_hold_is_bounded_then_released(gate, tmp_path):
    for _ in range(2):
        assert "agent_condition_hold" in _block(gate, INCIDENT_REQUEST)["message"]
    r = _block(gate, INCIDENT_REQUEST)
    assert r is None or "agent_condition_hold" not in r.get("message", "")
    assert _events(tmp_path, "agent_condition_released")


def test_gate_error_fails_open(gate, tmp_path, monkeypatch):
    def broken(_request):
        raise RuntimeError("regex exploded")
    monkeypatch.setattr(gate.agent_condition, "hit", broken)
    r = _block(gate, INCIDENT_REQUEST)
    if r is not None:  # the PR-approval wording check holds this wording once; a retry passes it
        assert "agent_condition_hold" not in r["message"]
        r = _block(gate, INCIDENT_REQUEST)
    assert r is None  # the hold broke: the judge decides, as before
    assert gate.LLM.seen
    errs = _events(tmp_path, "agent_condition_error")
    assert errs and "regex exploded" in errs[0]["error"]


def test_judge_rule_covers_prose_the_regex_misses(gate):
    assert "condition an agent set" in gate.judge_mod.STRUCTURED_SYSTEM
