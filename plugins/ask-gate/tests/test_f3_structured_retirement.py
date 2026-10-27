"""Retired answers cannot escape through structured rather than final asks."""
import pytest

ASK = "Could you approve the Example deployment?"


@pytest.mark.parametrize("tool,args", [
    ("clarify", {"questions": [{"question": ASK}]}),
    ("kanban_block", {"kind": "needs_input", "reason": ASK}),
    ("kanban_create", {"initial_status": "blocked", "title": "Deployment", "body": ASK}),
])
def test_retired_answer_blocks_structured_surface(gate, monkeypatch, tool, args):
    gate.ledger().add(ASK, "The step was completed.", session="origin")
    monkeypatch.setattr(gate, "run_judge", lambda *a, **k: pytest.fail("retirement precedes judgment"))
    result = gate.pre_tool_call(tool_name=tool, args=args, session_id="origin")
    assert result and result["action"] == "block"
    assert "retired" in result["message"].lower()


def test_retired_structured_action_checks_full_human_request(gate, monkeypatch):
    gate.ledger().add(ASK, "The step was completed.", session="origin")
    request = dict(action=ASK, why_human="Human choice", interaction="approval",
                   scope="Example deployment", evidence="No fresh evidence")
    result = gate.pre_tool_call(tool_name="kanban_block", session_id="origin",
                               args={"kind": "needs_input", "reason": "Decision", "human_request": request})
    assert result and result["action"] == "block"
    assert "retired" in result["message"].lower()


def test_unknown_retirement_source_holds_ask_but_not_read(gate, monkeypatch):
    def unavailable():
        raise OSError("private storage failure")
    monkeypatch.setattr(gate, "ledger", unavailable)
    result = gate.pre_tool_call(tool_name="clarify", args={"questions": [{"question": ASK}]}, session_id="origin")
    assert result and result["action"] == "block"
    assert "private storage failure" not in result["message"]
    assert gate.pre_tool_call(tool_name="read_file", args={"path": "report.txt"}, session_id="origin") is None


def test_retirement_is_not_global_and_operational_parking_survives(gate):
    gate.ledger().add(ASK, "The step was completed.", session="origin")
    assert gate.pre_tool_call(tool_name="clarify", args={"questions": [{"question": ASK}]}, session_id="other") is None
    assert gate.pre_tool_call(tool_name="kanban_block", args={"kind": "transient", "reason": ASK}, session_id="origin") is None
