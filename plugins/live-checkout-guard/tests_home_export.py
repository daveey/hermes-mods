"""A throwaway HERMES_HOME must not persist in the terminal session (2026-11-02 hermes-sync incident).

Run: scripts/test-all -k live-checkout-guard
"""
import importlib.util
import json
import os
import subprocess
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("home_export_guard_plugin", Path(__file__).with_name("__init__.py"))
assert spec is not None and spec.loader is not None
plugin = importlib.util.module_from_spec(spec)
spec.loader.exec_module(plugin)

BLOCK = [
    # the incident's exact call (coach compute tests, 2026-11-02 17:47:57 PST)
    'export HERMES_HOME=$(mktemp -d "$TMPDIR/coach-home.XXXX"); cd plugins/coach && "$PY" -m pytest -q',
    "export HERMES_HOME=$(mktemp -d)",
    "export HERMES_HOME=\"$(mktemp -d)\"",
    "export HERMES_HOME=`mktemp -d`",
    "export HERMES_HOME=/tmp/h",
    "export HERMES_HOME=/private/var/folders/dj/x/T/coach-home.L7Nk",
    "export HERMES_HOME=$TMPDIR/h",
    "export HERMES_HOME=${TMPDIR}/h",
    "export PY=python3 HERMES_HOME=/tmp/h",
    "export -n X; export HERMES_HOME=/var/tmp/x",
    "declare -x HERMES_HOME=/tmp/h",
    "typeset -x HERMES_HOME=/tmp/h",
    "HERMES_HOME=$(mktemp -d)",  # assignment-only: an inherited export stays exported
    "HERMES_HOME=$(mktemp -d); hermes-sync run",
    "HERMES_HOME=/tmp/h && pytest -q",
    "A=1 HERMES_HOME=/tmp/h",
    "cd /x\nexport HERMES_HOME=$(mktemp -d)\npytest",
    "{ export HERMES_HOME=/tmp/h; pytest; }",
    "if true; then export HERMES_HOME=/tmp/h; fi",
    "for i in 1; do export HERMES_HOME=/tmp/h; done",
    "pytest || export HERMES_HOME=/tmp/h",
    "cat <<EOF > /var/tmp/note\ntext\nEOF\nexport HERMES_HOME=/tmp/h",
]
ALLOW = [
    'HERMES_HOME=$(mktemp -d "$TMPDIR/coach-home.XXXX") python -m pytest -q',  # one-command prefix form
    "HERMES_HOME=/tmp/h pytest -q && echo done",
    "A=1 HERMES_HOME=/tmp/h B=2 pytest",
    "env HERMES_HOME=/tmp/h pytest",
    "( export HERMES_HOME=$(mktemp -d); pytest -q )",
    "(cd x && export HERMES_HOME=/tmp/h && pytest)",
    "bash -c 'export HERMES_HOME=/tmp/h; pytest'",
    "ssh host 'export HERMES_HOME=/tmp/h; hermes doctor'",
    "out=$(export HERMES_HOME=/tmp/h; hermes config get x)",
    "echo 'export HERMES_HOME=/tmp/h'",
    'grep -n "export HERMES_HOME=/tmp" notes.md',
    "# export HERMES_HOME=/tmp/h\nls",
    "cat <<'EOF' > script.sh\nexport HERMES_HOME=/tmp/h\nEOF",
    "bash <<'EOF'\nexport HERMES_HOME=$(mktemp -d)\npytest\nEOF",
    "export HERMES_HOME=$HOME/.hermes",
    "export HERMES_HOME=~/.hermes/profiles/coach",
    "unset HERMES_HOME",
    "export HERMES_HOME_BACKUP=/tmp/h",
    "export MY_HERMES_HOME=/tmp/h",
]


def _args(tool, command):
    if tool == "terminal":
        return {"command": command}
    return {"code": f"from hermes_tools import terminal\nterminal({command!r})"}


@pytest.mark.parametrize("tool", ["terminal", "execute_code"])
@pytest.mark.parametrize("command", BLOCK)
def test_persistent_temp_home_refused(tool, command):
    result = plugin._pre_tool_call(tool, _args(tool, command))
    assert result and result["action"] == "block", command
    for text in ("throwaway HERMES_HOME", "keeps exported variables between calls", "hermes-sync",
                 'HERMES_HOME="$(mktemp -d)" python -m pytest', "( export HERMES_HOME", "unset HERMES_HOME"):
        assert text in result["message"]


@pytest.mark.parametrize("tool", ["terminal", "execute_code"])
@pytest.mark.parametrize("command", ALLOW)
def test_scoped_or_canonical_home_allowed(tool, command):
    assert plugin._pre_tool_call(tool, _args(tool, command)) is None, command


def test_mcp_and_batched_terminal():
    cmd = "export HERMES_HOME=$(mktemp -d)"
    assert plugin._pre_tool_call("mcp__terminal", {"command": cmd})["action"] == "block"
    batch = {"calls": [{"name": "terminal", "arguments": {"command": cmd}}]}
    assert plugin._pre_tool_call("tool_call", batch)["action"] == "block"


def test_only_the_terminal_tool_persists():
    """execute_code's own subprocess shells and a process' stdin end with that process; nothing carries over."""
    code = "import subprocess\nsubprocess.run('export HERMES_HOME=/tmp/h; pytest', shell=True)"
    assert plugin._pre_tool_call("execute_code", {"code": code}) is None
    assert plugin._pre_tool_call("process", {"action": "submit", "data": "export HERMES_HOME=/tmp/h"}) is None


def test_kill_switch(monkeypatch):
    monkeypatch.setenv("LIVE_CHECKOUT_GUARD", "0")
    assert plugin._pre_tool_call("terminal", {"command": "export HERMES_HOME=/tmp/h"}) is None


def _core_python():
    cand = [os.environ.get("HERMES_CORE_PYTHON", "")]
    cand += sorted(str(p) for p in Path.home().glob(".hermes/installs/*/environments/*/venv/bin/python"))
    cand += [str(Path.home() / ".hermes/hermes-agent/.venv/bin/python")]
    core = Path(os.environ.get("HERMES_AGENT_DIR", str(Path.home() / ".hermes/hermes-agent")))
    for py in filter(None, dict.fromkeys(cand)):
        if not Path(py).exists():
            continue
        ok = subprocess.run([py, "-c", "import sys; sys.path.insert(0, sys.argv[1]); import tools.terminal_tool",
                             str(core)], capture_output=True, timeout=120)
        if ok.returncode == 0:
            return py, core
    return None, core


def test_real_core_two_calls(tmp_path):
    """Two calls through the real core: without the plugin the export from call 1 reaches call 2 (the incident's
    mechanism); with it call 1 is refused and call 2 still sees the session's own HERMES_HOME."""
    py, core = _core_python()
    if not py:
        pytest.skip("no Hermes runtime python that can import tools.terminal_tool (set HERMES_CORE_PYTHON)")
    home = tmp_path / "hermes-home"
    home.mkdir()
    env = {k: v for k, v in os.environ.items() if k not in ("HERMES_HOME", "LIVE_CHECKOUT_GUARD")}
    env.update(HERMES_HOME=str(home), TMPDIR=str(tmp_path), TERMINAL_CWD=str(tmp_path))

    def probe(register):
        p = subprocess.run([py, str(Path(__file__).with_name("_home_export_probe.py")), str(core), register],
                           capture_output=True, text=True, env=env, timeout=240, cwd=tmp_path)
        assert p.returncode == 0, p.stderr[-3000:]
        return json.loads(p.stdout.strip().splitlines()[-1])

    bare = probe("0")
    assert not bare["one"]["blocked"] and "coach-home." in bare["two"]["output"], bare
    out = probe("1")
    assert out["one"]["blocked"] and "throwaway HERMES_HOME" in out["one"]["message"], out
    assert not out["two"]["blocked"] and f"HOME_SEEN={home}" in out["two"]["output"], out  # the session's own home
    assert "coach-home." not in out["two"]["output"], out
