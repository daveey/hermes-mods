"""Result-only evidence must survive missing assistant call rows."""
import pytest

from test_status_fresh import ASK, DRAFT, _events, _hooks, _turn, rc


def _calls(count):
    return {"role": "assistant", "content": "", "tool_calls": [
        {"id": f"read-{i}", "type": "function",
         "function": {"name": "kanban_show", "arguments": "{}"}}
        for i in range(count)
    ]}


def _results(count):
    return [{"role": "tool", "tool_call_id": f"read-{i}",
             "content": '{"status": "running"}'} for i in range(count)]


def _armed_gate(tmp_path, monkeypatch):
    monkeypatch.setenv("COACH_STATUS_FRESH", "on")
    monkeypatch.setenv("COACH_STATUS_PLAIN", "on")
    hooks, log = _hooks(tmp_path, monkeypatch)
    hint = hooks["pre_llm_call"](session_id="result-evidence", user_message=ASK)
    assert hint and "live state" in hint["context"]
    gate = hooks["pre_final_gate:status_fresh"]
    assert gate.needs_buffer_for_session(session_id="result-evidence") is True
    return gate, log


def _check_gate(gate, rows):
    return gate(session_id="result-evidence", platform="desktop", final_response=DRAFT,
                turn_messages=_turn(ASK, *rows, {"role": "assistant", "content": DRAFT}), attempt=0)


@pytest.mark.parametrize("result_count", [1, 2, 3])
def test_tool_result_alone_passes_gate(tmp_path, monkeypatch, result_count):
    gate, log = _armed_gate(tmp_path, monkeypatch)
    # No assistant tool_calls row remains, but the read results are fresh evidence.
    assert _check_gate(gate, _results(result_count)) is None
    events = _events(log, "status_fresh")
    assert len(events) == 1
    assert events[0]["action"] == "pass"
    assert events[0]["tools"] == result_count


@pytest.mark.parametrize("calls,results,expected", [(1, 1, 1), (2, 1, 2), (1, 3, 3), (2, 0, 2)])
def test_call_and_result_accounting_uses_max_not_sum(tmp_path, monkeypatch, calls, results, expected):
    rows = [_calls(calls), *_results(results)]
    assert rc.turn_tool_calls(rows) == expected
    gate, log = _armed_gate(tmp_path, monkeypatch)
    assert _check_gate(gate, rows) is None
    events = _events(log, "status_fresh")
    assert len(events) == 1
    assert events[0]["action"] == "pass"
    assert events[0]["tools"] == expected


def test_zero_evidence_still_holds_status_draft(tmp_path, monkeypatch):
    rows = [_calls(0), {"role": "assistant", "content": "I checked the board."}]
    assert rc.turn_tool_calls(rows) == 0
    gate, log = _armed_gate(tmp_path, monkeypatch)
    result = _check_gate(gate, rows)
    assert result and result["hold"] is True
    assert "status_fresh" in result["nudge"]
    events = _events(log, "status_fresh")
    assert len(events) == 1
    assert events[0]["action"] == "hold"
    assert events[0]["tools"] == 0
