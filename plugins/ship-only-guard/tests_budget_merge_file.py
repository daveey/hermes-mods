"""The real merge-file call must enforce the budget before spawning git."""
import hashlib
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


def load_guard():
    spec = importlib.util.spec_from_file_location(
        "ship_merge_file_budget_guard", Path(__file__).with_name("guard.py")
    )
    guard = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(guard)
    return guard


def test_merge_file_expired_deadline_never_spawns(monkeypatch, tmp_path):
    guard = load_guard()
    monkeypatch.setattr(guard.time, "monotonic", lambda: 100.0)
    calls = []

    def run(*args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=0, stdout=b"merged\n")

    monkeypatch.setattr(guard.subprocess, "run", run)
    token = guard._PREFLIGHT_DEADLINE.set(100.0)
    try:
        # Call the consumer directly: a callback's final deadline check is too
        # late to prevent an over-budget subprocess from being started.
        with pytest.raises(guard._PreflightExpired, match="deadline exceeded"):
            guard._merge_file(b"a", b"b", b"c", str(tmp_path))
        assert calls == []
    finally:
        guard._PREFLIGHT_DEADLINE.reset(token)


def test_merge_file_clips_native_timeout_to_remaining_budget(monkeypatch, tmp_path):
    guard = load_guard()
    monkeypatch.setattr(guard.time, "monotonic", lambda: 100.0)
    calls = []
    merged = b"merged\n"

    def run(argv, **kwargs):
        calls.append(kwargs)
        assert argv[:4] == ["git", "merge-file", "-p", "-q"]
        assert [Path(name).read_bytes() for name in argv[4:]] == [b"a", b"b", b"c"]
        assert all(Path(name).parent == Path(kwargs["cwd"]) for name in argv[4:])
        assert kwargs["capture_output"] is True
        return SimpleNamespace(returncode=0, stdout=merged)

    monkeypatch.setattr(guard.subprocess, "run", run)
    token = guard._PREFLIGHT_DEADLINE.set(101.0)
    try:
        result = guard._merge_file(b"a", b"b", b"c", str(tmp_path))
    finally:
        guard._PREFLIGHT_DEADLINE.reset(token)
    assert result == hashlib.sha1(b"blob %d\0" % len(merged) + merged).hexdigest()
    assert len(calls) == 1
    assert 0 < calls[0]["timeout"] <= 1.0
