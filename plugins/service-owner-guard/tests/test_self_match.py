"""Inert regressions: dangerous commands are strings; no process is signalled."""
import importlib.util
from pathlib import Path
from unittest import mock

import pytest


@pytest.fixture
def guard():
    spec = importlib.util.spec_from_file_location(
        "self_match_fixture", Path(__file__).resolve().parents[1] / "__init__.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # No process discovery, SSH, owner state or logs are needed for these cases.
    with mock.patch.object(module, "_lookup", return_value={"status": "none"}), \
         mock.patch.object(module, "_log"), \
         mock.patch.object(module.subprocess, "run", side_effect=AssertionError("live subprocess")), \
         mock.patch.dict(module.os.environ, {"SERVICE_OWNER_GUARD": "on"}):
        yield module


PATTERN = "fixture-tests/.venv/bin/python -m pytest"
COMMAND = f'cd /work/fixture-tests && pkill -f "{PATTERN}"; echo restart'


def test_terminal_incident_blocks_before_owner_lookup(guard):
    with mock.patch.object(guard, "_lookup", side_effect=AssertionError("owner lookup")):
        result = guard._pre_tool_call("terminal", {"command": COMMAND})
    assert result["action"] == "block"
    assert "own shell" in result["message"]
    assert "process_manage" in result["message"]


@pytest.mark.parametrize("prefix", ["", "nice ", "timeout 20 ", "exec ", "/usr/bin/"])
@pytest.mark.parametrize("flags", ["-f", "--full", "-TERM -f", "-15 -f", "-f --"])
def test_supported_shell_forms(guard, prefix, flags):
    command = f'{prefix}pkill {flags} "{PATTERN}"'
    assert guard._pre_tool_call("terminal", {"command": command})["action"] == "block"


@pytest.mark.parametrize("source", [
    f"terminal(command={COMMAND!r}, background=True)",
    f"terminal({COMMAND!r})",
    f"tools.terminal(command={COMMAND!r})",
])
def test_execute_code_literal_terminal_command(guard, source):
    result = guard._pre_tool_call("execute_code", {"code": source})
    assert result["action"] == "block"
    assert "own shell" in result["message"]


def test_nested_tool_call(guard):
    result = guard._pre_tool_call("tool_call", {"calls": [
        {"name": "terminal", "arguments": {"command": COMMAND}}
    ]})
    assert result["action"] == "block"


@pytest.mark.parametrize("command", [
    "kill -TERM 424242",                         # exact PID cleanup remains available
    "kill -0 424242",                            # existence probe
    "pkill -f '^/work/fixture-tests/.venv/bin/python -m pytest'",  # anchored, no shell match
    "pkill -f '[p]ytest'",                        # non-self-matching expression
    "pkill -x pytest",                           # process-name match
    "pkill -P 424242 -f pytest",                  # narrowing flags outside this check
    "pkill -0 -f pytest",                        # no signal
    "pkill --signal=0 -f pytest",
    "pkill --signal 0 -f pytest",
    "pkill -f '" + "x" * 513 + "'",           # bounded pattern evaluation
    "printf '%s' 'pkill -f pytest'",              # inert text
    "echo safe # pkill -f pytest",               # comment
    "ssh example.com 'pkill -f pytest'",          # remote shell not claimed
])
def test_preserves_non_target_operations(guard, command):
    assert guard._pre_tool_call("terminal", {"command": command}) is None


@pytest.mark.parametrize("source", [
    f"example = {COMMAND!r}",                     # inert Python string
    "subprocess.run(['pkill', '-f', 'pytest'])",   # argv, not shell command
    "terminal(command=generated_command)",       # dynamic source not claimed
    "not valid python!",                         # unsupported source
])
def test_execute_code_non_targets(guard, source):
    assert guard._pre_tool_call("execute_code", {"code": source}) is None


def test_existing_opt_out(guard):
    with mock.patch.dict(guard.os.environ, {"SERVICE_OWNER_GUARD": "off"}):
        assert guard._pre_tool_call("terminal", {"command": COMMAND}) is None


def test_probe_does_not_hide_later_kill(guard):
    result = guard._pre_tool_call("terminal", {"command": "pkill -0 -f pytest; pkill -f pytest"})
    assert result["action"] == "block"


def test_shell_wrapper(guard):
    import shlex
    result = guard._pre_tool_call("terminal", {"command": "bash -c " + shlex.quote(COMMAND)})
    assert result["action"] == "block"
