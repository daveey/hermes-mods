"""Nested preflights must enforce the tighter deadline while work runs."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


def load_guard():
    spec = importlib.util.spec_from_file_location(
        "ship_nested_deadline_guard", Path(__file__).with_name("guard.py")
    )
    guard = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(guard)
    return guard


@pytest.mark.parametrize(
    "parent, expected", [(None, 110.0), (103.0, 103.0), (130.0, 110.0)],
    ids=["no-parent", "tighter-parent", "tighter-child"],
)
def test_callback_observes_tighter_deadline(monkeypatch, parent, expected):
    guard = load_guard()
    monkeypatch.setattr(guard, "time", SimpleNamespace(monotonic=lambda: 100.0))
    monkeypatch.setattr(guard, "PREFLIGHT_TIMEOUT", 10.0)
    token = guard._PREFLIGHT_DEADLINE.set(parent)
    called = []

    def check():
        called.append(True)
        assert guard._PREFLIGHT_DEADLINE.get() == expected
        return "checked"

    try:
        assert guard.bounded_preflight(check) == "checked"
        assert called == [True]
        assert guard._PREFLIGHT_DEADLINE.get() == parent
    finally:
        guard._PREFLIGHT_DEADLINE.reset(token)


def test_nested_probe_timeout_cannot_exceed_parent_remaining(monkeypatch):
    guard = load_guard()
    clock = [100.0]
    monkeypatch.setattr(guard, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr(guard, "PREFLIGHT_TIMEOUT", 10.0)
    probes = []
    completed = SimpleNamespace(returncode=0)

    def run(argv, **kwargs):
        probes.append((argv, kwargs["timeout"]))
        return completed

    monkeypatch.setattr(guard, "subprocess", SimpleNamespace(run=run))

    def outer():
        parent_deadline = guard._PREFLIGHT_DEADLINE.get()
        assert parent_deadline == 110.0
        clock[0] = 108.0

        def inner():
            assert guard._run_probe(["git", "version"], timeout=30.0) is completed
            assert len(probes) == 1
            argv, timeout = probes[0]
            assert argv == ["git", "version"]
            assert 0 < timeout <= parent_deadline - clock[0]
            assert timeout == 2.0
            return "inner checked"

        assert guard.bounded_preflight(inner) == "inner checked"
        assert guard._PREFLIGHT_DEADLINE.get() == parent_deadline
        return "outer checked"

    assert guard.bounded_preflight(outer) == "outer checked"
    assert guard._PREFLIGHT_DEADLINE.get() is None
