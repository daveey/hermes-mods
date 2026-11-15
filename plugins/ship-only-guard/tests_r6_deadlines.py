"""Round-6 eligibility deadline and read-worker lifetime regressions (offline)."""
import threading
import time
from types import SimpleNamespace

import pytest
from tests_turn import (g, gh, pr, delta, seed_turn, merge, H2563, NOW,
                        fl, BASE, sha, drift_world, chain_pushes, is_edge, push_event)


@pytest.mark.parametrize("outer,eligibility,expected", [(100, 7, 7), (4, 7, 4)])
def test_probe_clips_to_both_deadlines(monkeypatch, outer, eligibility, expected):
    monkeypatch.setattr(g, "time", SimpleNamespace(monotonic=lambda: 0))
    seen = []
    monkeypatch.setattr(g.subprocess, "run", lambda *a, **kw: seen.append(kw["timeout"]))
    a = g._PREFLIGHT_DEADLINE.set(outer)
    b = g._ELIGIBILITY_DEADLINE.set(eligibility)
    try:
        g._run_probe(["offline"], timeout=15)
        assert seen == [expected]
    finally:
        g._ELIGIBILITY_DEADLINE.reset(b)
        g._PREFLIGHT_DEADLINE.reset(a)


def test_probe_rejects_late_eligibility_result(monkeypatch):
    clock = [0]
    monkeypatch.setattr(g, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr(g.subprocess, "run", lambda *a, **kw: clock.__setitem__(0, 31))
    token = g._ELIGIBILITY_DEADLINE.set(30)
    try:
        with pytest.raises(g._Unreadable, match="eligibility deadline"):
            g._run_probe(["offline"], timeout=60)
    finally:
        g._ELIGIBILITY_DEADLINE.reset(token)


def test_holder_reads_finish_by_eligibility_without_acquisition(gh, monkeypatch):
    fake = gh({n: pr(n, H2563, ["hermes-sync"]) for n in (1, 2)},
              compare=delta(["hermes-sync"], when=NOW - 1000))
    fake.activity = [push_event(BASE, "c" * 40, NOW - 1000)]
    seed_turn(fake, 1, NOW - 1800)
    clock, writes = [0], []
    monkeypatch.setattr(g, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    inner = g.subprocess.run

    def bounded(argv, **kw):
        cost = 4 if any("matching-refs" in a for a in argv) else 9
        clock[0] += min(cost, kw["timeout"])
        if kw["timeout"] < cost:
            raise g.subprocess.TimeoutExpired(argv, kw["timeout"])
        if "-X" in argv or "--input" in argv and "graphql" not in argv:
            writes.append(argv)
        return inner(argv, **kw)

    monkeypatch.setattr(g.subprocess, "run", bounded)
    spans = []
    original = g._turn_hold_refusal_scoped

    def timed(*a):
        start = clock[0]
        try:
            return original(*a)
        finally:
            spans.append(clock[0] - start)

    monkeypatch.setattr(g, "_turn_hold_refusal_scoped", timed)
    why = merge(2, H2563)
    assert why and "now holds" not in why
    assert fake.turn(2) is None
    assert spans and max(spans) <= 30
    assert not writes


@pytest.mark.parametrize("stage", ["_turn_yielded", "_turn_holder_data"])
def test_late_cached_holder_result_cannot_admit_waiter(gh, monkeypatch, stage):
    fake = gh({n: pr(n, H2563, ["hermes-sync"]) for n in (1, 2)})
    seed_turn(fake, 1, NOW - 1800)
    clock = [0]
    monkeypatch.setattr(g, "time", SimpleNamespace(monotonic=lambda: clock[0]))

    def late(*a):
        clock[0] = 31
        return True if stage == "_turn_yielded" else fake.prs[1]

    monkeypatch.setattr(g, stage, late)
    why = merge(2, H2563)
    assert why and ("snapshot unreadable" in why or "deadline exceeded" in why)
    assert fake.turn(2) is None


@pytest.mark.parametrize("unreadable", [False, True])
def test_selector_joins_actual_inflight_reads_before_return(gh, monkeypatch, unreadable):
    pushes = [(b, a, w, fl("hermes-sync") if a == 20 else fl("unrelated/file"))
              for b, a, w, _ in chain_pushes(20, 1, NOW - 3000)]
    fake = drift_world(gh, pushes)
    if unreadable:
        fake.edge_compare[f"{BASE}...{sha(19)}"] = {"status": "diverged", "behind_by": 1, "files": []}
    inner = g.subprocess.run
    lock, ready = threading.Lock(), threading.Event()
    state = {"started": 0, "finished": 0}

    def read(argv, **kw):
        if not is_edge(argv):
            return inner(argv, **kw)
        with lock:
            state["started"] += 1
            if state["started"] == g._EDGE_WORKERS:
                ready.set()
        assert ready.wait(2)
        # Newest cumulative nonoverlap (or error) decides while older reads remain busy.
        if not argv[-1].endswith(sha(19)):
            time.sleep(0.1)
        try:
            return inner(argv, **kw)
        finally:
            with lock:
                state["finished"] += 1

    monkeypatch.setattr(g.subprocess, "run", read)
    why = merge(2, H2563)
    assert why and ("snapshot unreadable" in why if unreadable else "now holds" in why)
    with lock:
        assert state["finished"] == state["started"]
        assert g._EDGE_WORKERS <= state["started"] < len(pushes)
