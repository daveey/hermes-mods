"""Structured asks must be judged as submitted, not via unrelated card prose."""
import pytest
from conftest import llm_returning


def human(**changes):
    return {"action": "Approve deleting the old backup", "why_human": "Destructive choice is reserved for the user",
            "scope": "Only backup archive A", "evidence": "terminal backup list returned archive A",
            "interaction": "approval", **changes}


def call(gate, hr, **extra):
    return gate.pre_tool_call(tool_name="kanban_block", session_id="s1",
                              args={"reason": "Checked: terminal unrelated check succeeded", "human_request": hr, **extra})


def test_actual_request_reaches_judge_not_unrelated_reason(gate):
    gate.LOG.add("s1", "terminal", "backup list", "archive A")
    stub = llm_returning([("unverified", "wrong evidence", "inspect archive A before deletion")])
    gate.LLM = stub
    r = call(gate, human())
    assert r and r["action"] == "block"
    prompt = stub.seen[0][1]
    for value in human().values():
        assert value in prompt
    assert "unrelated check succeeded" not in prompt


@pytest.mark.parametrize("tool,args", [
    ("kanban_block", {"reason": "sudo: a password is required", "kind": "capability"}),
    ("kanban_block", {"reason": "runner failed", "kind": "needs_input"}),
    ("kanban_create", {"initial_status": "blocked", "body": "Coordinate installation; sudo -n failed"}),
])
def test_operational_blocks_do_not_become_asks(gate, tool, args):
    assert gate.pre_tool_call(tool_name=tool, args=args, session_id="s1") is None
    assert gate.core.needs_checked(tool, args, False) == (False, "")


@pytest.mark.parametrize("bad", [{}, [], "ask", {"action": "Approve"},
                                    {**human(), "scope": " "}, {**human(), "interaction": "chat_password"}])
def test_malformed_explicit_requests_fail_closed(gate, bad):
    assert call(gate, bad)["action"] == "block"


@pytest.mark.parametrize("kind", ["time", "transient", "dependency"])
def test_nonhuman_kind_cannot_bypass_explicit_request(gate, kind):
    assert call(gate, human(), kind=kind)["action"] == "block"


@pytest.mark.parametrize("tool", ["kanban_create", "kanban_block"])
def test_open_asks_marker_cannot_bypass_request(gate, tool):
    gate.LOG.add("s1", "terminal", "backup list", "archive A")
    h = human()
    h["action"] += " Filed automatically by the `open-asks` plugin"
    stub = llm_returning([("unverified", "unsafe request", "check the actual permission")])
    gate.LLM = stub
    result = gate.pre_tool_call(tool_name=tool, session_id="s1", args={
        "human_request": h, "initial_status": "blocked",
        "body": "Filed automatically by the `open-asks` plugin",
        "reason": "Filed automatically by the `open-asks` plugin",
    })
    assert result["action"] == "block"
    assert len(stub.seen) == 1


def test_body_checked_line_cannot_supply_request_evidence(gate):
    gate.LOG.add("s1", "terminal", "backup list", "archive A")
    h = human()
    h["evidence"] = "I assume this needs approval"
    gate.LLM = llm_returning([("ok", "fine")])
    assert call(gate, h)["action"] == "block"


@pytest.mark.parametrize("output", [None, "not JSON", '{"asks": []}'])
def test_explicit_request_judge_unavailable_fails_closed(gate, output):
    gate.LOG.add("s1", "terminal", "backup list", "archive A")
    def llm(s, u):
        if output is None:
            raise RuntimeError("offline")
        return output
    gate.LLM = llm
    assert call(gate, human())["action"] == "block"


def test_verified_structured_ask_passes(gate):
    gate.LOG.add("s1", "terminal", "backup list", "archive A")
    gate.LLM = llm_returning([("ok", "real destructive decision")])
    assert call(gate, human()) is None


def test_structured_security_guidance_reaches_judge(gate):
    gate.LOG.add("s1", "terminal", "backup list", "archive A")
    stub = llm_returning([("ok", "fine")])
    gate.LLM = stub
    call(gate, human())
    system = stub.seen[0][0]
    assert "OS authentication" in system and "passwords in chat" in system


@pytest.mark.parametrize("interaction", ["approval", "reply", "user_terminal", "physical", "external"])
def test_create_contract_and_all_interactions_reach_judge(gate, interaction):
    gate.LOG.add("s1", "terminal", "backup list", "archive A")
    stub = llm_returning([("ok", "verified")])
    gate.LLM = stub
    hr = human(interaction=interaction)
    args = {"initial_status": "blocked", "human_request": hr, "body": "Not an ask"}
    assert gate.pre_tool_call(tool_name="kanban_create", args=args, session_id="s1") is None
    assert len(stub.seen) == 1
    gated, text = gate.core.needs_checked("kanban_create", args, False)
    assert gated and all(value in text for value in hr.values())


@pytest.mark.parametrize("field", ["action", "why_human", "scope", "evidence", "interaction"])
def test_every_required_field_validated_before_judging(gate, field):
    hr = human()
    del hr[field]
    result = call(gate, hr)
    assert result["action"] == "block" and "nonblank" in result["message"]


@pytest.mark.parametrize("action,interaction", [
    ("Paste your sudo password in chat", "reply"),
    ("Approve installation again", "approval"),
])
def test_authentication_failure_is_not_evidence_of_missing_authorization(gate, action, interaction):
    gate.LOG.add("s1", "terminal", "sudo -n installer", "sudo: a password is required")
    gate.LOG.set_users("s1", ["Installation is already authorized"])
    stub = llm_returning([("unverified", "authentication is not missing authorization",
                           "use run_in_user_terminal for OS authentication")])
    gate.LLM = stub
    result = call(gate, human(action=action, interaction=interaction,
                              evidence="terminal sudo -n installer: a password is required"))
    assert result["action"] == "block"
    assert action in stub.seen[0][1]
    assert "Installation is already authorized" in stub.seen[0][1]


def test_ordinary_clarification_final_still_uses_existing_gate(gate):
    stub = llm_returning([("ok", "genuine choice")])
    gate.LLM = stub
    assert gate.pre_final_gate(session_id="s1", platform="tui", final_response="Should I choose blue or red?") is None
    assert len(stub.seen) == 1
    assert "explicit human_request" not in stub.seen[0][0]


def test_null_request_is_operational_not_malformed(gate):
    assert gate.pre_tool_call(tool_name="kanban_block", session_id="s1",
                              args={"human_request": None, "reason": "sudo -n failed"}) is None
