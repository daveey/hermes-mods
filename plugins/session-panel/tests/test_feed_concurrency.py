"""Deterministic overlap regressions: no sleeps or timing-based dedup assertions."""
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from test_feed import make, feedmod


def test_cold_changes_and_tick_share_work_without_blocking_disjoint_session():
    f, r, pushed, t = make()
    entered, release, joined = (threading.Event() for _ in range(3))
    calls = []
    original = r.linked

    def linked(ids):
        calls.append(ids[0])
        if ids[0] == 'S1':
            entered.set()
            assert release.wait(5)
        return original(ids)

    r.linked = linked
    with ThreadPoolExecutor(4) as pool:
        first = pool.submit(f.changes, ['S1'])
        assert entered.wait(5)
        # Instrument the join rather than sleeping and hoping the waiter ran.
        old_wait = getattr(f, '_wait_flight', None)
        if old_wait:
            def wait(flight):
                joined.set()
                return old_wait(flight)
            f._wait_flight = wait
        second = pool.submit(f.changes, ['S1'])
        tick = pool.submit(f.tick)
        try:
            other = pool.submit(f.changes, ['S2']).result(3)
            assert set(other['bundles']) == {'S2'}
            if old_wait:
                assert joined.wait(3)
        finally:
            release.set()
        a, b = first.result(5), second.result(5)
        tick.result(5)
    assert calls.count('S1') == 1
    assert a['bundles']['S1'] == b['bundles']['S1']
    assert f.status()['failures'] == {}


def test_reentrant_compute_fails_without_deadlock_and_retry_cleans_up():
    f, r, _, _ = make()
    original = r.linked
    reentered = []

    def linked(ids):
        if not reentered:
            reentered.append(True)
            # A reader callback must not wait on this thread's own unfinished work.
            f._compute_all(['S1'])
        return original(ids)

    r.linked = linked
    f.changes(['S1'])
    assert 'S1' in f.status()['failures']
    r.linked = original
    assert 'S1' in f.changes(['S1'])['bundles']
    assert f.status()['failures'] == {}


def test_failed_flight_is_shared_then_next_request_retries():
    f, r, _, _ = make()
    entered, release, joined = (threading.Event() for _ in range(3))
    calls = []
    original = r.linked

    def linked(ids):
        calls.append(1)
        entered.set()
        assert release.wait(5)
        raise ValueError('reader failed')

    r.linked = linked
    old_wait = getattr(f, '_wait_flight', None)
    if old_wait:
        def wait(flight):
            joined.set()
            return old_wait(flight)
        f._wait_flight = wait
    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(f.changes, ['S1'])
        assert entered.wait(3)
        second = pool.submit(f.changes, ['S1'])
        try:
            if old_wait:
                assert joined.wait(3)
        finally:
            release.set()
        assert first.result(5)['bundles'] == second.result(5)['bundles'] == {}
    assert len(calls) == 1
    assert f.status()['failures']['S1']['count'] == 1
    r.linked = original
    assert 'S1' in f.changes(['S1'])['bundles']


def test_cold_request_does_not_wait_behind_unstarted_tick_sessions():
    f, r, _, _ = make()
    f.watch(['S1', 'S2'])
    entered, release = threading.Event(), threading.Event()
    original = r.linked
    calls = []

    def linked(ids):
        calls.append(ids[0])
        if ids[0] == 'S1':
            entered.set()
            assert release.wait(5)
        return original(ids)

    r.linked = linked
    with ThreadPoolExecutor(2) as pool:
        tick = pool.submit(f.tick)
        assert entered.wait(3)
        try:
            answer = pool.submit(f.changes, ['S2']).result(2)
            assert 'S2' in answer['bundles']
        finally:
            release.set()
        tick.result(5)
    assert calls.count('S1') == 1


def test_expired_bundle_is_not_vouched_during_a_long_pass():
    f, _, pushed, t = make()
    f.changes(['S1'])
    t['now'] += f.full_every_s + 1
    f._publish({}, heartbeat=True)
    assert pushed[-1]['fresh'] == []


@pytest.mark.parametrize('at_exit', [False, True])
def test_reader_scope_failure_releases_every_owned_session(at_exit):
    from contextlib import contextmanager
    f, r, _, _ = make()

    @contextmanager
    def batch(sessions):
        if not at_exit:
            raise ValueError('entry failed')
        yield
        raise ValueError('exit failed')

    r.batch = batch
    with pytest.raises(ValueError):
        f.changes(['S1', 'S2'])
    assert f._flights == {}
    del r.batch
    assert set(f.changes(['S1', 'S2'])['bundles']) == {'S1', 'S2'}


def test_late_session_from_older_snapshot_cannot_replace_newer_compute():
    from contextlib import contextmanager
    f, r, pushed, t = make()
    original = r.linked
    entered, release = threading.Event(), threading.Event()
    owner = threading.local()

    @contextmanager
    def batch(sessions):
        owner.snapshot = {s: [dict(row, status='review') for row in original([s])] for s in sessions}
        try:
            yield
        finally:
            del owner.snapshot

    def linked(ids):
        snapshot = getattr(owner, 'snapshot', None)
        if snapshot is not None:
            if ids[0] == 'S1':
                entered.set()
                assert release.wait(5)
            return snapshot[ids[0]]
        return [dict(row, status='blocked') for row in original(ids)]

    r.batch, r.linked = batch, linked
    with ThreadPoolExecutor(1) as pool:
        old = pool.submit(f.changes, ['S1', 'S2'])
        assert entered.wait(3)
        try:
            f._publish({'S2': f.compute('S2')})
            t['now'] += f.full_every_s + 1
        finally:
            release.set()
        old.result(5)
    assert f.bundles['S2']['bundle']['tasks'][0]['status'] == 'blocked'
    assert 'S1' not in pushed[-1]['fresh'], 'old snapshots are not re-dated at completion'


def test_tick_joining_pre_write_cold_flight_retries_without_losing_the_cursor():
    f, r, pushed, _ = make()
    entered, release, joined = (threading.Event() for _ in range(3))
    original, wait = r.linked, f._wait_flight
    calls = []

    def linked(ids):
        rows = [dict(row) for row in original(ids)]
        calls.append(1)
        if len(calls) == 1:
            entered.set()
            assert release.wait(5)
        return rows

    def joining(flight):
        joined.set()
        return wait(flight)

    r.linked, f._wait_flight = linked, joining
    with ThreadPoolExecutor(2) as pool:
        cold = pool.submit(f.changes, ['S1'])
        assert entered.wait(3)
        r.links['S1'][0]['status'] = 'review'
        r.stamp += 1
        r.events = [('t_aaaaaa1', 'S1')]
        tick = pool.submit(f.tick)
        try:
            assert joined.wait(3)
        finally:
            release.set()
        cold.result(5); tick.result(5)
    assert len(calls) == 1, 'no concurrent duplicate rebuild'
    assert 'S1' not in pushed[-1]['fresh'], 'known-dirty snapshot is not vouched'
    f.tick()  # unchanged stamps, but the consumed change still has an owner
    assert len(calls) == 2
    assert f.bundles['S1']['bundle']['tasks'][0]['status'] == 'review'
    assert not f._retry
