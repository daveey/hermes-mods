"""Acquisition must recheck its write reserve after the tree read; inert transport only."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize(
    "own_read_seconds,tree_read_seconds,expected_tree_reads,expected_writes",
    [(0, 0, 1, 2), (9, 0, 0, 0), (0, 8, 1, 2), (0, 9, 1, 0)],
    ids=["no-time-consumed", "early-reserve-refusal", "exact-reserve", "tree-eats-reserve"],
)
def test_acquisition_rechecks_write_reserve_after_tree_read(
    monkeypatch, own_read_seconds, tree_read_seconds, expected_tree_reads, expected_writes
):
    spec = importlib.util.spec_from_file_location("reserve_guard", Path(__file__).with_name("guard.py"))
    guard = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(guard)
    clock = [0.0]
    reads, writes = [], []
    head, tree, made = "a" * 40, "c" * 40, "b" * 40
    prefix = "repos/example/repo/git/"
    monkeypatch.setattr(guard, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr(guard, "_now", lambda: 1800000000)
    monkeypatch.setattr(guard, "PREFLIGHT_TIMEOUT", 18.0)
    monkeypatch.setattr(guard, "_turn_hold_refusal", lambda *args: None)

    def transport(args, cwd, env, *, timeout, payload=None):
        endpoint, = args
        if payload is not None:
            assert endpoint in (prefix + "commits", prefix + "refs")
            writes.append((clock[0], endpoint))
            return 0, {"sha": made}
        reads.append(endpoint)
        if endpoint == prefix + "ref/land-turn/pr-12":
            clock[0] += own_read_seconds
            return 1, None
        assert endpoint == prefix + "commits/" + head
        clock[0] += tree_read_seconds
        return 0, {"tree": {"sha": tree}}

    # No subprocess, GitHub mutation, or production landing can escape this fake.
    monkeypatch.setattr(guard, "_gh_call", transport)
    result = guard._turn_acquire(
        12, {"headRefOid": head, "files": [{"path": "example.py"}]},
        "example/repo", None, {},
    )
    assert reads.count(prefix + "commits/" + head) == expected_tree_reads
    assert len(writes) == expected_writes, writes
    assert all(start <= 8.0 for start, _ in writes), writes
    if expected_writes:
        assert [endpoint for _, endpoint in writes] == [prefix + "commits", prefix + "refs"]
        assert result == guard._until(1800000000 + guard._TURN_TTL)
    else:
        assert result == ""
    assert guard._PREFLIGHT_DEADLINE.get() is None
    assert guard._CACHE.get() is None
