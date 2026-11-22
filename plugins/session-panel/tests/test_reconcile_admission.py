"""Source reconciliation is not a presentation side effect."""
import pytest
from test_feed import make


def setup():
    f, r, pushed, t = make()
    source = {'version': 1}
    calls = []
    r.reconciliation_stamp = lambda: source['version']
    r.reconcile_sources = lambda sessions: calls.append(list(sessions))
    r.panel_files_stamp = lambda: r.stamp
    r.panel_stamps = lambda ids: {s: r.stamp for s in ids}
    return f, r, source, calls


def test_source_producer_is_separate_from_compute_and_ignores_own_writes():
    f, r, source, calls = setup()
    f.changes(['S1'])  # request-side cold hydration must not write
    assert calls == []
    f.tick()
    assert calls == [['S1']]
    for _ in range(10):
        r.stamp += 1  # request-store write, not a source change
        f.tick()
    assert calls == [['S1']]
    source['version'] += 1
    r.stamp += 1
    r.links['S1'][0]['title'] = 'Source changed'
    f.tick()
    assert calls == [['S1'], ['S1']]
    assert f.bundles['S1']['bundle']['tasks'][0]['title'] == 'Source changed'


def test_state_only_membership_change_admits_producer_without_hydrating():
    f, r, source, calls = setup()
    f.watch(['S1'])
    f.tick()
    held = f.bundles['S1']['bundle']
    source['version'] += 1  # unchanged presentation stamps
    f.tick()
    assert calls == [['S1'], ['S1']]
    assert f.bundles['S1']['bundle'] is held


def test_failed_producer_does_not_consume_source_admission():
    f, r, source, calls = setup()
    f.watch(['S1'])
    def failed(sessions):
        raise OSError('source unavailable')
    r.reconcile_sources = failed
    with pytest.raises(OSError):
        f.tick()
    assert f._reconcile_stamps == {}
    assert 'S1' in f._retry
    r.reconcile_sources = lambda sessions: calls.append(list(sessions))
    f.tick()  # no further source write needed
    assert calls == [['S1']]
    assert 'S1' not in f._retry


def test_forget_and_reregister_reconciles_even_without_a_source_write():
    f, r, source, calls = setup()
    f.watch(['S1'])
    f.tick()
    with f.lock:
        f._forget('S1')
    f.watch(['S1'])
    f.tick()
    assert calls == [['S1'], ['S1']]


def test_concurrent_source_write_during_producer_is_not_acknowledged():
    f, r, source, calls = setup()
    def reconcile(sessions):
        calls.append(list(sessions))
        if len(calls) == 1:
            source['version'] += 1
            r.stamp += 1
    r.reconcile_sources = reconcile
    f.watch(['S1'])
    f.tick()
    assert f._reconcile_stamps['S1'] == 1
    f.tick()
    assert calls == [['S1'], ['S1']]
