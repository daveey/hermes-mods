"""Exercise the state-merge PR-view callsite, not only the probe helper."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def state_merge_probe(monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location(
        "ship_state_merge_budget_guard", Path(__file__).with_name("guard.py")
    )
    guard = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(guard)
    # Keep the fake clock ahead of the real clock so a bypass cannot expire by accident.
    now = guard.time.monotonic() + 1_000_000
    monkeypatch.setattr(guard, "time", SimpleNamespace(monotonic=lambda: now))
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        # Stop after the PR-view probe, before unrelated review/base-drift probes.
        return SimpleNamespace(returncode=1, stdout="", stderr="fixture view unavailable")

    monkeypatch.setattr(guard, "subprocess", SimpleNamespace(
        run=run, SubprocessError=guard.subprocess.SubprocessError,
        TimeoutExpired=guard.subprocess.TimeoutExpired,
    ))
    env = {"GH_PROMPT_DISABLED": "0"}

    def merge():
        return guard._state_merge(
            ["pr", "merge", "1", "--repo", "o/r", "--rebase",
             "--match-head-commit", "a" * 40],
            "o/r", str(tmp_path), env,
        )

    return guard, now, calls, merge, tmp_path, env


@pytest.mark.parametrize("remaining", [0, -1])
def test_state_merge_expired_deadline_never_spawns(state_merge_probe, remaining):
    guard, now, calls, merge, _, _ = state_merge_probe
    token = guard._PREFLIGHT_DEADLINE.set(now + remaining)
    try:
        with pytest.raises(guard._PreflightExpired, match="deadline exceeded"):
            merge()
        assert calls == []
    finally:
        guard._PREFLIGHT_DEADLINE.reset(token)


@pytest.mark.parametrize("remaining, expected_timeout", [(1, 1), (30, 15), (None, 15)])
def test_state_merge_view_timeout_obeys_remaining_budget(
    state_merge_probe, remaining, expected_timeout,
):
    guard, now, calls, merge, cwd, env = state_merge_probe
    deadline = None if remaining is None else now + remaining
    token = guard._PREFLIGHT_DEADLINE.set(deadline)
    try:
        refusal = merge()
        assert "`gh pr view 1 --repo o/r` exited 1 (fixture view unavailable)" in refusal
        assert len(calls) == 1
        argv, kwargs = calls[0]
        assert argv[:7] == ["gh", "pr", "view", "1", "--repo", "o/r", "--json"]
        assert "headRefOid" in argv[7].split(",")
        assert kwargs == {
            "cwd": str(cwd), "env": {"GH_PROMPT_DISABLED": "1"},
            "capture_output": True, "text": True, "timeout": expected_timeout,
        }
        assert env == {"GH_PROMPT_DISABLED": "0"}
    finally:
        guard._PREFLIGHT_DEADLINE.reset(token)
