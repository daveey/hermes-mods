"""Pin the gh-text callsite's aggregate budget, not just late rejection."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def guard():
    spec = importlib.util.spec_from_file_location(
        "ship_budget_gh_text_guard", Path(__file__).with_name("guard.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def probe(guard, monkeypatch):
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=0, stdout="response\n")

    monkeypatch.setattr(guard.subprocess, "run", run)
    # A module-local clock avoids changing pytest's own monotonic clock.
    monkeypatch.setattr(guard, "time", SimpleNamespace(monotonic=lambda: 100.0))
    return calls


def test_gh_text_expired_budget_does_not_spawn(guard, probe, tmp_path):
    token = guard._PREFLIGHT_DEADLINE.set(99.0)
    try:
        with pytest.raises(guard._PreflightExpired, match="deadline exceeded"):
            guard._gh_text("p", tmp_path, {})
        assert probe == []
    finally:
        guard._PREFLIGHT_DEADLINE.reset(token)


def test_gh_text_short_budget_clips_subprocess_timeout(guard, probe, tmp_path):
    token = guard._PREFLIGHT_DEADLINE.set(101.0)
    try:
        assert guard._gh_text("p", tmp_path, {}) == "response\n"
        assert len(probe) == 1
        argv, kwargs = probe[0]
        assert argv == ["gh", "api", "p"]
        assert 0 < kwargs["timeout"] <= 1
    finally:
        guard._PREFLIGHT_DEADLINE.reset(token)


def test_gh_text_without_deadline_keeps_native_timeout(guard, probe, tmp_path):
    env = {"FIXTURE": "value"}
    assert guard._PREFLIGHT_DEADLINE.get() is None
    assert guard._gh_text("p", tmp_path, env) == "response\n"
    assert len(probe) == 1
    argv, kwargs = probe[0]
    assert argv == ["gh", "api", "p"]
    assert kwargs["timeout"] == 15
    assert Path(kwargs["cwd"]) == tmp_path
    assert kwargs["capture_output"] is True
    assert kwargs["text"] is True
    assert kwargs["env"] == {"FIXTURE": "value", "GH_PROMPT_DISABLED": "1"}
    assert env == {"FIXTURE": "value"}
