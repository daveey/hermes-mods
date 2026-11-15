"""Drift attribution is garnish and immutable carry reads are single-flight (t_01a1d96e71233fe1).

Live 2026-11-14: state PR #2944 (green, approved, drifted 8 commits on hermes-sync/tests) was refused three times
with `deadline exceeded during drift attribution` instead of its drift refusal, and the land turn that refusal
should record was never acquired: hot-path PRs livelocked through update-branch/CI/drift. #2842's carry read two
trees twice in parallel and left the drift check no time.
"""
import importlib.util
from pathlib import Path
import threading
import time
from types import SimpleNamespace

import pytest


def load():
    spec = importlib.util.spec_from_file_location("attr_guard", Path(__file__).with_name("guard.py"))
    g = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(g)
    return g


class Clock:
    def __init__(self):
        self.now = 1000.0

    def monotonic(self):
        return self.now


def at(g, monkeypatch, left):
    clock = Clock()
    monkeypatch.setattr(g, "time", SimpleNamespace(**{**vars(time), "monotonic": clock.monotonic}))
    g._PREFLIGHT_DEADLINE.set(clock.now + left)
    return clock


def test_attribution_expiry_degrades_to_no_attribution(monkeypatch):
    g = load()
    clock = at(g, monkeypatch, 5.0)                   # after a carry: no turn possible, verdict reserve kept
    seen = []

    def slow():
        seen.append(g._PREFLIGHT_DEADLINE.get() - clock.now)
        clock.now += 4.0
        g._remaining()                                # the sub-deadline expires here
        return "never"
    assert g._best_effort(slow) == ""
    assert seen == [pytest.approx(5.0 - g.PREFLIGHT_TIMEOUT * g._ATTRIBUTE_RESERVE)]
    assert g._remaining() == pytest.approx(1.0)       # the canonical deadline is restored, still live


def test_attribution_keeps_land_turn_eligibility(monkeypatch):
    g = load()
    clock = at(g, monkeypatch, 13.0)
    seen = []

    def fn():
        seen.append(g._PREFLIGHT_DEADLINE.get() - clock.now)
        return "x: abc fix"
    assert g._best_effort(fn) == "x: abc fix"
    assert seen and 13.0 - seen[0] > g._TURN_BUDGET   # whatever attribution costs, the turn stays eligible
    # Too little room above the turn budget: attribution is skipped instead of costing the turn.
    at(g, monkeypatch, g._TURN_BUDGET + 0.5)
    assert g._best_effort(lambda: pytest.fail("attribution must not start")) == ""


def test_canonical_expiry_still_raises(monkeypatch):
    g = load()
    clock = at(g, monkeypatch, 5.0)

    def overrun():
        clock.now += 6.0
        return "late"
    with pytest.raises(g._PreflightExpired):
        g._best_effort(overrun)


def test_drift_refusal_survives_slow_attribution(monkeypatch):
    """The refusal text goes out (and _state_merge may acquire the turn) even when every attribution read
    hangs to the deadline."""
    g = load()
    monkeypatch.setattr(g, "PREFLIGHT_TIMEOUT", 4.0)
    monkeypatch.setattr(g, "_TURN_BUDGET", 1.0)
    monkeypatch.setattr(g, "_ATTRIBUTE_MIN", 0.1)

    def hang(*a, **k):
        time.sleep(g._remaining() + 0.02)
        g._remaining()
    monkeypatch.setattr(g, "_run_probe", hang)

    def refuse():
        hits = g._best_effort(g._attribute_hits, "o/r", ["a/b.py"], {}, lambda c: "", None, {})
        return f"refused; culprits={hits!r}; left={g._remaining():.1f}"
    out = g.bounded_preflight(refuse)
    assert out.startswith("refused; culprits=''"), out
    assert float(out.rsplit("=", 1)[1]) > g._TURN_BUDGET


def test_once_single_flight():
    g = load()
    once, calls, gate = g._Once(), [], threading.Event()

    def read(key):
        calls.append(key)
        gate.wait(1)
        return {"tree": key}
    threads = [threading.Thread(target=lambda: out.append(once.get("t", read, "t"))) for _ in range(4)]
    out = []
    for t in threads:
        t.start()
    time.sleep(0.05)
    gate.set()
    for t in threads:
        t.join()
    assert calls == ["t"] and out == [{"tree": "t"}] * 4


@pytest.mark.parametrize("owner_fails", [False, True])
def test_once_waiter_expires_while_owner_is_in_flight(monkeypatch, owner_fails):
    """A waiter's budget must not become an unbounded wait on another reader.

    Use a real blocked owner, but model the slot event's timeout and monotonic
    clock together: no scheduler-dependent sleep or elapsed-time threshold.
    The owner is released in finally, including when the deadline mutant fails.
    """
    g = load()
    clock = at(g, monkeypatch, 2.0)
    once = g._Once()
    entered, release = threading.Event(), threading.Event()
    calls, values, errors, waits = [], [], [], []

    def read():
        calls.append("owner")
        entered.set()
        release.wait()
        if owner_fails:
            raise RuntimeError("owner failed")
        return {"tree": "t"}

    def own():
        try:
            values.append(once.get("t", read))
        except BaseException as exc:
            errors.append(exc)

    owner = threading.Thread(target=own)
    owner.start()
    try:
        assert entered.wait(5), "owner did not enter the read"
        slot = once._slots["t"]
        event = slot[0]

        class ExpiringWait:
            def wait(self, timeout=None):
                waits.append(timeout)
                # An unbounded wait is itself the regression: refuse it rather
                # than hanging the test process until an outer test timeout.
                assert timeout is not None and timeout > 0, "waiter requested an unbounded wait"
                assert len(waits) == 1, "waiter waited again after its budget expired"
                remaining = g._PREFLIGHT_DEADLINE.get() - clock.now
                assert timeout == pytest.approx(remaining), "waiter ignored its remaining budget"
                assert not event.is_set() and not release.is_set()
                clock.now += timeout
                return event.wait(0)

            def set(self):
                event.set()

        slot[0] = ExpiringWait()
        with pytest.raises(g._PreflightExpired):
            once.get("t", lambda: pytest.fail("waiter started a duplicate read"))
        assert waits == [2.0]
        assert owner.is_alive() and not release.is_set()
        assert not event.is_set() and once._slots["t"] is slot
        assert calls == ["owner"] and values == [] and errors == []
    finally:
        release.set()
        owner.join(5)
    assert not owner.is_alive(), "owner was not cleaned up"
    assert event.is_set(), "owner must wake any remaining waiters"
    if owner_fails:
        assert values == [] and len(errors) == 1
        assert isinstance(errors[0], RuntimeError) and str(errors[0]) == "owner failed"
        assert "t" not in once._slots
        assert once.get("t", lambda: "retry") == "retry"
    else:
        assert errors == [] and values == [{"tree": "t"}]
        # Read the completed memo under a fresh budget (not the expired waiter).
        g._PREFLIGHT_DEADLINE.set(clock.now + 2.0)
        slot[0] = event
        assert once.get("t", lambda: pytest.fail("owner result was not memoized")) == values[0]


def test_once_memoizes_unreadable_but_not_other_failures():
    g = load()
    once, calls = g._Once(), []

    def bad():
        calls.append("bad")
        raise g._Unreadable("tree x unreadable")
    for _ in range(2):
        with pytest.raises(g._Unreadable, match="tree x unreadable"):
            once.get("x", bad)
    assert calls == ["bad"]

    def flaky():
        calls.append("flaky")
        if calls.count("flaky") == 1:
            raise RuntimeError("expired")
        return "ok"
    with pytest.raises(RuntimeError):
        once.get("y", flaky)
    assert once.get("y", flaky) == "ok" and calls.count("flaky") == 2


def test_flat_trees_reads_each_tree_once_and_fills_a_dict_cache(monkeypatch):
    g = load()
    calls = []
    monkeypatch.setattr(g, "_flat_tree", lambda sha, *a: calls.append(sha) or {"p": sha})
    cache = {"done": {"p": "done"}}
    assert g._flat_trees(["a", "b", "a", "done"], "o/r", None, {}, cache) == [
        {"p": "a"}, {"p": "b"}, {"p": "a"}, {"p": "done"}]
    assert sorted(calls) == ["a", "b"] and set(cache) == {"a", "b", "done"}
