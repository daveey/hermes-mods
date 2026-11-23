"""Adverse role/projection retries through disposable real CoreReaders."""
import threading
from test_source_coordinator_core import api, board, kanban_db, readers_for, feedmod, compose, drain, strict_empty


def test_review_due_source_retry_then_reader_factory_failure(api, board, monkeypatch):
    path, conn = board
    r = readers_for(api, path, monkeypatch)
    now = [1800000000.]
    def fail_capture(*args, **kwargs):
        raise RuntimeError('strict capture unavailable')
    monkeypatch.setattr(r, 'external_capture', fail_capture)
    f = feedmod.Feed(r, emit=lambda _: None, clock=lambda: now[0])
    f.watch(['S']); lane = compose(f, r)
    try:
        f.changes(['S']); drain(lane)
        now[0] = lane.retry_at()
        assert now[0] is not None
        factory_calls = []
        def fail_factory():
            if threading.current_thread().name.endswith('external'):
                factory_calls.append(1)
                raise RuntimeError('reader factory unavailable on retry')
            return r
        lane.reader_factory = fail_factory
        drain(lane)
        role_due = lane.role_attempts['external'].retry_at
        source_due = lane.attempts[lane.external].retry_at
        selected_due = lane.retry_at()
        # Freeze the exact observed source stamps for this unchanged-input
        # counterfactual, independently of unrelated source-clock renewals.
        local_stamp, external_stamp = lane._local_stamp, lane._external_stamp[1]
        monkeypatch.setattr(r, 'board_stamps', lambda: local_stamp)
        monkeypatch.setattr(r, 'external_stamps', lambda: external_stamp)
        ticks, capped = [], threading.Event()
        original_step = lane.step
        def counted_step():
            ticks.append(1)
            original_step()
            if len(ticks) >= 100:
                lane.service_stop.set()
                capped.set()
        monkeypatch.setattr(lane, 'step', counted_step)
        lane.start_service()
        spun = capped.wait(2)
        print('REVIEW_RETRY_CLOCK', now[0], role_due, source_due, selected_due,
              'SERVICE_TICKS', len(ticks), 'FACTORY_CALLS', len(factory_calls), 'SPUN', spun)
        assert role_due > now[0]
        assert selected_due > now[0], 'past source deadline spins service while role backoff prevents launch'
        assert not spun
        lane.service_stop.set(); lane.service_wake.set(); lane.service_worker.join(2)
        assert not lane.service_worker.is_alive()
        for _ in range(3):
            now[0] = lane.role_attempts['external'].retry_at
            assert now[0] is not None
            drain(lane)
        assert len(factory_calls) == 4
        assert lane.role_attempts['external'].retry_at is None
        assert lane.retry_at() is None, 'exhausted role must suppress the overdue source deadline'
    finally:
        f.stopped.set(); lane.close(); r.release(); conn.close()


def test_review_full_cannot_restart_exhausted_projection(api, board, monkeypatch):
    path, conn = board
    r = readers_for(api, path, monkeypatch)
    monkeypatch.setattr(r, 'external_capture', strict_empty)
    now = [1800000000.]
    kanban_db.create_task(conn, title='Local', session_id='S')
    f = feedmod.Feed(r, emit=lambda _: None, clock=lambda: now[0])
    f.watch(['S']); lane = compose(f, r)
    calls = []
    try:
        f.changes(['S']); drain(lane)
        def fail(*args, **kwargs):
            calls.append(1)
            raise RuntimeError('projector unavailable')
        monkeypatch.setattr(f, 'project_board', fail)
        conn.execute('UPDATE tasks SET title=?', ('Changed',)); conn.commit()
        drain(lane)
        for _ in range(3):
            now[0] = lane.retry_at()
            assert now[0] is not None
            drain(lane)
        assert len(calls) == 4 and lane.retry_at() is None
        for _ in range(3):
            assert lane.full(['S']) == {}
        print('REVIEW_PROJECTION_CALLS', len(calls))
        assert len(calls) == 4, 'unchanged full reads restart exhausted pure projection budget'
        # A genuinely changed acquired source is a new budget, unlike a full
        # canonical read with unchanged board/demand inputs.
        conn.execute('UPDATE tasks SET title=?', ('New source value',)); conn.commit()
        drain(lane)
        assert len(calls) == 5
        assert lane.retry_at() > now[0]
        for _ in range(3):
            assert lane.full(['S']) == {}
        assert len(calls) == 5, 'full reads must respect future backoff too'
    finally:
        f.stopped.set(); lane.close(); r.release(); conn.close()
