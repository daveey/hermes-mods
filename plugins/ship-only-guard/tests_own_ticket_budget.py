"""Own-ticket hook budget: full entry, real decision code, fake network only."""
import threading
from types import SimpleNamespace

import pytest
from tests_turn import (g, gh, pr, seed_turn, merge, delta, IDENTICAL,
                        SimClock, R, NOW, H2563, H2600)


def world(gh, compare=IDENTICAL, age=3000):
    numbers = (3207, 3304, 3314, 3316, 3323, 3331)
    fake = gh({n: pr(n, H2563 if n == 3207 else H2600, ["hermes-sync"])
               for n in numbers}, compare=compare)
    for i, n in enumerate(numbers):
        seed_turn(fake, n, NOW - age + i)
    return fake


@pytest.mark.parametrize("compare", [IDENTICAL, delta(["unrelated.txt"])])
def test_old_clean_own_hook_fits_unchanged_budget(gh, monkeypatch, compare):
    fake = world(gh, compare)
    sim = SimClock(monkeypatch, fake, 3.7)
    assert g.PREFLIGHT_TIMEOUT == 18
    assert merge(3207, H2563) is None
    assert sim.monotonic() == pytest.approx(14.8)
    assert len([a for _, a, _ in sim.starts if any("...state" in w for w in a)]) == 1
    assert not sim.writes()


@pytest.mark.parametrize("age", [3000, 100])
def test_own_drift_returns_actionable_refusal_without_history(gh, monkeypatch, age):
    fake = world(gh, delta(["hermes-sync"], when=NOW - 2500), age)
    sim = SimClock(monkeypatch, fake, 3.7)
    why = merge(3207, H2563)
    assert "state commit(s) landed since then" in why, why
    assert "update-branch" in why and "deadline exceeded" not in why, why
    assert sim.monotonic() <= 18
    assert not any("/activity?" in w for _, a, _ in sim.starts for w in a)
    assert not sim.writes()


def test_old_own_fast_hook_defers_acquisition_fairness(gh, monkeypatch):
    fake = world(gh, delta(["hermes-sync"], when=NOW - 2500))
    sim = SimClock(monkeypatch, fake, 0.1)
    why = merge(3207, H2563)
    assert "--land-turn" in why and "now holds" not in why, why
    assert not any("/activity?" in w for _, a, _ in sim.starts for w in a)
    assert not sim.writes()


def test_standalone_old_own_yields_to_younger_holder(gh):
    fake = world(gh, delta(["hermes-sync"], when=NOW - 2500))
    # Younger holder still inside refresh grace, so it holds after own yields.
    seed_turn(fake, 3304, NOW - 20)
    fake.prs[3304] = pr(3304, H2563, ["hermes-sync"])
    # Other old peers explicitly reject; no fake carry machinery required.
    for n in (3314, 3316, 3323, 3331):
        fake.prs[n]["reviews"][0]["body"] = "Independent exact-head review: request changes"
    why = g._turn_hold_refusal(3207, fake.prs[3207], R, None, {})
    assert "PR #3304 holds" in why
    assert any("/activity?" in w for a in fake.calls for w in a)


def test_timeout_clock_stops_before_fake_side_effect(monkeypatch):
    calls = []
    sim = SimClock(monkeypatch, SimpleNamespace(run=lambda *a, **kw: calls.append(a)), 3.7)
    with pytest.raises(g.subprocess.TimeoutExpired):
        g.subprocess.run(["fake", "-X", "POST"], timeout=1.4)
    assert sim.monotonic() == pytest.approx(1.4)
    assert not calls


def test_clock_nested_children_start_at_parent_dispatch(monkeypatch):
    sim = SimClock(monkeypatch, SimpleNamespace(run=lambda *a, **kw: None), 3.7)
    def call():
        g.subprocess.run(["fake"], timeout=10)
    def parent():
        call()
        children = [threading.Thread(target=call) for _ in range(2)]
        for child in children:
            child.start()
        for child in children:
            child.join()
        call()
    call()
    worker = threading.Thread(target=parent)
    worker.start()
    worker.join()
    assert [t for t, _, _ in sim.starts] == pytest.approx([0, 3.7, 7.4, 7.4, 11.1])
    assert sim.monotonic() == pytest.approx(14.8)


@pytest.mark.parametrize("latency", [5, 9])
def test_canonical_exhaustion_never_allows_or_writes(gh, monkeypatch, latency):
    fake = world(gh)
    sim = SimClock(monkeypatch, fake, latency)
    why = merge(3207, H2563)
    assert isinstance(why, str) and "deadline exceeded" in why
    assert not sim.writes()
    assert all(t < 18 for t, _, _ in sim.starts)


def test_held_waiter_reports_drift_with_bounded_attribution(gh, monkeypatch):
    fake = world(gh, delta(["hermes-sync"]))
    # The holder is young; this isolates own final attribution from peer history.
    for i, n in enumerate(fake.prs):
        seed_turn(fake, n, NOW - 100 + i)
        fake.prs[n] = pr(n, H2563, ["hermes-sync"])
    sim = SimClock(monkeypatch, fake, 3.0)
    why = merge(3304, H2563)
    assert "PR #3207 holds" in why and "Also:" in why
    assert "update-branch" in why and "deadline exceeded" not in why
    assert sim.monotonic() < 18
    assert not sim.writes()


def test_held_waiter_five_causal_rounds_exhaust_canonical_budget(gh, monkeypatch):
    fake = gh({n: pr(n, H2563, ["hermes-sync"]) for n in (1, 2)},
              compare=delta(["hermes-sync"]))
    seed_turn(fake, 1, NOW - 100)
    sim = SimClock(monkeypatch, fake, 3.7)
    why = merge(2, H2563)
    assert "deadline exceeded during drift check" in why
    assert sim.monotonic() == pytest.approx(18)
    assert not sim.writes()


def test_actual_late_compare_result_is_never_admitted(gh, monkeypatch):
    fake = world(gh)
    original = fake.run
    def overrun(argv, **kw):
        result = original(argv, **kw)
        if any("...state" in w for w in argv):
            # A misbehaving subprocess returns after its timeout instead of raising.
            with sim.lock:
                sim.clock[threading.current_thread()] += 20
        return result
    fake.run = overrun
    sim = SimClock(monkeypatch, fake, 0.1)
    why = merge(3207, H2563)
    assert "deadline exceeded during drift check" in why
    assert not sim.writes()


def test_provisional_own_result_never_poisoned_full_eligibility(gh):
    fake = gh({1: pr(1, H2563, ["hermes-sync"])}, compare=delta(["hermes-sync"], when=NOW - 2500))
    seed_turn(fake, 1, NOW - 3000)
    with g._scope():
        assert g._turn_hold_read_only(1, fake.prs[1], R, None, {}) is None
        assert g._own_live_turn(1, fake.prs[1]) is None
        assert g._turn_yielded(fake.turn(1), fake.prs[1], R, None, {}) is True
        assert g._turn_acquire(1, fake.prs[1], R, None, {}, True) == ""
    code, why = g.land_turn(1, R, H2563, env={"GH_REPO": R})
    assert code == 1 and "now holds" not in why


@pytest.mark.parametrize("verdict", ["approve", "block", "missing", "malformed", "carry", "changed"])
@pytest.mark.parametrize("overlap", [False, True])
@pytest.mark.parametrize("older", [False, True])
def test_admission_equivalent_to_full_own_rule(gh, monkeypatch, verdict, overlap, older):
    fake = gh({n: pr(n, H2563, ["hermes-sync"]) for n in (1, 2)},
              compare=delta(["hermes-sync" if overlap else "unrelated.txt"], when=NOW - 2500))
    seed_turn(fake, 1, NOW - 3000)
    seed_turn(fake, 2, NOW - (3100 if older else 100))
    data = fake.prs[1]
    head = H2563
    if verdict == "block":
        data["reviews"][0]["body"] = "Independent exact-head review: request changes"
    elif verdict == "missing":
        data["reviews"] = []
    elif verdict == "malformed":
        data["reviews"] = None
    elif verdict in ("carry", "changed"):
        head = data["headRefOid"] = H2600
        monkeypatch.setattr(g, "_carried_approval", lambda *a: (verdict == "carry", "fixture patch changed"))
    optimized = merge(1, head)
    # --land-turn retains full own eligibility. Compare only admission, not
    # differing refusal guidance or acquisition outcome on already-refused drift.
    token = g._TURN_ONLY.set({})
    try:
        reference = merge(1, head)
    finally:
        g._TURN_ONLY.reset(token)
    assert (optimized is None) == (reference is None), (optimized, reference)
