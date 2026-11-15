"""Exercise attribution's real subprocess boundary under a shared deadline."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def attribution(monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location(
        "ship_attribute_budget_guard", Path(__file__).with_name("guard.py")
    )
    guard = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(guard)
    clock = [guard.time.monotonic() + 1_000_000]
    monkeypatch.setattr(guard.time, "monotonic", lambda: clock[0])
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=0, stdout=json.dumps([{"sha": "abc123"}]))

    monkeypatch.setattr(guard.subprocess, "run", run)
    token = guard._PREFLIGHT_DEADLINE.set(None)
    try:
        yield guard, clock, calls, tmp_path
    finally:
        guard._PREFLIGHT_DEADLINE.reset(token)


def test_expired_attribution_never_spawns(attribution):
    guard, clock, calls, cwd = attribution
    guard._PREFLIGHT_DEADLINE.set(clock[0])
    with pytest.raises(guard._PreflightExpired, match="deadline exceeded"):
        guard._attribute_hits("o/r", ["a"], {}, lambda c: "", cwd, {})
    assert calls == []


def test_attribution_clips_native_timeout_to_one_second(attribution):
    guard, clock, calls, cwd = attribution
    guard._PREFLIGHT_DEADLINE.set(clock[0] + 1)
    assert guard._attribute_hits("o/r", ["a"], {}, lambda c: "", cwd, {}) == ""
    assert len(calls) == 1
    assert calls[0][1]["timeout"] == 1
    assert calls[0][0] == ["gh", "api", "repos/o/r/commits?sha=state&path=a&per_page=5"]


def test_attribution_multiple_hits_share_decreasing_budget(attribution, monkeypatch):
    guard, clock, calls, cwd = attribution
    guard._PREFLIGHT_DEADLINE.set(clock[0] + 1)

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        clock[0] += 0.25
        return SimpleNamespace(returncode=0, stdout='[{"sha": "abc123"}]')

    monkeypatch.setattr(guard.subprocess, "run", run)
    hit = {"sha": "abc123"}
    result = guard._attribute_hits(
        "o/r", ["a", "b", "c"], {"abc123": hit}, lambda c: "subject", cwd, {}
    )
    assert result == "a: abc123 subject; b: abc123 subject; c: abc123 subject"
    assert [kwargs["timeout"] for _, kwargs in calls] == [1, 0.75, 0.5]


def test_attribution_expiry_stops_later_hits(attribution, monkeypatch):
    guard, clock, calls, cwd = attribution
    guard._PREFLIGHT_DEADLINE.set(clock[0] + 1)

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        clock[0] += 1
        raise guard.subprocess.TimeoutExpired(argv, kwargs["timeout"])

    monkeypatch.setattr(guard.subprocess, "run", run)
    with pytest.raises(guard._PreflightExpired, match="deadline exceeded"):
        guard._attribute_hits("o/r", ["a", "b"], {}, lambda c: "", cwd, {})
    assert len(calls) == 1
    assert calls[0][1]["timeout"] == 1


@pytest.mark.parametrize("remaining", [None, 20])
def test_attribution_preserves_native_timeout_and_results(attribution, remaining):
    guard, clock, calls, cwd = attribution
    guard._PREFLIGHT_DEADLINE.set(None if remaining is None else clock[0] + remaining)
    hit = {"sha": "abc123"}
    env = {"ATTRIBUTION_FIXTURE": "yes"}
    result = guard._attribute_hits("o/r", ["a"], {"abc123": hit}, lambda c: "subject", cwd, env)
    assert result == "a: abc123 subject"
    assert len(calls) == 1
    assert calls[0][1]["timeout"] == 8
    assert calls[0][1]["env"] == {**env, "GH_PROMPT_DISABLED": "1"}
    assert env == {"ATTRIBUTION_FIXTURE": "yes"}
