"""Independent bounded exact-head review probes; fixture-only state."""
import threading
import pytest
from test_core_readers import api, board
from test_reader_batch import graph
from test_coordinator_observation import observed
from test_source_coordinator_core import readers_for, strict_empty, compose, drain, kanban_db
from test_feed import feedmod


def join_workers(lane):
    for worker in list(lane.active.values()):
        worker.join(5)
        assert not worker.is_alive()


@pytest.mark.parametrize('carry_clock', [False, True])
def test_reviewer_retry_parent_cas(observed, monkeypatch, carry_clock):
    r, f, lane, c, remote, now = observed
    original_base = lane._base
    def fail(*args):
        raise RuntimeError('fixture transient projection error')
    monkeypatch.setattr(lane, '_base', fail)
    with lane.lock:
        lane._queue(['S0'])
    lane.service(); join_workers(lane); lane.service()
    assert lane.projection_attempts['S0'].count == 1
    now[0] = lane.projection_attempts['S0'].retry_at + .001
    monkeypatch.setattr(lane, '_base', original_base)
    entered, release = threading.Event(), threading.Event()
    publish = f.publish_projections
    results = []
    def held(*args, **kwargs):
        if threading.current_thread().name == 'session-panel-projection-S0':
            entered.set(); assert release.wait(5)
            result = publish(*args, **kwargs)
            results.append(result['S0'].disposition)
            return result
        return publish(*args, **kwargs)
    monkeypatch.setattr(f, 'publish_projections', held)
    try:
        lane.service(); assert entered.wait(3)
        if carry_clock:
            old_admission = lane.store.pin(lane.chats['S0'])[1]
            c.execute('UPDATE kanban_sync_state SET pulled_at=1799999999'); c.commit()
            lane._capture_one(r, lane.external, (r.board_stamps(), r.external_stamps()))
            assert lane.store.pin(lane.chats['S0'])[1] == old_admission
        release.set(); join_workers(lane); lane.service()
        # Parent-CAS supersession is not another projector failure and must
        # retain the already-consumed finite failure budget until settlement.
        if 'S0' in lane.projection_attempts:
            assert lane.projection_attempts['S0'].count == 1
        if carry_clock:
            assert results[0] == 'superseded'
            with lane.lock:
                runnable = ('S0' in lane.active or 'S0' not in lane.projection_attempts
                            or lane._projection_ready('S0'))
            assert runnable, 'same-admission observation CAS stranded a real retry'
            drain(lane)
            assert 'S0' not in lane.pending
        else:
            assert 'S0' not in lane.pending
    finally:
        release.set(); join_workers(lane)


@pytest.mark.parametrize('queue_before_exit', [False, True])
def test_reviewer_served_age_is_retired(api, board, monkeypatch, queue_before_exit):
    path, conn = board
    r = readers_for(api, path, monkeypatch)
    monkeypatch.setattr(r, 'external_capture', strict_empty)
    for s in ('A', 'C'):
        kanban_db.create_task(conn, title=s, session_id=s)
    f = feedmod.Feed(r, emit=lambda _: None)
    f.watch(['A', 'C']); lane = compose(f, r)
    entered, release = threading.Event(), threading.Event()
    original = lane._worker_finished
    def held():
        if threading.current_thread().name == 'session-panel-projection-A':
            entered.set(); assert release.wait(5)
        return original()
    try:
        drain(lane)
        monkeypatch.setattr(lane, '_worker_finished', held)
        with lane.lock:
            lane._queue(['A'])
        lane.service(); assert entered.wait(3)
        assert 'A' not in lane.projection_attempts and 'A' not in lane.pending
        if not queue_before_exit:
            release.set(); join_workers(lane); lane.service()
        with lane.lock:
            lane._queue(['C'])
            lane._queue(['A'])
            ages = dict(lane.pending)
        assert not queue_before_exit or lane.active['A'].is_alive()
        assert ages['C'] < ages['A'], 'new A episode inherited already-served age and overtook waiting C'
    finally:
        release.set(); join_workers(lane)
        f.stopped.set(); lane.close(); r.release(); conn.close()
