"""Credit-error hint (3 strings only) and the once-per-session hold on an ask to buy API credit."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _load import load  # noqa: E402

# Verbatim: the hub card t_01a18622e706e70a's kanban_block reason (blocker anthropic-openai-api-credits, 10-29).
REAL_BLOCK = ("Add API credit; the eval can't run without it. 1) Open https://console.anthropic.com/settings/billing "
              "(the org behind the broker's anthropic.inference key) and add credits. It returns 400 \"Your credit "
              "balance is too low\". 2) Open https://platform.openai.com/settings/organization/billing (the org behind "
              "openai.inference) and add credits. It returns 429 \"You have no credits remaining\".")


def fresh():
    return load("hooks")


def test_hint_fires_on_each_of_the_three_credit_errors():
    h = fresh()
    for out in ("anthropic.BadRequestError: Error code: 400 - Your credit balance is too low to access the API",
                "openai.RateLimitError: Error code: 429 - You have no credits remaining",
                "{'error': {'code': 'insufficient_quota', 'type': 'insufficient_quota'}}"):
        res = h.transform_terminal_output(command="python run.py", output=out)
        assert res and res.startswith(out) and "hermes-pool-llm" in res, out


def test_hint_skips_unrelated_output_and_is_idempotent():
    h = fresh()
    assert h.transform_terminal_output(command="ls", output="Traceback: 429 Too Many Requests") is None
    assert h.transform_terminal_output(command="ls", output="all good") is None
    once = h.transform_terminal_output(command="x", output="no credits remaining")
    assert h.transform_terminal_output(command="x", output=once) is None


def test_hint_not_added_to_the_helpers_own_output():
    h = fresh()
    assert h.transform_terminal_output(command="hermes-pool-llm check", output="no credits remaining") is None


def test_tool_result_hint_for_execute_code_only():
    h = fresh()
    res = h.transform_tool_result(tool_name="execute_code", args={"code": "x"},
                                  result=json.dumps({"output": "insufficient_quota"}))
    assert res and "hermes-pool-llm" in res
    assert h.transform_tool_result(tool_name="read_file", args={"path": "skill.md"},
                                   result="pitfall: credit balance is too low") is None


def test_real_credit_block_is_held_once_then_allowed():
    h = fresh()
    args = {"reason": REAL_BLOCK, "kind": "needs_input"}
    first = h.pre_tool_call(tool_name="kanban_block", args=args, session_id="s1")
    assert first and first["action"] == "block" and "pool" in first["message"]
    assert h.pre_tool_call(tool_name="kanban_block", args=args, session_id="s1") is None


def test_credit_block_allowed_when_the_session_tried_the_pool():
    h = fresh()
    h.pre_tool_call(tool_name="terminal", args={"command": "hermes-pool-llm check"}, session_id="s2")
    assert h.pre_tool_call(tool_name="kanban_block", args={"reason": REAL_BLOCK}, session_id="s2") is None


def test_credit_ask_through_tool_call_bridge_and_blocked_create():
    h = fresh()
    bridged = {"calls": [{"name": "kanban_create", "arguments": {
        "title": "Add API credit", "body": "Add about $250 to each.", "initial_status": "blocked"}}]}
    assert h.pre_tool_call(tool_name="tool_call", args=bridged, session_id="s3")["action"] == "block"
    # a normal (not blocked) card about credits is work, not an ask
    assert h.credit_ask("kanban_create", {"title": "check API credit usage", "body": "add credits report"}) is None


def test_unrelated_block_passes():
    h = fresh()
    args = {"reason": "1) Open https://github.com/settings/billing and fix the Actions payment failure"}
    assert h.pre_tool_call(tool_name="kanban_block", args=args, session_id="s4") is None
    assert h.pre_tool_call(tool_name="kanban_block", args={"reason": "needs a passkey tap"}, session_id="s4") is None
