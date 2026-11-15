"""The nested merge-tree git runner must spend the aggregate deadline."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def merge_probe(monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location(
        "merge_tree_budget_guard", Path(__file__).with_name("guard.py"))
    guard = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(guard)
    clock = [guard.time.monotonic() + 10**6]
    monkeypatch.setattr(guard.time, "monotonic", lambda: clock[0])
    merge, ours, theirs, base, tree = (c * 40 for c in "abcde")
    calls = []
    elapsed = [0.125]
    outputs = {"init": "", "fetch": "", "show": f"{ours} {theirs}",
               "merge-base": base, "merge-tree": tree, "rev-parse": tree}

    def run(argv, **kwargs):
        command = argv[5]
        assert command in outputs
        assert Path(kwargs["cwd"]).parent == tmp_path / "cache"
        calls.append((command, kwargs["timeout"]))
        clock[0] += elapsed[0]
        return SimpleNamespace(returncode=0, stdout=outputs[command])

    monkeypatch.setattr(guard.subprocess, "run", run)

    def check():
        return guard._git_merge_tree_refusal(
            merge, ours, theirs, "example/repository", str(tmp_path),
            {"HERMES_HOME": str(tmp_path)})

    return guard, clock, calls, elapsed, check


def test_expired_merge_tree_budget_never_spawns(merge_probe):
    guard, clock, calls, _, check = merge_probe
    token = guard._PREFLIGHT_DEADLINE.set(clock[0])
    try:
        assert "deadline exceeded" in guard.bounded_preflight(check)
        assert calls == []  # A late outer refusal alone cannot prevent an overrun.
    finally:
        guard._PREFLIGHT_DEADLINE.reset(token)


def test_merge_tree_clips_every_sequential_git_timeout(merge_probe):
    guard, clock, calls, _, check = merge_probe
    token = guard._PREFLIGHT_DEADLINE.set(clock[0] + 1)
    try:
        assert guard.bounded_preflight(check) == ""
        assert calls == [("init", 1.0), ("fetch", 0.875), ("show", 0.75),
                         ("merge-base", 0.625), ("merge-tree", 0.5),
                         ("rev-parse", 0.375)]
    finally:
        guard._PREFLIGHT_DEADLINE.reset(token)


def test_merge_tree_stops_spawning_when_previous_git_exhausts_budget(merge_probe):
    guard, clock, calls, elapsed, check = merge_probe
    elapsed[0] = 1
    token = guard._PREFLIGHT_DEADLINE.set(clock[0] + 1)
    try:
        assert "deadline exceeded" in guard.bounded_preflight(check)
        assert calls == [("init", 1.0)]
    finally:
        guard._PREFLIGHT_DEADLINE.reset(token)


def test_merge_tree_keeps_native_timeout_without_aggregate_deadline(merge_probe):
    guard, _, calls, _, check = merge_probe
    assert guard._PREFLIGHT_DEADLINE.get() is None
    assert check() == ""
    assert calls == [(command, 30) for command in
                     ("init", "fetch", "show", "merge-base", "merge-tree", "rev-parse")]
