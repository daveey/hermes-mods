"""Real keyed/projection lock ordering and watch-incarnation fences."""
import threading

import pytest
from test_source_coordinator_core import (
    api, board, readers_for, feedmod, compose, drain, strict_empty, kanban_db,
)


def test_keyed_admission_does_not_deadlock_projection_base(api, board, monkeypatch):
    path, conn = board
    r = readers_for(api, path, monkeypatch)
    monkeypatch.setattr(r, 'external_capture', strict_empty)
    kanban_db.create_task(conn, title='Lock probe', session_id='S')
    f = feedmod.Feed(r, emit=lambda _: None)
    f.watch(['S']); lane = compose(f, r)
    errors, workers = [], []
    entered, admitting = threading.Event(), threading.Event()
    try:
        f.changes(['S']); drain(lane)
        original_lock = lane.lock
        class BoundedLock:
            # A failure must unwind and join, not leave deadlocked test threads.
            def __enter__(self):
                if not original_lock.acquire(timeout=2):
                    raise TimeoutError('keyed/projection lock inversion')
                return self
            def __exit__(self, *args):
                original_lock.release()
        lane.lock = BoundedLock()
        original_base, original_admit = lane._base, lane._admit
        def held_base(*args):
            entered.set()
            assert admitting.wait(2)
            return original_base(*args)
        def admit(*args):
            admitting.set()
            return original_admit(*args)
        monkeypatch.setattr(lane, '_base', held_base)
        monkeypatch.setattr(lane, '_admit', admit)
        with lane.lock:
            lane._queue(['S'])
        projection = threading.Thread(target=lane._project, args=('S',))
        workers.append(projection); projection.start()
        assert entered.wait(2)
        def queue_key():
            try:
                f.queue_keyed({'S', 'unwatched'})
            except Exception as exc:
                errors.append(exc)
        queued = threading.Thread(target=queue_key)
        workers.append(queued); queued.start()
        queued.join(4); projection.join(4)
        assert not queued.is_alive() and not projection.is_alive()
        assert not errors, errors
        assert f._keyed_pending == {'S'}
        assert set(lane.pending) == {'S'}
    finally:
        admitting.set()
        for worker in workers:
            worker.join(4)
        f.stopped.set(); lane.close(); r.release(); conn.close()


@pytest.mark.parametrize('rewatch', [False, True])
def test_projection_base_unlock_preserves_watch_incarnation(api, board, monkeypatch, rewatch):
    path, conn = board
    r = readers_for(api, path, monkeypatch)
    monkeypatch.setattr(r, 'external_capture', strict_empty)
    kanban_db.create_task(conn, title='Original watch', session_id='S')
    f = feedmod.Feed(r, emit=lambda _: None)
    f.watch(['S']); lane = compose(f, r)
    entered, release = threading.Event(), threading.Event()
    worker = None
    try:
        f.changes(['S']); drain(lane)
        token = f._watch_tokens['S']
        original_base = lane._base
        def held_base(*args):
            entered.set()
            assert release.wait(3)
            return original_base(*args)
        monkeypatch.setattr(lane, '_base', held_base)
        outcomes = []
        original_publish = f.publish_projections
        def publish(*args, **kwargs):
            result = original_publish(*args, **kwargs)
            outcomes.extend(v.disposition for v in result.values())
            return result
        monkeypatch.setattr(f, 'publish_projections', publish)
        worker = threading.Thread(target=lane._project, args=('S',))
        worker.start(); assert entered.wait(2)
        # The old pin must not publish into either absent membership or a new
        # same-name watch. Do not advance coordinator registration artificially.
        with f.lock:
            f._forget('S')
        if rewatch:
            f.watch(['S'])
            assert f._watch_tokens['S'] is not token
        release.set(); worker.join(3)
        assert not worker.is_alive()
        assert outcomes == ['superseded']
        assert 'S' not in f.bundles
        assert not getattr(f, '_keyed_pending', set())
    finally:
        release.set()
        if worker:
            worker.join(4)
        f.stopped.set(); lane.close(); r.release(); conn.close()
