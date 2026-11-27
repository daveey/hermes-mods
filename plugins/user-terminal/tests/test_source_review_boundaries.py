"""Source-review D1/D2/D3 regressions; no GUI, courier, or live home access."""
import importlib.util
import json
import os
from pathlib import Path
import stat

import pytest


@pytest.fixture
def ut(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("HERMES_USER_TERMINAL_DIR", str(tmp_path / "jobs"))
    spec = importlib.util.spec_from_file_location("source_review_subject", Path(__file__).parents[1] / "__init__.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "_session_env", lambda key: "")
    monkeypatch.setattr(mod, "_owner_slack_dm", lambda: False)
    return mod


@pytest.mark.parametrize("action", ["start", "focus", "enter", "withdraw", "resume"])
@pytest.mark.parametrize("surfaces", [["slack"], [], ["desktop", "slack"], ["cron"]])
def test_local_owner_gate_precedes_all_job_operations(ut, monkeypatch, action, surfaces):
    monkeypatch.setattr(ut, "caller_surfaces", lambda sid: surfaces)
    calls = []
    def touched(*args, **kwargs):
        calls.append(True)
        raise AssertionError("job access before owner authorization")
    monkeypatch.setattr(ut, "jobs_dir", touched)
    args = {"action": action, "command": "true", "job_id": "known", "cwd": "/"}
    out = json.loads(ut.handle(args, session_id="non-owner"))
    assert "refused" in out.get("error", ""), out
    assert calls == []


@pytest.mark.parametrize("surface", sorted(["desktop", "api_server", "tui", "cli", "local"]))
def test_local_owner_surface_still_reaches_handler(ut, monkeypatch, surface):
    monkeypatch.setattr(ut, "caller_surfaces", lambda sid: [surface])
    monkeypatch.setattr(ut, "sweep_jobs", lambda **kw: None)
    out = json.loads(ut.handle({}, session_id="owner"))
    assert out["error"] == "command is required (or job_id to resume waiting)"


@pytest.mark.parametrize("mask", [0, 0o022, 0o077])
def test_job_directory_and_initial_files_have_explicit_private_modes(ut, tmp_path, mask):
    old = os.umask(mask)
    try:
        job = ut.create_job("true", "private reason", str(tmp_path),
                            origin={"gateway": "example", "title": "fixture"},
                            confirm=True, owner={"kind": "session", "session_id": "fixture"})
    finally:
        os.umask(old)
    assert stat.S_IMODE(job.stat().st_mode) == 0o700
    files = list(job.iterdir())
    assert {"cmd.sh", "runner.sh", "watchdog.sh", "meta.json", "owner.json", "confirm"} <= {p.name for p in files}
    assert {p.name: stat.S_IMODE(p.stat().st_mode) for p in files} == {p.name: 0o600 for p in files}


@pytest.mark.parametrize("key", ["HERMES_KANBAN_TASK", "HERMES_KANBAN_WORKER_TASK"])
@pytest.mark.parametrize("action", ["start", "resume", "focus"])
def test_local_card_worker_real_environment_reaches_handler(ut, monkeypatch, key, action):
    # Exercise caller_surfaces, not a stub; mimic a dispatcher-spawned worker.
    for name in ("HERMES_PLATFORM", "HERMES_SESSION_PLATFORM", "HERMES_SESSION_SOURCE",
                 "HERMES_KANBAN_TASK", "HERMES_KANBAN_WORKER_TASK"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HERMES_SESSION_SOURCE", "kanban")
    monkeypatch.setenv(key, "t_fixture")
    monkeypatch.setattr(ut, "_session_env", lambda name: os.environ.get(name, ""))
    monkeypatch.setattr(ut, "sweep_jobs", lambda **kw: None)
    args = {} if action == "start" else {"action": action, "job_id": "missing"}
    out = json.loads(ut.handle(args))
    assert "refused" not in out.get("error", ""), out
    assert "required" in out["error"] or "unknown job_id" in out["error"]


@pytest.mark.parametrize("source,platform,task", [
    ("kanban", "", ""), ("kanban", "slack", "t_fixture"),
    ("slack", "cli", "t_fixture"), ("", "", "t_fixture"),
    ("cron", "", "t_fixture"),
])
def test_local_worker_identity_never_overrides_other_or_unknown_surfaces(ut, monkeypatch, source, platform, task):
    monkeypatch.setenv("HERMES_SESSION_SOURCE", source)
    monkeypatch.setenv("HERMES_SESSION_PLATFORM", "")
    monkeypatch.setenv("HERMES_PLATFORM", platform)
    monkeypatch.setenv("HERMES_KANBAN_TASK", task)
    monkeypatch.delenv("HERMES_KANBAN_WORKER_TASK", raising=False)
    monkeypatch.setattr(ut, "_session_env", lambda name: os.environ.get(name, ""))
    assert "refused" in json.loads(ut.handle({}))["error"]


def test_card_worker_does_not_gain_remote_mac_access(ut, monkeypatch):
    monkeypatch.setenv("HERMES_KANBAN_TASK", "t_fixture")
    monkeypatch.setattr(ut, "caller_surfaces", lambda sid: ["kanban"])
    assert "refused" in json.loads(ut.handle_remote({}))["error"]


def test_job_modes_are_private_at_creation_and_exclusive(ut, tmp_path, monkeypatch):
    mkdir, open_file = Path.mkdir, os.open
    dirs, files = [], []
    def record_mkdir(path, mode=0o777, **kw):
        dirs.append((path, mode))
        return mkdir(path, mode=mode, **kw)
    def record_open(path, flags, mode=0o777, **kw):
        files.append((Path(path), flags, mode))
        return open_file(path, flags, mode, **kw)
    monkeypatch.setattr(Path, "mkdir", record_mkdir)
    monkeypatch.setattr(os, "open", record_open)
    job = ut.create_job("true", "private", str(tmp_path), confirm=True,
                        owner={"kind": "session", "session_id": "fixture"})
    assert (job, 0o700) in dirs
    initial = [(p, flags, mode) for p, flags, mode in files if p.parent == job]
    assert {p.name for p, _, _ in initial} == {p.name for p in job.iterdir()}
    assert all(mode == 0o600 and flags & os.O_EXCL and flags & os.O_CREAT
               for _, flags, mode in initial)


def test_take_back_never_executes_sibling_plugin(ut, tmp_path, monkeypatch):
    plugin = tmp_path / "plugins" / "user-terminal"
    sibling = plugin.parent / "session-mentions"
    plugin.mkdir(parents=True)
    sibling.mkdir()
    sentinel = tmp_path / "foreign-executed"
    (sibling / "relay_files.py").write_text(
        f"from pathlib import Path\nPath({str(sentinel)!r}).touch()\n"
        "def take_back(path):\n    return False\n")
    monkeypatch.setattr(ut, "__file__", str(plugin / "__init__.py"))
    request = tmp_path / "request.json"
    request.write_text("{}")
    assert ut._take_back(request) is True
    assert not sentinel.exists()
    assert not request.exists()
    assert not request.with_name(".request.taken").exists()
    assert ut._take_back(request) is False
