"""Bound source reconciliation without losing the final write of a burst."""
from test_feed import make


def configured():
    f, r, _, clock = make()
    r.reconcile_scoped = True
    r.reconcile_min_interval_s = 3.0
    r.reconcile_safety_s = 30.0
    r.reconciliation_stamp = lambda: r.stamp
    calls = []
    r.reconcile_sources = lambda sessions: calls.append((clock['now'], list(sessions))) or set()
    f.watch(['S1'])
    f.tick()
    return f, r, clock, calls


def test_floor_keeps_trailing_work_without_another_source_write():
    f, r, clock, calls = configured()
    start = clock['now']
    for dt in (0.1, 1, 2.9):
        clock['now'] = start + dt
        r.lineages['S1'] = ['S1', str(dt)]
        r.stamp += 1
        f.tick()
        assert len(calls) == 1
        assert f._producer_pending == {'S1': start + 3}
    assert 0 < f._due(clock['now'])[0] < 0.2
    clock['now'] = start + 3
    f.tick()
    assert calls == [(start, ['S1']), (start + 3, ['S1'])]
    assert not f._producer_pending
    assert f._producer_lineage['S1'] == ('S1', '2.9')


def test_quiet_inputs_skip_compute_and_reconcile_until_safety():
    f, r, clock, calls = configured()
    start = clock['now']
    f._compute_all = lambda *a, **k: (_ for _ in ()).throw(AssertionError('unchanged compute'))
    for dt in (1, 3, 29.9):
        clock['now'] = start + dt
        f.tick()
        assert len(calls) == 1
    clock['now'] = start + 30
    assert f._due(clock['now'])[0] == 0
    f.tick()
    assert calls == [(start, ['S1']), (start + 30, ['S1'])]
    assert f._due(clock['now'])[0] == 30


def test_deferred_retry_is_not_spent_while_floor_delays_it():
    f, r, clock, calls = configured()
    start = clock['now']
    r.reconcile_sources = lambda sessions: calls.append((clock['now'], list(sessions))) or {'S1'}
    clock['now'] = start + 3
    r.lineages['S1'] = ['S1', 'parent']
    r.stamp += 1
    f.tick()
    spent = dict(f._budgets['reconcile'])
    clock['now'] += 0.2
    f.tick()
    assert f._budgets['reconcile'] == spent
    assert f._producer_pending == {'S1': start + 6}
    r.reconcile_sources = lambda sessions: calls.append((clock['now'], list(sessions))) or set()
    clock['now'] = start + 6
    f.tick()
    assert not f._reconcile_deferred and not f._producer_pending
    assert calls[-1] == (start + 6, ['S1'])


def test_failed_capture_does_not_rollback_actual_writer_floor():
    f, r, clock, calls = configured()
    start = clock['now']
    clock['now'] = start + 3
    r.lineages['S1'] = ['S1', 'parent']
    r.stamp += 1
    original = f._panel_dirty
    count = {'n': 0}

    def fail_after_producer(sessions):
        count['n'] += 1
        if count['n'] == 2:
            raise OSError('post-producer capture failed')
        return original(sessions)

    f._panel_dirty = fail_after_producer
    import pytest
    with pytest.raises(OSError):
        f.tick()
    assert len(calls) == 2
    f._panel_dirty = original
    clock['now'] += 0.2
    f.tick()
    assert len(calls) == 2
    assert f._producer_pending == {'S1': start + 6}
    clock['now'] = start + 6
    f.tick()
    assert len(calls) == 3


def test_forgetting_chat_removes_floor_and_safety_wakes():
    f, r, clock, calls = configured()
    r.lineages['S1'] = ['S1', 'parent']
    r.stamp += 1
    f.tick()
    assert f._producer_pending
    with f.lock:
        f._forget('S1')
    assert not f._producer_pending
    assert f._due(clock['now']) == (None, None)
