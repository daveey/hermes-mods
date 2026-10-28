"""A judge outage is not a verdict: fall back across classes, then accept the deterministically checked ask.

10-28 ~08:05 PT a verified explicit ask got "No eligible fast routes under billing/capability policy" (a
ModelRoutingError from allocating the judge's model) wrapped as a refusal with "Keep operational failures parked
without a human_request" advice, which turned a real ask into a hidden wait.
"""
import json

import pytest
from conftest import llm_returning

PARK_ADVICE = "parked without a human_request"


def human(**changes):
    return {"action": "Approve merging PR 1777 on head 46754d080", "why_human": "Red-tier merge policy reserves it",
            "scope": "PR 1777 only", "evidence": "terminal gh pr view 1777 returned REVIEW_REQUIRED",
            "interaction": "approval", **changes}


def call(gate, hr):
    return gate.pre_tool_call(tool_name="kanban_block", session_id="s1",
                              args={"reason": "PR 1777 waits on David", "kind": "needs_input", "human_request": hr})


class ModelRoutingError(ValueError):
    """Same shape as agent.model_routing.ModelRoutingError (a ValueError subclass)."""


def routing_down(system, user):
    raise ModelRoutingError("No eligible fast routes under billing/capability policy")


def events(gate, tmp_path):
    p = tmp_path / "logs" / "ask-gate.jsonl"
    return [json.loads(line) for line in p.read_text().splitlines()] if p.exists() else []


@pytest.fixture
def verified(gate):
    gate.LOG.add("s1", "terminal", "gh pr view 1777", "REVIEW_REQUIRED")
    return gate


def test_routing_error_falls_back_to_the_next_class(verified, tmp_path):
    gate = verified
    gate.LLM = routing_down
    standard = llm_returning([("ok", "real merge approval")])
    tried = []
    gate.FALLBACKS = lambda: [("class:fast", lambda s, u: tried.append("fast") or routing_down(s, u)),
                              ("class:standard", lambda s, u: tried.append("standard") or standard(s, u)),
                              ("class:deep", lambda s, u: tried.append("deep") or standard(s, u))]
    assert call(gate, human()) is None
    assert tried == ["fast", "standard"]
    kinds = [e["kind"] for e in events(gate, tmp_path)]
    assert "judge_fallback" in kinds and "judge_unavailable" not in kinds


def test_fallback_refusal_is_still_a_refusal(verified):
    gate = verified
    gate.LLM = routing_down
    gate.FALLBACKS = lambda: [("class:standard", llm_returning([("unverified", "PR already approved", "merge it")]))]
    r = call(gate, human())
    assert r["action"] == "block" and "not verified" in r["message"]


@pytest.mark.parametrize("err", [ModelRoutingError("No eligible fast routes under billing/capability policy"),
                                 TimeoutError("judge timed out"), ConnectionError("provider 529 overloaded")])
def test_no_reachable_judge_accepts_the_checked_ask(verified, tmp_path, err):
    gate = verified

    def down(system, user):
        raise err
    gate.LLM = down
    gate.FALLBACKS = lambda: [("class:fast", down), ("class:standard", down), ("class:deep", down)]
    assert call(gate, human()) is None
    unavailable = [e for e in events(gate, tmp_path) if e["kind"] == "judge_unavailable"]
    assert len(unavailable) == 1 and type(err).__name__ in json.dumps(unavailable[0])


def test_no_fallback_routes_still_accepts_and_logs(verified, tmp_path):
    gate = verified
    gate.LLM = routing_down
    gate.FALLBACKS = lambda: []
    assert call(gate, human()) is None
    assert any(e["kind"] == "judge_unavailable" for e in events(gate, tmp_path))


def test_outage_never_skips_the_deterministic_checks(gate):
    """No tool call this turn backs the evidence: refused before any judge runs, outage or not."""
    gate.LLM = routing_down
    gate.FALLBACKS = lambda: []
    r = call(gate, human())
    assert r["action"] == "block" and "matches no tool call" in r["message"]


def test_real_not_verified_verdict_is_still_refused(verified):
    gate = verified
    gate.LLM = llm_returning([("unverified", "nothing checked the approval store", "read the store")])
    gate.FALLBACKS = lambda: (_ for _ in ()).throw(AssertionError("a verdict must not fall back"))
    r = call(gate, human())
    assert r["action"] == "block" and "not verified" in r["message"]


def test_internal_gate_error_never_carries_the_park_advice(verified, monkeypatch):
    gate = verified
    monkeypatch.setattr(gate.approval_reconciliation, "preflight", lambda ctx: 1 / 0)
    r = call(gate, human())
    assert r["action"] == "block"
    assert PARK_ADVICE not in r["message"] and "not a verdict" in r["message"]
