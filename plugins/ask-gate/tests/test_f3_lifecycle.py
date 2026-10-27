"""Synthetic, isolated lifecycle adapter and durable-state regressions."""
import importlib.util
from pathlib import Path
import time

import pytest


@pytest.fixture
def lifecycle(tmp_path, monkeypatch):
    monkeypatch.setenv("ASK_GATE_LIFECYCLE_DB", str(tmp_path / "lifecycle.sqlite"))
    spec = importlib.util.spec_from_file_location(
        "lifecycle_test", Path(__file__).resolve().parents[1] / "lifecycle.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def target(**changes):
    return dict(authority="example", kind="account", subject="synthetic", action="connect") | changes


def record(identity, state="revoked", revision=1, **changes):
    return dict(target=identity.copy(), state=state, revision=revision,
                verified=True, observed_at=time.time(), explicit_reopen=False) | changes


def install(lifecycle, identity, box):
    return lifecycle.register("example", lambda context: identity.copy(), lambda scope: box[0])


@pytest.mark.parametrize("kind,state", [
    ("account", "revoked"), ("subscription", "cancelled"),
    ("project", "cancelled"), ("user_step", "completed"),
    ("native_action", "impossible"),
])
def test_terminal_persists_across_store_instances(lifecycle, kind, state):
    identity = target(kind=kind)
    box = [record(identity, state)]
    install(lifecycle, identity, box)
    assert lifecycle.preflight({})["action"] == "block"
    assert lifecycle.Store().get(identity)["state"] == state
    box[0] = record(identity, "active", 2)
    assert lifecycle.preflight({})["action"] == "block"
    assert lifecycle.Store().get(identity)["state"] == state


def test_reopen_is_explicit_ordered_and_never_permission(lifecycle):
    identity = target()
    box = [record(identity)]
    install(lifecycle, identity, box)
    assert "not execution authorization" in lifecycle.preflight({})["message"]
    box[0] = record(identity, "active", 2, explicit_reopen=True)
    assert lifecycle.preflight({}) is None
    assert lifecycle.Store().get(identity)["state"] == "active"
    box[0] = record(identity, "revoked", 1, observed_at=time.time())
    assert lifecycle.preflight({})["action"] == "block"  # stale evidence recovery
    assert lifecycle.Store().get(identity)["state"] == "active"
    box[0] = record(identity, "revoked", 3)
    assert lifecycle.preflight({})["action"] == "block"
    box[0] = record(identity, "active", 2, explicit_reopen=True)
    assert lifecycle.preflight({})["action"] == "block"
    assert lifecycle.Store().get(identity)["state"] == "revoked"


@pytest.mark.parametrize("changes", [
    {"verified": False}, {"verified": 1}, {"observed_at": 0},
    {"observed_at": float("nan")}, {"observed_at": float("inf")},
    {"observed_at": True}, {"observed_at": time.time() + 3600},
    {"target": target(subject="other")}, {"target": target(authority="other")},
    {"revision": -1}, {"revision": True}, {"revision": "2"},
    {"state": "approved"}, {"explicit_reopen": "true"},
])
def test_invalid_evidence_never_reopens(lifecycle, changes):
    identity = target()
    box = [record(identity)]
    install(lifecycle, identity, box)
    lifecycle.preflight({})
    box[0] = record(identity, "active", 2, explicit_reopen=True) | changes
    assert lifecycle.preflight({})["action"] == "block"
    assert lifecycle.Store().get(identity)["state"] == "revoked"


def test_unavailable_read_preserves_retirement_and_refreshes(lifecycle):
    identity = target()
    calls = []
    def read(scope):
        calls.append(scope)
        if len(calls) > 1:
            raise OSError("private details must not escape")
        return record(identity)
    lifecycle.register("example", lambda context: identity, read)
    lifecycle.preflight({})
    result = lifecycle.preflight({})
    assert len(calls) == 2
    assert result["action"] == "block"
    assert "private details" not in result["message"]
    assert lifecycle.Store().get(identity)["state"] == "revoked"


@pytest.mark.parametrize("field,value", [
    ("authority", "Example"), ("kind", "subscription"),
    ("subject", "Synthetic"), ("subject", "synthetic "), ("action", "Connect"),
])
def test_identity_is_exact_without_normalization(lifecycle, field, value):
    identity = target()
    install(lifecycle, identity, [record(identity)])
    lifecycle.preflight({})
    assert lifecycle.Store().get(identity | {field: value}) is None


def test_source_scope_and_model_reopen_not_trusted(lifecycle):
    identity = target()
    box = [record(identity)]
    dispose = install(lifecycle, identity, box)
    lifecycle.preflight({})
    box[0] = record(identity, "active", 2)
    assert lifecycle.preflight({"explicit_reopen": True, "verified": True})["action"] == "block"
    dispose()
    lifecycle.register("other", lambda context: identity, lambda scope: record(identity, "active", 3, explicit_reopen=True))
    assert lifecycle.preflight({})["action"] == "block"
    assert lifecycle.Store().get(identity)["state"] == "revoked"


def test_same_revision_conflict_cannot_override(lifecycle):
    identity = target()
    box = [record(identity)]
    install(lifecycle, identity, box)
    lifecycle.preflight({})
    box[0] = record(identity, "active", 1, explicit_reopen=True)
    assert lifecycle.preflight({})["action"] == "block"
    assert lifecycle.Store().get(identity)["state"] == "revoked"


@pytest.mark.parametrize("identity", [
    target(kind="unknown"), target(subject=""), target(action=None),
    target(extra="ignored?"), {"authority": "example", "kind": "account"},
])
def test_invalid_identity_fails_closed_without_read(lifecycle, identity):
    lifecycle.register("example", lambda context: identity,
                       lambda scope: pytest.fail("invalid scope must not be read"))
    assert lifecycle.preflight({})["action"] == "block"


def test_failed_adapter_does_not_skip_other_durable_records(lifecycle):
    def fail(context):
        raise RuntimeError("unavailable")
    lifecycle.register("unavailable", fail, lambda scope: None)
    identity = target()
    install(lifecycle, identity, [record(identity)])
    assert lifecycle.preflight({})["action"] == "block"
    assert lifecycle.Store().get(identity)["state"] == "revoked"


def test_repeated_active_snapshot_does_not_erase_terminal(lifecycle):
    identity = target()
    box = [record(identity)]
    install(lifecycle, identity, box)
    lifecycle.preflight({})
    box[0] = record(identity, "active", 4)
    for _ in range(2):
        assert lifecycle.preflight({})["action"] == "block"
        assert lifecycle.Store().get(identity)["state"] == "revoked"
    # A late explicit reopen cannot overwrite a higher revision active snapshot.
    box[0] = record(identity, "active", 3, explicit_reopen=True)
    assert lifecycle.preflight({})["action"] == "block"
    assert lifecycle.Store().get(identity)["revision"] == 4


def test_parallel_reads_do_not_roll_back_revision(lifecycle):
    from concurrent.futures import ThreadPoolExecutor
    import threading

    identity = target()
    barrier = threading.Barrier(2)
    newer_done = threading.Event()
    def read(scope):
        barrier.wait(timeout=5)
        if threading.current_thread().name.endswith("_0"):
            assert newer_done.wait(timeout=5)
            return record(identity, "active", 1, explicit_reopen=True)
        return record(identity, "revoked", 2)
    lifecycle.register("example", lambda context: identity, read)
    def run():
        result = lifecycle.preflight({})
        if threading.current_thread().name.endswith("_1"):
            newer_done.set()
        return result
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: run(), range(2)))
    assert all(result["action"] == "block" for result in results)
    assert lifecycle.Store().get(identity)["state"] == "revoked"
    assert lifecycle.Store().get(identity)["revision"] == 2


def test_unmanaged_dispose_and_duplicate(lifecycle):
    assert lifecycle.preflight({}) is None
    dispose = lifecycle.register("example", lambda context: None, lambda scope: pytest.fail("unmanaged read"))
    assert lifecycle.preflight({}) is None
    with pytest.raises(ValueError):
        lifecycle.register("example", lambda context: None, lambda scope: None)
    dispose()
    dispose()
    assert lifecycle.preflight({}) is None
