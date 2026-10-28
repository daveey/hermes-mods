"""core-update backend: status derivation from state.json + git, and the restart hand-off."""
from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location("core_update_core", Path(__file__).resolve().parents[1] / "core.py")
core = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(core)


def _git(root, *args):
    return subprocess.run(["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@e.invalid", *args],
                          check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "checkout"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "commit", "-q", "--allow-empty", "-m", "running")
    running = _git(root, "rev-parse", "HEAD")
    _git(root, "commit", "-q", "--allow-empty", "-m", "next-1")
    _git(root, "commit", "-q", "--allow-empty", "-m", "next-2")
    ahead = _git(root, "rev-parse", "HEAD")
    _git(root, "checkout", "-q", running)
    home = tmp_path / "home"
    (home / "cache/core-update").mkdir(parents=True)
    app = tmp_path / "Hermes.app"
    app.mkdir()
    return root, home, running, ahead, app


def _write(home, **state):
    (home / "cache/core-update/state.json").write_text(json.dumps(state))


def test_ready_when_a_newer_build_exists(repo):
    root, home, _running, ahead, app = repo
    _write(home, status="ready", sha=ahead, app_path=str(app), waiter_cmd=["true"])
    s = core.status(home, checkout=root)
    assert s["state"] == "ready" and s["commits"] == 2 and s["can_restart"]


def test_ready_for_the_newer_head_an_older_build_serves(repo):
    """The updater keeps a build of next-1 for next-2 when next-2 changes no desktop input."""
    root, home, running, ahead, app = repo
    older = _git(root, "rev-parse", f"{ahead}~1")
    _write(home, status="ready", sha=older, serves_sha=ahead, app_path=str(app), waiter_cmd=["true"])
    s = core.status(home, checkout=root)
    assert s["state"] == "ready" and s["sha"] == ahead and s["commits"] == 2
    _write(home, status="ready", sha=running, serves_sha=ahead, app_path=str(app), waiter_cmd=["true"])
    assert core.status(home, checkout=root)["state"] == "ready"  # built from what runs, serving next-2
    _write(home, status="building", sha=older, serves_sha=ahead, pid=1, updated_at="2099-01-01T00:00:00+00:00")
    assert core.status(home, checkout=root)["sha"] == older  # only a ready build serves another head


def test_building_and_failed_are_reported_for_newer_shas(repo):
    root, home, _running, ahead, _app = repo
    _write(home, status="building", sha=ahead, pid=1, updated_at="2099-01-01T00:00:00+00:00")
    assert core.status(home, checkout=root)["state"] == "building"
    _write(home, status="failed", sha=ahead, error="boom")
    assert core.status(home, checkout=root)["state"] == "failed"


def test_idle_when_the_build_is_what_already_runs_or_missing(repo):
    root, home, running, ahead, app = repo
    _write(home, status="ready", sha=running, app_path=str(app), waiter_cmd=["true"])
    assert core.status(home, checkout=root)["state"] == "idle"
    _write(home, status="ready", sha=ahead, app_path=str(app.parent / "gone.app"), waiter_cmd=["true"])
    assert core.status(home, checkout=root)["state"] == "idle"
    (home / "cache/core-update/state.json").unlink()
    assert core.status(home, checkout=root)["state"] == "idle"


def test_idle_when_the_checkout_already_moved_past_the_build(repo):
    root, home, running, ahead, app = repo
    _write(home, status="ready", sha=running, app_path=str(app), waiter_cmd=["true"])
    _git(root, "checkout", "-q", ahead)
    assert core.status(home, checkout=root)["state"] == "idle"


def test_restart_starts_the_waiter_then_quits_desktop_detached(repo):
    root, home, _running, ahead, app = repo
    _write(home, status="ready", sha=ahead, app_path=str(app), waiter_cmd=["/usr/bin/python3", "sync", "core-waiter"])
    spawned = []
    r = core.restart(home, checkout=root, popen=lambda cmd, **kw: spawned.append((cmd, kw)))
    assert r == {"ok": True, "sha": ahead}
    assert spawned[0][0] == ["/usr/bin/python3", "sync", "core-waiter"]
    assert "osascript" in spawned[1][0][-1] and core.DESKTOP_BUNDLE_ID in spawned[1][0][-1]
    assert all(kw["start_new_session"] for _cmd, kw in spawned)


def test_restart_refuses_without_a_ready_update(repo):
    root, home, _running, ahead, _app = repo
    _write(home, status="building", sha=ahead, pid=1, updated_at="2099-01-01T00:00:00+00:00")
    spawned = []
    r = core.restart(home, checkout=root, popen=lambda cmd, **kw: spawned.append(cmd))
    assert r["ok"] is False and spawned == []
