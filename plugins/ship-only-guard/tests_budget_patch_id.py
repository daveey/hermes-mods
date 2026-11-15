"""The patch-id subprocess must share the aggregate preflight deadline."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


@pytest.fixture
def guard():
    spec = importlib.util.spec_from_file_location(
        "ship_patch_id_budget_guard", Path(__file__).with_name("guard.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("remaining", [0.0, -1.0])
def test_patch_id_expired_deadline_does_not_spawn(guard, monkeypatch, tmp_path, remaining):
    monkeypatch.setattr(guard.time, "monotonic", lambda: 100.0)
    run = Mock(return_value=SimpleNamespace(returncode=0, stdout="a" * 40 + "\n"))
    monkeypatch.setattr(guard.subprocess, "run", run)
    token = guard._PREFLIGHT_DEADLINE.set(100.0 + remaining)
    try:
        with pytest.raises(guard._PreflightExpired, match="deadline exceeded"):
            guard._patch_id("diff x", tmp_path)
        run.assert_not_called()
    finally:
        guard._PREFLIGHT_DEADLINE.reset(token)


@pytest.mark.parametrize("remaining, expected_timeout", [(1.0, 1.0), (30.0, 15.0), (None, 15.0)])
def test_patch_id_clips_timeout_to_remaining_budget(
    guard, monkeypatch, tmp_path, remaining, expected_timeout
):
    monkeypatch.setattr(guard.time, "monotonic", lambda: 100.0)
    run = Mock(return_value=SimpleNamespace(returncode=0, stdout="a" * 40 + "\n"))
    monkeypatch.setattr(guard.subprocess, "run", run)
    token = guard._PREFLIGHT_DEADLINE.set(None if remaining is None else 100.0 + remaining)
    try:
        assert guard._patch_id("diff x", tmp_path) == "a" * 40
        run.assert_called_once_with(
            ["git", "patch-id", "--verbatim"], input="diff x", cwd=tmp_path,
            capture_output=True, text=True, timeout=expected_timeout,
        )
    finally:
        guard._PREFLIGHT_DEADLINE.reset(token)
