"""Bounded peer land-turn eligibility (t_01a1e1107d705388).

2026-11-15 16:10-16:30Z every merge preflight of #3314 refused "land-turn snapshot unreadable": deciding whether
#3207's older ticket had yielded walked its approval carry and drift history (~40 gh reads at 2-3.7 s through
worker-gh token vending) inside the 30 s eligibility allowance. A peer ticket is now judged from its record and
one holder view; anything that needs a content walk is undecided and blocks (fail safe), never admits.
"""
import itertools
import random

import pytest
from tests_turn import (g, gh, pr, delta, seed_turn, merge, own_rule, SimClock, H2563, H2600, NOW, R, IDENTICAL)

GH_READ = 3.7                                    # seconds per gh read through worker-gh token vending (11-15)
TURNS = (3207, 3304, 3314, 3316, 3323, 3331)    # the live turns at 16:40Z
AREA = ["hermes-sync"]


def walk_reads(calls):
    """Reads that belong to a per-turn content walk (carry or drift-start), never to the bounded peer rule."""
    return [c for c in calls if c[0] != "gh" or any(
        "/activity?" in a or "/git/trees" in a or "/git/blobs" in a or "/commits/" in a
        or ("/compare/" in a and not a.endswith("...state")) for a in c)]


def expensive_world(gh, waiter, own_ticket):
    """Six overlapping live turns whose every peer would need the full content walk under the old rule:
    refreshed head (H2600) with no exact-head verdict but a carryable earlier approval, ticket past the
    900 s refresh grace, and state drifted on the shared area."""
    fake = gh({n: pr(n, H2563, AREA) for n in TURNS}, compare=delta(AREA, when=NOW - 2500))
    for i, n in enumerate(TURNS):
        if n == waiter and not own_ticket:
            continue
        seed_turn(fake, n, NOW - 3000 + i)
        if n != waiter:
            fake.prs[n]["headRefOid"] = H2600     # the approval of H2563 is a carry candidate: a walk under the old rule
    return fake


def per_turn_reads(calls):
    counts = {n: 0 for n in TURNS}
    for c in calls:
        for n in TURNS:
            if str(n) in c and c[:3] == ["gh", "pr", "view"]:
                counts[n] += 1
    return counts


@pytest.mark.parametrize("waiter,own_ticket", [(3331, False), (3314, True), (3314, False), (3207, True)])
def test_six_turns_at_3_7s_per_read_fit_the_budget_with_bounded_reads_per_turn(gh, monkeypatch, waiter, own_ticket):
    """The eligibility check every consumer calls (hook stage "land-turn reads", `--land-turn`, state's
    state-push-guard): 6 overlapping turns that each needed a ~40-read walk under the old rule."""
    fake = expensive_world(gh, waiter, own_ticket)
    if waiter == 3207:
        fake.compare = IDENTICAL           # its own (full-rule) ticket: one drift compare, no drift to walk
    sim = SimClock(monkeypatch, fake, GH_READ)
    why = g._turn_hold_refusal(waiter, fake.prs[waiter], R, None, {})
    elapsed = sim.monotonic()
    if waiter == 3207:                     # the oldest: no peer is older, its own ticket uses the full rule
        assert why is None
    else:
        assert "PR #3207 may still hold" in why and "unreadable" not in why, why
    # Each OTHER turn costs at most one holder view; the listing and record batch are shared by all turns.
    counts = per_turn_reads(fake.calls)
    assert all(counts[n] <= 1 for n in TURNS if n != waiter), counts
    shared = [c for c in fake.calls if c[:3] != ["gh", "pr", "view"]]
    assert len([c for c in shared if "matching-refs" in " ".join(c)]) == 1
    assert len([c for c in shared if c[1:3] == ["api", "graphql"]]) == 1
    assert not walk_reads(fake.calls), walk_reads(fake.calls)
    # listing, record batch, concurrent holder views (the oldest instead reads its own drift compare):
    # three 3.7 s rounds, inside the 30 s allowance and the hook's 18 s preflight.
    assert elapsed <= 3 * GH_READ + 1e-6, elapsed
    assert elapsed <= 30 and elapsed < g.PREFLIGHT_TIMEOUT
    print(f"6 turns, waiter #{waiter}: eligibility {elapsed:.1f} s simulated, holder reads {counts}")


@pytest.mark.parametrize("waiter,own_ticket", [(3331, False), (3314, True), (3314, False)])
@pytest.mark.parametrize("latency", [3.0, GH_READ])
def test_hook_preserves_final_fresh_compare_and_deadline(gh, monkeypatch, waiter, own_ticket, latency):
    fake = expensive_world(gh, waiter, own_ticket)
    # View, listing, records, holders and final fresh compare are five serial rounds.
    # At 3.7 s each they cannot fit the unchanged 18 s hook budget, even without drift.
    fake.compare = IDENTICAL
    sim = SimClock(monkeypatch, fake, latency)
    why = merge(waiter, H2563)
    if latency == GH_READ:
        assert "deadline exceeded during drift check" in why, why
    else:
        assert "PR #3207 may still hold" in why and "deadline" not in why, why
        assert any("/compare/" in " ".join(c) for c in fake.calls)
    assert not walk_reads(fake.calls) and not sim.writes()
    assert sim.monotonic() <= g.PREFLIGHT_TIMEOUT


def test_standalone_land_turn_decides_the_incident_without_unreadable(gh, monkeypatch):
    """`guard.py --land-turn` (LAND_TURN_TIMEOUT) for #3314 behind #3207's walk-needing ticket: a decided
    "may still hold" refusal instead of "snapshot unreadable", with no acquisition write and no walk read."""
    fake = expensive_world(gh, 3314, True)
    sim = SimClock(monkeypatch, fake, GH_READ)
    status, message = g.land_turn(3314, R, H2563)
    assert status == 1 and "PR #3207 may still hold" in message, message
    assert "unreadable" not in message and "deadline" not in message, message
    assert not sim.writes() and not walk_reads(fake.calls)
    assert sim.monotonic() < g.LAND_TURN_TIMEOUT


def test_standalone_land_turn_fits_too(gh, monkeypatch):
    fake = expensive_world(gh, 3314, False)
    sim = SimClock(monkeypatch, fake, GH_READ)
    status, message = g.land_turn(3314, R, H2563)
    assert status == 1 and "PR #3207 may still hold" in message and "unreadable" not in message, message
    assert sim.monotonic() < g.LAND_TURN_TIMEOUT
    assert not walk_reads(fake.calls)


@pytest.mark.parametrize("fault", ["holder", "slow"])
def test_bounded_read_still_fails_closed(gh, monkeypatch, fault):
    fake = expensive_world(gh, 3331, False)
    if fault == "holder":
        inner = fake.run
        monkeypatch.setattr(fake, "run", lambda argv, **kw: (
            g.subprocess.CompletedProcess(argv, 1, "", "HTTP 502") if argv[:4] == ["gh", "pr", "view", "3304"]
            else inner(argv, **kw)))
        sim = SimClock(monkeypatch, fake, GH_READ)
    else:
        sim = SimClock(monkeypatch, fake, 9.5)            # every read slower than the budget allows
    why = merge(3331, H2563)
    assert why and ("snapshot unreadable" in why or "deadline" in why), why
    assert "now holds" not in why
    assert fake.turn(3331) is None and not sim.writes()


def test_yields_decidable_from_the_holder_view_still_free_waiters(gh, monkeypatch):
    """Exact-head block, closed/merged holder, nothing to carry: decided without a walk, as before."""
    fake = expensive_world(gh, 3331, False)
    fake.prs[3207]["reviews"] = [{**pr(3207, H2600, AREA)["reviews"][0],
                                  "body": "Independent exact-head review: request changes"}]
    fake.prs[3304]["state"] = "MERGED"
    for n in (3314, 3316, 3323):
        fake.prs[n]["reviews"] = []                        # refreshed with no approval anywhere: cannot carry
    SimClock(monkeypatch, fake, GH_READ)
    why = merge(3331, H2563)
    assert why is None or ("may still hold" not in why and "holds the state land turn until" not in why
                           or "PR #3331 now holds" in why), why
    assert not walk_reads([c for c in fake.calls if "3331" not in " ".join(c)])


def test_young_unrefreshed_peer_definitely_holds(gh):
    fake = gh({n: pr(n, H2563, AREA) for n in (1, 2)})
    seed_turn(fake, 1, NOW - 60)
    assert "PR #1 holds the state land turn" in merge(2, H2563)


def test_waiter_never_runs_peer_carry_or_drift(gh, monkeypatch):
    fake = expensive_world(gh, 3331, False)
    called = []
    for name in ("_carried_approval", "_turn_drift_started", "_turn_yielded"):
        monkeypatch.setattr(g, name, lambda *a, _n=name: called.append(_n))
    merge(3331, H2563)
    assert called == []


def test_own_ticket_defers_drift_only_in_merge_scope(gh, monkeypatch):
    """Merge defers own drift to the final compare; standalone retains the full rule."""
    fake = gh({n: pr(n, H2563, AREA) for n in (1, 2)}, compare=delta(AREA, when=NOW - 2000))
    seed_turn(fake, 1, NOW - 3000)
    seen = []
    real = g._turn_yielded_uncached

    def traced(rec, *args, **kwargs):
        seen.append((rec["pr"], kwargs.get("defer_own_drift", False)))
        return real(rec, *args, **kwargs)

    monkeypatch.setattr(g, "_turn_yielded_uncached", traced)
    why = merge(1, H2563)
    assert why, "final drift check must still refuse"
    assert seen == [(1, True)]
    seen.clear()
    assert own_rule(fake) == "yields"
    assert seen == [(1, False)]


# ---- pure selection: sound over-approximation of every outcome of the undecided tickets ----

def _rec(n, files):
    return {"pr": n, "acquired": NOW - 1000 + n, "expires": NOW + 1000, "files": files}


def test_possible_turns_with_boolean_eligibility_is_effective_turns():
    rng = random.Random(7)
    for _ in range(300):
        recs = [(f"r{n}", _rec(n, rng.sample(["a/x", "a/y", "b/z", "a"], rng.randint(1, 2)))) for n in range(6)]
        elig = {n: rng.random() < 0.6 for n in range(6)}
        want = [(ref, rec) for ref, rec in g.effective_turns(recs, lambda ref, rec: elig[rec["pr"]], lambda: None)]
        got = [(ref, rec, why) for ref, rec, why in g.possible_turns(recs, lambda ref, rec: elig[rec["pr"]],
                                                                      lambda: None)]
        assert [(ref, rec) for ref, rec, _ in got] == want and all(why is None for *_, why in got)


def _bounded_admits(recs, elig, me, files, monkeypatch):
    """Exercise the real scoped decision; mock only snapshot IO and eligibility."""
    with monkeypatch.context() as m:
        m.setattr(g, "_live_turns", lambda *a: recs)
        m.setattr(g, "_prefetch_holders", lambda *a: None)
        m.setattr(g, "_turn_holder_data", lambda n, *a: {"state": "OPEN", "number": n})
        m.setattr(g, "peer_eligibility", lambda rec, holder: elig[rec["pr"]])
        m.setattr(g, "_turn_yielded", lambda rec, *a: not elig[rec["pr"]])
        data = {"state": "OPEN", "files": [{"path": f} for f in files]}
        return g._turn_hold_refusal(me, data, R, None, {}) is None


def test_chain_behind_undecided_ticket_does_not_suppress_later_blocker(gh):
    fake = gh({1: pr(1, H2563, ["a"]), 2: pr(2, H2563, ["a", "b"]),
               3: pr(3, H2563, ["b", "c"]), 4: pr(4, H2563, ["c"])})
    seed_turn(fake, 1, NOW - 2000, ["a"])
    seed_turn(fake, 2, NOW - 60, ["a", "b"])
    seed_turn(fake, 3, NOW - 30, ["b", "c"])
    why = g._turn_hold_refusal(4, fake.prs[4], R, None, {})
    assert why and "PR #3 may still hold" in why, why


def test_possible_own_ticket_with_changed_files_does_not_admit(gh):
    fake = gh({1: pr(1, H2563, ["a"]), 2: pr(2, H2563, ["a", "b"]),
               3: pr(3, H2563, ["b", "c"])})
    seed_turn(fake, 1, NOW - 2000, ["a"])
    seed_turn(fake, 2, NOW - 60, ["a", "b"])
    seed_turn(fake, 3, NOW - 30, ["b", "c"])
    fake.prs[2]["files"] = [{"path": "c"}]
    why = g._turn_hold_refusal(2, fake.prs[2], R, None, {})
    assert why and "PR #3 may still hold" in why, why


def _full_admits(recs, elig, me, files):
    for ref, rec in g.effective_turns(recs, lambda ref, rec: elig[rec["pr"]], lambda: None):
        if rec["pr"] == me:
            return True
        if g.turn_blocks(rec["files"], files) or g.turn_blocks(files, rec["files"]):
            return False
    return True


def test_bounded_selection_never_admits_what_any_outcome_refuses(monkeypatch):
    rng = random.Random(11)
    areas = ["a/x", "a/y", "b/z", "a", "c"]
    checked = 0
    for _ in range(400):
        n = rng.randint(2, 6)
        recs = [(f"r{i}", _rec(i, rng.sample(areas, rng.randint(1, 2)))) for i in range(n)]
        me = rng.randrange(n + 1)                          # n: a waiter without a ticket of its own
        files = rng.sample(areas, rng.randint(1, 2))  # independent of the acquisition record
        bounded = {i: rng.choice([True, False, g._Undecided("walk")]) for i in range(n)}
        if me < n and isinstance(bounded[me], g._Undecided):
            bounded[me] = True                             # own ticket: always decided by the full rule
        open_ = [i for i in range(n) if isinstance(bounded[i], g._Undecided)]
        if _bounded_admits(recs, bounded, me, files, monkeypatch):
            for outcome in itertools.product([True, False], repeat=len(open_)):
                full = {**bounded, **dict(zip(open_, outcome))}
                assert _full_admits(recs, full, me, files), (recs, bounded, me, files, outcome)
                checked += 1
    assert checked > 100


def test_undecided_has_no_truth_value():
    with pytest.raises(TypeError):
        bool(g._Undecided("x"))
    with pytest.raises(TypeError):
        list(g.possible_turns([("r", _rec(1, ["a"]))], lambda *a: None, lambda: None))
