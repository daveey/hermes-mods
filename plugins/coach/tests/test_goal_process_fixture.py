"""Safety invariants of the production process-ownership acceptance fixture."""
import json
import shlex
import subprocess
import sys

import pytest

from test_goal_process_serve import FixtureFrames, _coach_diagnostics, _fixture_python
from test_goal_disposition_transport import Frames


def test_stable_python_alias_preserves_interpreter_identity(tmp_path, monkeypatch):
    stamped = tmp_path / "python-3.14.7+2026090901-linux-x64"
    stamped.symlink_to(sys.executable)
    monkeypatch.setattr(sys, "executable", str(stamped))
    alias = _fixture_python(tmp_path / "home")
    assert alias == tmp_path / "home" / "bin" / "python"
    assert alias.is_symlink()
    assert alias.samefile(stamped)
    command = "exec " + shlex.quote(str(alias)) + " -c " + shlex.quote("print('fixture-ok')")
    assert shlex.split(command) == ["exec", str(alias), "-c", "print('fixture-ok')"]
    assert stamped.name not in command
    result = subprocess.run(command, shell=True, check=True, text=True, capture_output=True)
    assert result.stdout.strip() == "fixture-ok"


@pytest.mark.parametrize("matches", [True, False])
def test_approval_still_requires_exact_command(monkeypatch, matches):
    command = "exec /fixture/bin/python -c 'print(1)'"
    frame = dict(method="approval", id="approval-fixture",
                 params=dict(command=command if matches else command + "; false"))
    monkeypatch.setattr(Frames, "receive", lambda self: frame)
    sent = []
    frames = object.__new__(FixtureFrames)
    frames.commands = [command]
    frames.ws = type("Socket", (), {"send": lambda self, value: sent.append(json.loads(value))})()
    if matches:
        assert frames.receive() == frame
        assert sent == [dict(jsonrpc="2.0", id="approval-fixture", result=dict(choice="once"))]
    else:
        with pytest.raises(AssertionError):
            frames.receive()
        assert sent == []


@pytest.mark.parametrize("log_state", ["missing", "unreadable", "present"])
def test_coach_diagnostics_preserve_original_failure(tmp_path, monkeypatch, log_state):
    log = tmp_path / "coach" / "log.jsonl"
    if log_state != "missing":
        log.parent.mkdir()
        log.write_text('{"disposition":"owned_continuing"}\n')
    if log_state == "unreadable":
        original_read = type(log).read_text

        def read(path, *args, **kwargs):
            if path == log:
                raise PermissionError("fixture denied")
            return original_read(path, *args, **kwargs)

        monkeypatch.setattr(type(log), "read_text", read)
    diagnostic = ""
    with pytest.raises(AssertionError, match="original approval failure"):
        try:
            raise AssertionError("original approval failure")
        finally:
            diagnostic = _coach_diagnostics(tmp_path)
    if log_state == "present":
        assert diagnostic == log.read_text()
    else:
        assert diagnostic.startswith("Coach log unavailable:")
