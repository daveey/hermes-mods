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
def repo(tmp_path, monkeypatch):
    monkeypatch.setattr(core.waiter, "attested_source", lambda: b"# attested fixture\n")
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
    path = home / "cache/core-update/state.json"
    path.write_text(json.dumps(state))
    path.chmod(0o600)



@pytest.mark.parametrize("terminal_state", ["ready", "failed"])
def test_same_checkout_build_lifecycle_until_bundle_is_installed(repo, terminal_state):
    root, home, old, _, app = repo
    source = root / "apps/desktop/source.js"
    source.parent.mkdir(parents=True)
    source.write_text("new desktop")
    _git(root, "add", "apps/desktop/source.js")
    _git(root, "commit", "-qm", "desktop change")
    target = _git(root, "rev-parse", "HEAD")
    stamp = root / "apps/desktop/release/mac-arm64/Hermes.app/Contents/Resources/install-stamp.json"
    stamp.parent.mkdir(parents=True)
    stamp.write_text(json.dumps({"commit": old}))
    for phase in ("building", terminal_state):
        _write(home, status=phase, sha=target, app_path=str(app), waiter_cmd=["true"],
               updated_at="2099-01-01T00:00:00+00:00",
               error="pack failed" if phase == "failed" else None)
        s = core.status(home, checkout=root)
        assert s["state"] == phase
        assert s["can_restart"] == (phase == "ready")

        if phase != "ready":
            assert not core.restart(home, checkout=root, popen=lambda *a, **kw: pytest.fail("early quit"))["ok"]
        else:
            calls = []
            assert core.restart(home, checkout=root, popen=lambda *a, **kw: calls.append(a))["ok"]
            assert len(calls) == 2
    stamp.write_text(json.dumps({"commit": target}))
    s = core.status(home, checkout=root)
    assert s["state"] == "idle" and not s["can_restart"]
    assert not core.restart(home, checkout=root, popen=lambda *a, **kw: pytest.fail("already installed"))["ok"]
    # An older installed bundle can serve a newer backend-only head too.
    _git(root, "commit", "-q", "--allow-empty", "-m", "backend only")
    newer = _git(root, "rev-parse", "HEAD")
    _write(home, status="ready", sha=target, serves_sha=newer, app_path=str(app), waiter_cmd=["true"])
    assert core.status(home, checkout=root)["state"] == "idle"


@pytest.mark.parametrize("stamp_content", [None, "broken", '{"commit": "short"}'])
def test_unknown_installed_identity_never_offers_same_head_restart(repo, stamp_content):
    root, home, running, _, app = repo
    if stamp_content is not None:
        stamp = root / "apps/desktop/release/mac/Hermes.app/Contents/Resources/install-stamp.json"
        stamp.parent.mkdir(parents=True)
        stamp.write_text(stamp_content)
    _write(home, status="ready", sha=running, app_path=str(app), waiter_cmd=["true"])
    s = core.status(home, checkout=root)
    assert not s["can_restart"] and s["desktop_swap_pending"] is None
    assert "identity unavailable" in s["reason"]
    _write(home, status="failed", sha=running, error="pack failed")
    s = core.status(home, checkout=root)
    assert s["state"] == "failed" and s["error"] == "pack failed"



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
    assert spawned[0][0] == ["/usr/bin/python3", "-I", str(Path(core.__file__).resolve().with_name("waiter.py"))]
    assert "osascript" in spawned[1][0][-1] and core.DESKTOP_BUNDLE_ID in spawned[1][0][-1]
    assert all(kw["start_new_session"] for _cmd, kw in spawned)


def test_restart_refuses_without_a_ready_update(repo):
    root, home, _running, ahead, _app = repo
    _write(home, status="building", sha=ahead, pid=1, updated_at="2099-01-01T00:00:00+00:00")
    spawned = []
    r = core.restart(home, checkout=root, popen=lambda cmd, **kw: spawned.append(cmd))
    assert r["ok"] is False and spawned == []
