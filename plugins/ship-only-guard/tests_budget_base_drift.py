"""Base-drift compare must obey the preflight deadline before spawning."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def guard():
    spec = importlib.util.spec_from_file_location(
        "ship_base_drift_budget_guard", Path(__file__).with_name("guard.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def compare_probe(guard, monkeypatch):
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=0, stdout=json.dumps({
            "status": "identical", "behind_by": 0,
            "total_commits": 0, "commits": [], "files": [],
        }))

    monkeypatch.setattr(guard.subprocess, "run", run)
    monkeypatch.setattr(guard.time, "monotonic", lambda: 1_000_000_000.0)
    return calls


def compare(guard, tmp_path):
    return guard._base_drift_refusal(12, "example/project", {
        "headRefOid": "a" * 40,
        "baseRefOid": "b" * 40,
        "files": [{"path": "home/scripts/example.py"}],
    }, str(tmp_path), {})


def test_base_drift_expired_deadline_never_spawns(guard, compare_probe, tmp_path):
    token = guard._PREFLIGHT_DEADLINE.set(999_999_999.0)
    try:
        with pytest.raises(guard._PreflightExpired, match="deadline exceeded"):
            compare(guard, tmp_path)
    finally:
        guard._PREFLIGHT_DEADLINE.reset(token)
        assert compare_probe == [], "expired base-drift compare spawned a subprocess"


@pytest.mark.parametrize("remaining, expected_timeout", [(1.0, 1.0), (30.0, 15.0)])
def test_base_drift_clips_compare_timeout(
    guard, compare_probe, tmp_path, remaining, expected_timeout
):
    token = guard._PREFLIGHT_DEADLINE.set(1_000_000_000.0 + remaining)
    try:
        assert compare(guard, tmp_path) is None
    finally:
        guard._PREFLIGHT_DEADLINE.reset(token)
    assert len(compare_probe) == 1
    argv, kwargs = compare_probe[0]
    assert argv == ["gh", "api", f"repos/example/project/compare/{'b' * 40}...state"]
    assert kwargs["timeout"] == expected_timeout
    assert kwargs["env"]["GH_PROMPT_DISABLED"] == "1"
