"""State is advisory data, never authority to select executable code."""
import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

spec = importlib.util.spec_from_file_location("restart_security_core", Path(__file__).resolve().parents[1] / "core.py")
core = importlib.util.module_from_spec(spec)
spec.loader.exec_module(core)


@pytest.fixture
def ready(tmp_path, monkeypatch):
    home = tmp_path / "home"
    directory = home / "cache/core-update"
    directory.mkdir(parents=True)
    app = tmp_path / "Hermes.app"
    app.mkdir()
    path = directory / "state.json"
    path.write_text(json.dumps(dict(status="ready", sha="b" * 40, app_path=str(app),
                                    waiter_cmd=["/bin/sh", "-c", "touch injected"])))
    path.chmod(0o600)
    # The independent attestation boundary is exercised separately. Here isolate
    # state authority from presence/absence of a host's reference updater.
    if hasattr(core, "waiter"):
        monkeypatch.setattr(core.waiter, "attested_source", lambda: b"# attested fixture\n")
    def git(root, *args):
        if args == ("rev-parse", "HEAD"):
            return "a" * 40
        if args[0] == "rev-list":
            return "1"
        return ""
    return home, path, git


def restart(ready):
    home, path, git = ready
    calls = []
    result = core.restart(home, checkout=home, git=git,
                          popen=lambda cmd, **kw: calls.append(cmd))
    return result, calls


def test_state_argv_is_never_executed(ready):
    result, calls = restart(ready)
    assert result["ok"]
    assert calls[0] != ["/bin/sh", "-c", "touch injected"]
    assert "injected" not in " ".join(calls[0])
    assert Path(calls[0][-1]).resolve() == Path(core.__file__).resolve().with_name("waiter.py")


def test_restart_reads_state_exactly_once(ready, monkeypatch):
    _, path, _ = ready
    count = []
    original = json.loads
    def loads(data, *args, **kwargs):
        value = original(data, *args, **kwargs)
        if isinstance(value, dict) and "waiter_cmd" in value:
            count.append(value)
        return value
    monkeypatch.setattr(json, "loads", loads)
    result, _ = restart(ready)
    assert result["ok"]
    assert len(count) == 1


@pytest.mark.parametrize("mode", [0o620, 0o602, 0o666])
def test_restart_refuses_writable_state(ready, mode):
    ready[1].chmod(mode)
    result, calls = restart(ready)
    assert not result["ok"] and not calls


def test_restart_refuses_foreign_owner(ready, monkeypatch):
    real = os.fstat
    def foreign(fd):
        st = real(fd)
        values = list(st)
        values[4] = os.getuid() + 1
        return os.stat_result(values)
    monkeypatch.setattr(os, "fstat", foreign)
    result, calls = restart(ready)
    assert not result["ok"] and not calls


def test_restart_refuses_state_symlink(ready):
    _, path, _ = ready
    target = path.with_name("other.json")
    path.rename(target)
    path.symlink_to(target)
    result, calls = restart(ready)
    assert not result["ok"] and not calls


def test_restart_refuses_nonregular_state(ready):
    ready[1].unlink()
    os.mkfifo(ready[1])
    result, calls = restart(ready)
    assert not result["ok"] and not calls


@pytest.mark.parametrize("content", [None, "broken", "[]"])
def test_restart_refuses_missing_or_invalid_state(ready, content):
    if content is None:
        ready[1].unlink()
    else:
        ready[1].write_text(content)
    result, calls = restart(ready)
    assert not result["ok"] and not calls


@pytest.mark.parametrize("error", [ValueError("unattested"), OSError("unavailable")])
def test_restart_refuses_unattested_updater(ready, monkeypatch, error):
    def refuse():
        raise error
    monkeypatch.setattr(core.waiter, "attested_source", refuse)
    result, calls = restart(ready)
    assert not result["ok"] and not calls


def test_restart_does_not_require_legacy_command(ready):
    data = json.loads(ready[1].read_text())
    del data["waiter_cmd"]
    ready[1].write_text(json.dumps(data))
    result, calls = restart(ready)
    assert result["ok"] and calls

