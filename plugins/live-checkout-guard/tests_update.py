"""Core updates must use the host's fenced route, not an agent shell."""
import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("update_guard_plugin", Path(__file__).with_name("__init__.py"))
assert spec is not None and spec.loader is not None
plugin = importlib.util.module_from_spec(spec)
spec.loader.exec_module(plugin)

BLOCK = [
    "hermes -p work update --yes", "hermes --profile=work update",
    "python -mhermes_cli.main update", "python3.14t -m hermes_cli.main update",
    "ssh host -t 'hermes update'", "ssh -tp 22 host 'hermes update'",
    "ssh -P tag host 'hermes update'", "ssh host -- hermes update",
    "{ hermes update; }", "if true; then hermes update; fi",
    "for x in 1; do hermes update; done", "! hermes update",
    "echo hi # note << EOF\nhermes update",
    "x=$((1 << 2))\nhermes update",
    "ssh host <<'EOF'\nhermes update\nEOF",
    "bash <<'EOF'\nhermes update\nEOF",
    "bash <<< 'hermes update'", "ssh host <<< 'hermes update'",

    "hermes update", "hermes update --yes",
    "ssh ubuntu@hermes-hub 'hermes update --yes'",
    "ssh ubuntu@hermes-hub '~/.local/bin/hermes update --yes'",
    "~/.local/bin/hermes update --no-gateway-restart",
    "/opt/venv/bin/hermes update -y", "python -m hermes_cli.main update",
    "/opt/venv/bin/python3.14 -m hermes_cli.main update --yes",
    "ssh -o ConnectTimeout=1 guard-probe.invalid 'hermes update --yes'",
    "ssh -i key -p 22 host 'python3 -m hermes_cli.main update'",
    "ssh host hermes update --yes", "bash -lc 'hermes update --yes'",
    "env X=1 timeout 3 hermes update", "echo HERMES_UPDATE_OK=1; hermes update",
    "HERMES_UPDATE_OK=1 echo ok; hermes update", "HERMES_UPDATE_OK=0 hermes update",
    "hermes update --help; hermes update --yes",
    "cat <<'EOF' > /var/tmp/note\nhermes update\nEOF\nhermes update --yes",
]
ALLOW = [
    "echo 'text\nhermes update --yes\ntext'",
    "HERMES_UPDATE_OK=1 ssh host <<'EOF'\nhermes update\nEOF",
    "HERMES_UPDATE_OK=1 bash <<< 'hermes update'",
    *[f"hermes update {flag}" for flag in ("-h", "--help", "--check", "--plan", "--list-venv-holders", "--install-id")],
    "ssh host '~/.local/bin/hermes update --check'",
    "python3 -m hermes_cli.main update --plan",
    "grep 'hermes update --yes' log", "rg 'hermes update' .",
    '''sqlite3 state.db "SELECT * FROM messages WHERE content LIKE '%hermes update%'"''',
    "echo 'hermes update --yes' > /var/tmp/note", "echo hermes update --yes",
    "cat <<'EOF' > /var/tmp/note\nhermes update --yes\nEOF",
    "cat <<EOF > /var/tmp/note\nhermes update --yes\nEOF",
    "HERMES_UPDATE_OK=1 hermes update --yes",
    "HERMES_UPDATE_OK=1 ssh host 'hermes update --yes'",
    "ssh host 'HERMES_UPDATE_OK=1 ~/.local/bin/hermes update --yes'",
    "env HERMES_UPDATE_OK=1 /opt/venv/bin/hermes update",
    "HERMES_UPDATE_OK=1 bash -lc 'hermes update'",
    "ssh host \"grep 'hermes update' log\"",
]

@pytest.mark.parametrize("tool", ["terminal", "execute_code"])
@pytest.mark.parametrize("command", BLOCK)
def test_update_refused(tool, command):
    args = {"command": command} if tool == "terminal" else {"code": f"from hermes_tools import terminal\nterminal({command!r})"}
    result = plugin._pre_tool_call(tool, args)
    assert result and result["action"] == "block"
    for text in ("hermes-core-update.timer", "drain-first", "batched", "hermes-sync", "wake check", "merge-base --is-ancestor", "HERMES_UPDATE_OK=1", "user-approved"):
        assert text in result["message"]

@pytest.mark.parametrize("tool", ["terminal", "execute_code"])
@pytest.mark.parametrize("command", ALLOW)
def test_update_allowed(tool, command):
    args = {"command": command} if tool == "terminal" else {"code": f"terminal({command!r})"}
    assert plugin._pre_tool_call(tool, args) is None


def test_python_data_not_execution():
    assert plugin._pre_tool_call("execute_code", {"code": 'print("hermes update --yes")'}) is None


def test_python_subprocess_argv():
    result = plugin._pre_tool_call("execute_code", {"code": "subprocess.run(['ssh', 'host', 'hermes update --yes'])"})
    assert result and result["action"] == "block"
