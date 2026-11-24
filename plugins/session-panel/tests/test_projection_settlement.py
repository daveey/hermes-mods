"""Successful projection episode settlement, before and after immutable pin."""
import threading

from test_source_coordinator_core import api, board, kanban_db, readers_for, strict_empty, compose, drain
from test_feed import feedmod


def test_successful_retry_keeps_post_pin_work_runnable_without_new_source(api, board, monkeypatch):
    path, conn = board
    r = readers_for(api, path, monkeypatch)
    monkeypatch.setattr(r, 'external_capture', strict_empty)
    kanban_db.create_task(conn, title='Stable work', session_id='S')
    f = feedmod.Feed(r, emit=lambda _: None)
    f.watch(['S'])
    lane = compose(f, r)
    entered, release = threading.Event(), threading.Event()
    try:
        drain(lane)
        original = lane._base
        def held(*args):
            entered.set()
            assert release.wait(5)
            return original(*args)
        monkeypatch.setattr(lane, '_base', held)
        with lane.lock:
            lane._queue(['S'])
            attempt = lane.projection_attempts['S']
            attempt.count, attempt.retry_at = 1, f.clock() - 1
        lane.service()
        assert entered.wait(3)
        with lane.lock:
            lane._queue(['S'])
        release.set()
        for worker in list(lane.active.values()):
            worker.join(5); assert not worker.is_alive()
        assert 'S' in lane.pending, 'post-pin work was erased'
        assert lane._projection_ready('S'), 'successful retry left post-pin work permanently in-flight'
        assert lane.projection_attempts['S'].count == 1, 'success invented a fresh failure budget'
        monkeypatch.setattr(lane, '_base', original)
        drain(lane)
        assert 'S' not in lane.pending and 'S' not in lane.projection_attempts
        assert 'S' not in f._board_failures and 'S' not in f._retry
    finally:
        release.set(); f.stopped.set(); lane.close(); r.release(); conn.close()


def test_successful_publication_serves_pre_pin_requeue_and_clears_failure(api, board, monkeypatch):
    path, conn = board
    r = readers_for(api, path, monkeypatch)
    monkeypatch.setattr(r, 'external_capture', strict_empty)
    kanban_db.create_task(conn, title='Stable work', session_id='S')
    f = feedmod.Feed(r, emit=lambda _: None)
    f.watch(['S'])
    lane = compose(f, r)
    entered, release = threading.Event(), threading.Event()
    original = lane._project
    def held(s):
        entered.set(); assert release.wait(5)
        return original(s)
    try:
        drain(lane)
        monkeypatch.setattr(lane, '_project', held)
        with lane.lock:
            lane._queue(['S'])
        lane.service()
        assert entered.wait(3)
        with lane.lock:
            lane._queue(['S'])
        release.set()
        for worker in list(lane.active.values()):
            worker.join(5); assert not worker.is_alive()
        assert 'S' not in lane.pending
        assert 'S' not in f._board_failures, 'served pre-pin queue retained false failure'
        assert 'S' not in f._retry
    finally:
        release.set(); f.stopped.set(); lane.close(); r.release(); conn.close()


def test_reload_rearms_inflight_projection_retry_without_resetting_budget(api, board, monkeypatch):
    from test_source_lifetime_core import RetiredReaders
    path, conn = board
    r = readers_for(api, path, monkeypatch)
    monkeypatch.setattr(r, 'external_capture', strict_empty)
    now = [1800000000.]
    f = feedmod.Feed(r, emit=lambda _: None, clock=lambda: now[0])
    f.watch(['S'])
    lane = compose(f, r)
    new = None
    entered, release = threading.Event(), threading.Event()
    try:
        drain(lane)
        original = lane._base
        def held(*args):
            entered.set(); assert release.wait(5)
            return original(*args)
        monkeypatch.setattr(lane, '_base', held)
        with lane.lock:
            lane._queue(['S'])
            age = lane.pending['S']
            attempt = lane.projection_attempts['S']
            attempt.count, attempt.retry_at = 1, now[0] - 1
        lane.service()
        assert entered.wait(3)
        monkeypatch.setattr(feedmod, 'SNAPSHOT_READER_FACTORY', lambda: r)
        new = feedmod.Feed(RetiredReaders(), emit=lambda _: None, clock=lambda: now[0])
        new.resume(f)
        release.set()
        for worker in list(lane.active.values()):
            worker.join(5); assert not worker.is_alive()
        owner = new.ensure_snapshot_service('settlement-current', lambda: r)
        held_attempt = owner.projection_attempts['S']
        assert held_attempt.count == 1
        assert held_attempt.retry_at is not None, 'reload stranded a consumed retry as permanently in-flight'
        assert owner.pending['S'] == age
        release.set()
        for worker in list(lane.active.values()):
            worker.join(5); assert not worker.is_alive()
        now[0] += 31
        drain(owner)
        assert 'S' not in owner.pending and 'S' not in owner.projection_attempts
    finally:
        release.set(); f.stopped.set(); lane.close()
        if new is not None:
            new.stopped.set(); new._source_coordinator.close()
        r.release(); conn.close()
