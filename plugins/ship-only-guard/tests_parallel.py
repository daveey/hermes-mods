"""Bounded parallel reads preserve deadline context and fail closed."""
import importlib.util
from pathlib import Path
import subprocess
import sys
import time
import pytest


def guard():
    spec = importlib.util.spec_from_file_location('parallel_guard', Path(__file__).with_name('guard.py'))
    g = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(g)
    return g


def test_parallel_inherits_deadline_and_stage():
    g = guard()
    g.PREFLIGHT_TIMEOUT = .15
    def slow():
        g._run_probe([sys.executable, '-c', 'import time; time.sleep(10)'], timeout=15)
    start = time.monotonic()
    result = g.bounded_preflight(g._parallel, [('carry fetch', slow, ()), ('drift check', slow, ())])
    assert 'deadline exceeded' in result and 'carry fetch' in result
    assert time.monotonic() - start < 2
    assert g._PREFLIGHT_DEADLINE.get() is None


def test_parallel_preserves_order_and_runs_concurrently():
    g = guard()
    import threading
    barrier = threading.Barrier(2)
    def read(value):
        barrier.wait(timeout=2)
        return value
    assert g._parallel([('first', read, (1,)), ('second', read, (2,))]) == [1, 2]


def test_turn_safe_never_swallows_deadline():
    g = guard()
    def expired():
        raise g._PreflightExpired('land-turn reads deadline exceeded')
    with pytest.raises(g._PreflightExpired):
        g._turn_safe(expired)


def test_flat_tree_workers_inherit_deadline(monkeypatch):
    g = guard()
    deadline = time.monotonic() + 5
    token = g._PREFLIGHT_DEADLINE.set(deadline)
    monkeypatch.setattr(g, '_flat_tree', lambda *a: g._PREFLIGHT_DEADLINE.get())
    try:
        assert g._flat_trees(['a', 'b'], 'owner/repo', '.', {}, {}) == [deadline, deadline]
    finally:
        g._PREFLIGHT_DEADLINE.reset(token)


@pytest.mark.parametrize('approval,hold,expected', [
    ('approval refused', 'turn held', 'approval refused'),
    (None, 'turn held', 'turn held'),
    (None, None, True),
])
def test_preflight_precedence_read_only_turn_and_final_drift(monkeypatch, approval, hold, expected):
    import json
    from types import SimpleNamespace
    g = guard()
    head = 'a' * 40
    data = dict(number=12, url='https://github.com/example/repo/pull/12', state='OPEN',
                baseRefName='state', headRefOid=head, statusCheckRollup=[
                    dict(__typename='CheckRun', status='COMPLETED', conclusion='SUCCESS')])
    monkeypatch.setattr(g, '_run_probe', lambda *a, **kw: SimpleNamespace(returncode=0, stdout=json.dumps(data)))
    monkeypatch.setattr(g, '_review_reply_refusal', lambda *a: None)
    done = []
    def approve(*args):
        done.append('approval')
        return approval
    def turn(*args):
        assert g._TURN_READ_ONLY.get()
        g._turn_delete('refs/land-turn/pr-1', 'example/repo', '.', {})
        done.append('hold')
        return hold
    def drift(*args):
        assert sorted(done) == ['approval', 'hold']
        done.append('drift')
        return None
    monkeypatch.setattr(g, '_approval_refusal', approve)
    monkeypatch.setattr(g, '_turn_hold_refusal', turn)
    monkeypatch.setattr(g, '_base_drift_refusal', drift)
    monkeypatch.setattr(g, '_gh_call', lambda *a, **kw: pytest.fail('speculative write'))
    monkeypatch.setattr(g, '_turn_acquire', lambda *a: pytest.fail('unexpected acquisition'))
    words = ['pr', 'merge', '12', '-R', 'example/repo', '--rebase', '--match-head-commit', head]
    assert g.bounded_preflight(g._state_merge, words, 'example/repo', '.', {}) == expected
    # Protocol 2 also reports drift to a blocked waiter; approval still wins.
    assert ('drift' in done) == (not approval)
    assert not g._TURN_READ_ONLY.get()
