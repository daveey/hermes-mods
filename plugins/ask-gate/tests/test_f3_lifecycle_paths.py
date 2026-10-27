"""Typed terminal facts fence actual prompt hooks, never authorize actions."""
import time
import pytest
from conftest import llm_returning

ASK = "Could you reconnect the example account?"
TARGET = {"authority": "example", "kind": "account", "subject": "account-1", "action": "connect"}

@pytest.mark.parametrize("surface", ["final", "legacy", "structured", "clarify"])
@pytest.mark.parametrize("state", ["revoked", "cancelled", "completed", "impossible"])
def test_terminal_lifecycle_blocks_prompts(gate, monkeypatch, tmp_path, surface, state):
    monkeypatch.setenv("ASK_GATE_LIFECYCLE_DB", str(tmp_path / "lifecycle.sqlite"))
    seen = []
    def resolve(context):
        seen.append(context)
        return TARGET
    dispose = gate.lifecycle.register("example", resolve, lambda target: {
        "target": target, "state": state, "revision": 1, "verified": True, "observed_at": time.time()})
    gate.LLM = llm_returning([("ok", "incorrect stale permission")])
    try:
        if surface == "final":
            result = gate.pre_final_gate(session_id="s1", final_response=ASK, hold_budget=0)
            assert result and "replace" in result
            assert ASK not in result["replace"]
        else:
            tool = "clarify" if surface == "clarify" else "kanban_block"
            args = {"questions": [{"question": ASK}]} if surface == "clarify" else {"kind": "needs_input", "reason": ASK}
            if surface == "structured":
                args["human_request"] = {"action": ASK, "why_human": "Needs user", "interaction": "external", "scope": "example", "evidence": "inspected state"}
            result = gate.pre_tool_call(session_id="s1", tool_name=tool, args=args)
            assert result and result["action"] == "block"
        assert seen
        assert not gate.LLM.seen
    finally:
        dispose()

@pytest.mark.parametrize("kind", ["dependency", "time", "transient"])
def test_operational_kind_cannot_hide_retired_ask(gate, monkeypatch, tmp_path, kind):
    monkeypatch.setenv("ASK_GATE_LIFECYCLE_DB", str(tmp_path / "lifecycle.sqlite"))
    dispose = gate.lifecycle.register("example", lambda ctx: TARGET,
        lambda target: {"target": target, "state": "revoked", "revision": 1,
                        "verified": True, "observed_at": time.time()})
    try:
        result = gate.pre_tool_call(session_id="s1", tool_name="kanban_block",
                                   args={"kind": kind, "reason": ASK})
        assert result and result["action"] == "block"
    finally:
        dispose()

@pytest.mark.parametrize("draft,blocked", [("Reconnect the example account.", True),
                                          ("The example account is revoked.", False)])
def test_full_draft_reaches_trusted_lifecycle_classifier(gate, monkeypatch, tmp_path, draft, blocked):
    monkeypatch.setenv("ASK_GATE_LIFECYCLE_DB", str(tmp_path / "lifecycle.sqlite"))
    seen = []
    def resolve(ctx):
        seen.append(ctx)
        return TARGET if ctx.get("draft") == "Reconnect the example account." else None
    dispose = gate.lifecycle.register("example", resolve,
        lambda target: {"target": target, "state": "revoked", "revision": 1,
                        "verified": True, "observed_at": time.time()})
    try:
        result = gate.pre_final_gate(session_id="s1", final_response=draft, hold_budget=0)
        assert bool(result and "replace" in result) == blocked
        assert any(ctx.get("draft") == draft for ctx in seen)
    finally:
        dispose()
