"""Regression tests for the terminal-runtime-hint hook."""
import importlib.util
import json
import sys
from pathlib import Path


HERE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("terminal_runtime_hint_test", HERE / "__init__.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
sys.modules.setdefault("terminal_runtime_hint_test", mod)
hint = mod


TRACEBACK = "Traceback (most recent call last):\nModuleNotFoundError: No module named 'ruamel'"


def terminal_result(output=TRACEBACK, exit_code=1):
    return json.dumps({"output": output, "exit_code": exit_code})


def test_failed_python_terminal_result_explains_managed_interpreter():
    out = hint.transform_tool_result(
        tool_name="terminal",
        args={"command": "python3 -B -m unittest discover -s tools"},
        result=terminal_result(),
    )
    data = json.loads(out)
    assert data["output"] == TRACEBACK
    assert data["exit_code"] == 1
    assert data["hint"].startswith(hint.MARK)
    assert ".hermes/hermes-agent/.venv/bin/python" in data["hint"]
    assert "Do not install ruamel" in data["hint"]


def test_hint_is_idempotent_and_preserves_tool_result():
    first = hint.transform_tool_result(
        tool_name="terminal", args={"command": "python3 script.py"}, result=terminal_result()
    )
    assert hint.transform_tool_result(
        tool_name="terminal", args={"command": "python3 script.py"}, result=first
    ) is None


def test_unrelated_terminal_output_is_unchanged():
    for args, result in [
        ({"command": "python3 script.py"}, terminal_result(exit_code=0)),
        ({"command": "grep ruamel log.txt"}, terminal_result()),
        ({"command": "python3 script.py"}, terminal_result("ModuleNotFoundError: No module named 'yaml'")),
        ({"command": "python3 script.py"}, "not-json"),
    ]:
        assert hint.transform_tool_result(tool_name="terminal", args=args, result=result) is None


def test_other_tools_are_not_annotated():
    assert hint.transform_tool_result(
        tool_name="execute_code", args={"command": "python3 script.py"}, result=terminal_result()
    ) is None
