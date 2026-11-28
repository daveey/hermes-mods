"""Hub update contract against disposable Git and the real core force writer."""
import importlib.util
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

BASE = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location("test_hub_" + name, BASE / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), "-c", "user.name=fixture", "-c",
                           "user.email=fixture@example.com", *args], check=True,
                          capture_output=True, text=True).stdout.strip()


def test_version_returns_only_running_to_target_newest_first(tmp_path, monkeypatch):
    force = pytest.importorskip("hermes_cli.core_update_force")
    progress = pytest.importorskip("hermes_cli.core_update_progress")
    module = load("version")
    core = load("core")
    monkeypatch.setattr(module, "_core", lambda: core)
    monkeypatch.setattr(force, "running_turn_count", lambda root: 2)
    monkeypatch.setattr(force, "read_force_request", lambda root: None)
    monkeypatch.setattr(progress, "read_progress", lambda: {"state": "blocked", "waiting_on": ["drain cooldown"]})
    git(tmp_path, "init", "-q")
    git(tmp_path, "commit", "--allow-empty", "-qm", "running")
    running = git(tmp_path, "rev-parse", "HEAD")
    git(tmp_path, "commit", "--allow-empty", "-qm", "first change")
    first = git(tmp_path, "rev-parse", "HEAD")
    git(tmp_path, "commit", "--allow-empty", "-qm", "newest change")
    target = git(tmp_path, "rev-parse", "HEAD")
    git(tmp_path, "update-ref", "refs/remotes/origin/main", target)
    git(tmp_path, "checkout", "--detach", running)
    home = tmp_path / "home"
    result = module.view(home, tmp_path)
    assert result["running"] == running and result["target"] == target
    assert [c["sha"] for c in result["changes"]] == [target, first]
    assert [c["subject"] for c in result["changes"]] == ["newest change", "first change"]
    assert all(set(c) == {"sha", "subject", "at"} and "T" in c["at"] for c in result["changes"])
    assert result["running_turns"] == 2
    assert result["deferral_reason"] == "drain cooldown"


def test_update_writes_standard_force_even_without_pending_build(tmp_path, monkeypatch):
    force = pytest.importorskip("hermes_cli.core_update_force")
    module = load("version")
    core = load("core")
    monkeypatch.setattr(core.waiter, "attested_source", lambda: b"fixture")
    monkeypatch.setattr(module, "_core", lambda: core)
    profile = tmp_path / "profiles/work"
    calls = []
    result = module.update_now(profile, popen=lambda *a, **kw: calls.append((a, kw)))
    assert result["ok"]
    assert force.read_force_request(tmp_path)["by"] == "hub panel"
    assert not (profile / "core-update/force-request").exists()
    assert len(calls) == 1 and calls[0][1]["start_new_session"]
    assert calls[0][0][0][-1] == str(BASE / "update.py")


def test_unattested_updater_writes_no_force_and_spawns_nothing(tmp_path, monkeypatch):
    module = load("version")
    core = load("core")
    def refuse():
        raise ValueError("unsafe updater")
    monkeypatch.setattr(core.waiter, "attested_source", refuse)
    monkeypatch.setattr(module, "_core", lambda: core)
    with pytest.raises(ValueError, match="unsafe updater"):
        module.update_now(tmp_path, popen=lambda *a, **kw: pytest.fail("spawned"))
    assert not (tmp_path / "core-update/force-request").exists()


@pytest.mark.parametrize("pending,state,can_restart,expected", [
    (True, "ready", True, True), (False, "ready", True, False),
    (None, "ready", True, False), (True, "building", False, False),
    (True, "ready", False, False),
])
def test_quit_only_for_ready_changed_bundle(pending, state, can_restart, expected):
    module = load("update")
    core = SimpleNamespace(running_checkout=lambda: Path("fixture"), _git=lambda *a: "a" * 40,
                           _desktop_swap_pending=lambda *a: pending)
    assert bool(module.needs_quit(core, {"state": state, "can_restart": can_restart, "sha": "a" * 40})) is expected


@pytest.mark.parametrize("swap", [True, False, None])
def test_monitor_runs_attested_update_and_only_restarts_for_swap(tmp_path, monkeypatch, swap):
    module = load("update")
    core = load("core")
    monkeypatch.setattr(core, "hermes_home", lambda: tmp_path)
    monkeypatch.setattr(core, "running_checkout", lambda: tmp_path / "checkout")
    monkeypatch.setattr(core, "_git", lambda *a: "a" * 40)
    monkeypatch.setattr(core, "_desktop_swap_pending", lambda *a: swap)
    monkeypatch.setattr(core, "status", lambda: {"state": "ready", "can_restart": True,
                                               "sha": "a" * 40, "running": "b" * 40})
    restarts = []
    monkeypatch.setattr(core, "restart", lambda: restarts.append(True) or {"ok": True})
    runs = []
    module.monitor(core, run=lambda argv, **kw: runs.append(argv),
                   wait=lambda _: pytest.fail("unexpected wait"))
    assert len(runs) == 1 and runs[0][-1] == "--update"
    assert restarts == ([True] if swap else [])
    result = json.loads((core.state_dir(tmp_path) / "hub-update.json").read_text())
    assert result["phase"] == ("restarting" if swap else "updated")


def test_monitor_publishes_actual_runner_failure(tmp_path, monkeypatch):
    module = load("update")
    core = load("core")
    monkeypatch.setattr(core, "hermes_home", lambda: tmp_path)
    def refuse(*a, **kw):
        raise RuntimeError("attested updater refused")
    module.monitor(core, run=refuse)
    result = json.loads((core.state_dir(tmp_path) / "hub-update.json").read_text())
    assert result["phase"] == "failed" and result["reason"] == "attested updater refused"


def test_quit_rejects_stale_ready_target():
    module = load("update")
    core = SimpleNamespace(running_checkout=lambda: Path("fixture"), _git=lambda *a: "b" * 40,
                           _desktop_swap_pending=lambda *a: True)
    assert not module.needs_quit(core, {"state": "ready", "can_restart": True, "sha": "a" * 40})
