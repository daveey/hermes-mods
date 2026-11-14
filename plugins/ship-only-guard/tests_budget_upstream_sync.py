"""The upstream-sync exception must bound its own PR probe before spawning."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def upstream_probe(monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location(
        "ship_budget_upstream_sync_guard", Path(__file__).with_name("guard.py")
    )
    guard = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(guard)
    now = guard.time.monotonic() + 1_000_000
    monkeypatch.setattr(guard.time, "monotonic", lambda: now)
    sha = "a" * 40
    words = ["pr", "merge", "1", "--repo", "o/r", "--merge",
             "--match-head-commit", sha]
    payload = {
        "number": 1, "url": "https://github.com/o/r/pull/1", "state": "OPEN",
        "baseRefName": "main", "headRefName": "sync/upstream-" + sha,
        "headRefOid": sha, "isCrossRepository": False,
        "statusCheckRollup": [{"__typename": "CheckRun",
                               "name": "All required checks pass (v2)",
                               "status": "COMPLETED", "conclusion": "SUCCESS"}],
    }
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=0, stdout=json.dumps(payload))

    monkeypatch.setattr(guard.subprocess, "run", run)

    def invoke():
        return guard._upstream_sync_merge(words, "o/r", str(tmp_path), {})

    return guard, now, calls, invoke


@pytest.mark.parametrize("remaining", [0, -1])
def test_upstream_sync_expired_deadline_never_spawns(upstream_probe, remaining):
    guard, now, calls, invoke = upstream_probe
    token = guard._PREFLIGHT_DEADLINE.set(now + remaining)
    try:
        # A late refusal by bounded_preflight cannot undo an already-started probe.
        with pytest.raises(guard._PreflightExpired, match="deadline exceeded"):
            invoke()
        assert calls == []
    finally:
        guard._PREFLIGHT_DEADLINE.reset(token)


@pytest.mark.parametrize("remaining, expected_timeout", [(1, 1), (30, 15), (None, 15)])
def test_upstream_sync_probe_timeout_respects_remaining_budget(
    upstream_probe, remaining, expected_timeout
):
    guard, now, calls, invoke = upstream_probe
    token = guard._PREFLIGHT_DEADLINE.set(None if remaining is None else now + remaining)
    try:
        assert invoke() is True
        assert len(calls) == 1
        argv, kwargs = calls[0]
        assert argv[:7] == ["gh", "pr", "view", "1", "--repo", "o/r", "--json"]
        assert kwargs["env"]["GH_PROMPT_DISABLED"] == "1"
        assert kwargs["timeout"] == expected_timeout
    finally:
        guard._PREFLIGHT_DEADLINE.reset(token)
