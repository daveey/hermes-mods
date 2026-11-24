"""Main's producer admission survives the reserved-route conflict resolution."""
from test_source_coordinator_core import api, board, acquisition, compose, drain, strict_empty, kanban_db
from test_feed import feedmod


def test_composed_tick_keeps_leading_trailing_floor_and_quiet_safety(acquisition, monkeypatch):
    r, *_ = acquisition
    monkeypatch.setattr(r, 'external_capture', strict_empty)
    now, source, calls = {'t': 1000.0}, {'revision': 1}, []
    conn = acquisition[-1]
    task = kanban_db.create_task(conn, title='Initial producer input', session_id='S')
    monkeypatch.setattr(r, 'reconciliation_stamp', lambda: source['revision'])
    monkeypatch.setattr(r, 'reconcile_sources', lambda sessions: calls.append((now['t'], list(sessions))) or set())
    monkeypatch.setattr(r, 'reconcile_scoped', True)
    monkeypatch.setattr(r, 'reconcile_min_interval_s', 3.0)
    monkeypatch.setattr(r, 'reconcile_safety_s', 30.0)
    f = feedmod.Feed(r, emit=lambda _: None, clock=lambda: now['t'])
    f.watch(['S'])
    lane = compose(f, r)
    try:
        drain(lane); f.tick()
        assert calls == [(1000.0, ['S'])]
        for t in (1000.1, 1001.0, 1002.9):
            now['t'] = t
            source['revision'] += 1
            conn.execute('UPDATE tasks SET title=? WHERE id=?', (str(t), task)); conn.commit()
            drain(lane); f.tick()
            assert len(calls) == 1
            assert f._producer_pending == {'S': 1003.0}
        now['t'] = 1003.0
        f.tick()
        assert calls == [(1000.0, ['S']), (1003.0, ['S'])]
        assert not f._producer_pending
        now['t'] = 1032.9
        f.tick()
        assert len(calls) == 2
        now['t'] = 1033.0
        f.tick()
        assert calls[-1] == (1033.0, ['S']) and len(calls) == 3
    finally:
        f.stopped.set(); lane.close(); r.release()


def test_composed_tick_drains_budget_carry_without_another_source_write(acquisition, monkeypatch):
    r, *_ = acquisition
    monkeypatch.setattr(r, 'external_capture', strict_empty)
    monkeypatch.setattr(r, 'reconciliation_stamp', lambda: 1)
    monkeypatch.setattr(r, 'reconcile_scoped', True)
    monkeypatch.setattr(r, 'reconcile_budgeted', True)
    monkeypatch.setattr(r, 'reconcile_min_interval_s', 0.0)
    calls = []

    def reconcile(sessions, deadline=None):
        assert deadline is not None
        calls.append(list(sessions))
        r.reconcile_unreached = list(sessions)[1:]
        return set()

    monkeypatch.setattr(r, 'reconcile_sources', reconcile)
    f = feedmod.Feed(r, emit=lambda _: None)
    f.watch(['A', 'B'])
    lane = compose(f, r)
    try:
        drain(lane); f.tick()
        assert len(calls) == 1 and set(calls[0]) == {'A', 'B'}
        carry = set(calls[0][1:])
        assert f._producer_carry == carry
        assert carry.isdisjoint(f._reconcile_stamps)
        assert {'A', 'B'} <= set(f.bundles), 'budget carry must not hold presentation'
        f.tick()
        assert len(calls) == 2 and set(calls[1]) == carry
        assert not f._producer_carry
        f.tick()
        assert len(calls) == 2, 'unchanged settled sources do not rerun'
    finally:
        f.stopped.set(); lane.close(); r.release()
