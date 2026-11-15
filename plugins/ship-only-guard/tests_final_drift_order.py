"""Final mutable-base observations and oldest-holder budget regressions."""
import threading
from types import SimpleNamespace

from tests_turn import g, gh, pr, seed_turn, merge, H2563, NOW, delta


def test_final_drift_must_observe_push_during_parallel_approval(gh, monkeypatch):
    fake = gh({1: pr(1, H2563, ['hermes-sync'])})
    seed_turn(fake, 1, NOW - 1800)
    read = threading.Event()
    original = g._turn_yielded

    def yielded(*args):
        result = original(*args)
        read.set()
        return result

    monkeypatch.setattr(g, '_turn_yielded', yielded)
    original_approval = g._approval_refusal

    def approval(*args):
        result = original_approval(*args)
        assert read.wait(2)
        fake.compare = delta(['hermes-sync'])
        return result

    monkeypatch.setattr(g, '_approval_refusal', approval)
    why = merge(1, H2563)
    assert why and 'state commit(s) landed since then' in why
    assert len([c for c in fake.calls if any('/compare/' in a for a in c)]) >= 2


def test_young_waiters_cannot_spend_oldest_budget(gh, monkeypatch):
    fake = gh({n: pr(n, H2563, ['hermes-sync']) for n in (1, 2, 3)})
    for n in (1, 2, 3):
        seed_turn(fake, n, NOW - 100 + n)
    clock = [0]
    monkeypatch.setattr(g, 'time', SimpleNamespace(monotonic=lambda: clock[0]))
    original = g.subprocess.run

    def run(argv, **kw):
        if argv[:3] == ['gh', 'pr', 'view']:
            if int(argv[3]) == 1:
                clock[0] += 9
            else:
                clock[0] += kw['timeout']
                raise g.subprocess.TimeoutExpired(argv, kw['timeout'])
        return original(argv, **kw)

    monkeypatch.setattr(g.subprocess, 'run', run)
    assert merge(1, H2563) is None
    assert not any(c[:3] == ['gh', 'pr', 'view'] and int(c[3]) != 1 for c in fake.calls)
