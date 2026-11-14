"""Composition regressions: protocol-2 turns under upstream's 18-second preflight."""
from tests_turn import gh, seed_turn, merge, pr, g, NOW, H2563, R, delta, SimClock


def test_speculative_complete_snapshot_does_not_cleanup_on_rejected_approval(gh):
    fake = gh({2: pr(2, H2563, ["hermes-sync"])})
    for n in range(10, 13):
        seed_turn(fake, n, NOW - 3 * g._TURN_TTL)
    fake.prs[2]["reviews"] = []
    why = merge(2, H2563)
    assert why and "approval" in why
    assert not [c for c in fake.calls if "DELETE" in c]
    assert len(fake.refs) == 3
    assert any("graphql" in c for c in fake.calls)  # speculation actually read the cooled snapshot


def test_combined_hold_and_drift_keeps_approval_priority_and_never_acquires(gh):
    fake = gh({n: pr(n, H2563, ["hermes-sync"]) for n in (1, 2)}, compare=delta(["hermes-sync"]))
    seed_turn(fake, 1, NOW - 60)
    why = merge(2, H2563)
    assert "PR #1 holds" in why and "Also:" in why and "state commit(s) landed since then" in why
    assert fake.turn(2) is None
    fake.prs[2]["reviews"] = []
    why = merge(2, H2563)
    assert "approval" in why and "PR #1 holds" not in why
    assert fake.turn(2) is None


def test_slow_complete_snapshot_refuses_under_canonical_18_seconds(gh, monkeypatch):
    fake = gh({n: pr(n, H2563, ["hermes-sync"]) for n in (1, 2)})
    seed_turn(fake, 1, NOW - 60)
    sim = SimClock(monkeypatch, fake, per_call=9)
    why = merge(2, H2563)
    assert g.PREFLIGHT_TIMEOUT == 18
    assert why and "deadline exceeded" in why
    assert not sim.writes()
    assert all(t < 18 for t, _, _ in sim.starts)
