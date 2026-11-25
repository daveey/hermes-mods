"""Reload must settle composed observation parents and current source inventory."""
import pytest

from test_coordinator_observation import observed, graph, api, board
from test_source_coordinator_core import compose, drain, readers_for, strict_empty, kanban_db
from test_feed import feedmod


def install(f, readers, now, monkeypatch):
    monkeypatch.setattr(feedmod, 'SNAPSHOT_READER_FACTORY', lambda: readers)
    successor = feedmod.Feed(readers, emit=lambda _: None, clock=lambda: now[0]).resume(f)
    pending = successor._source_coordinator
    # Resume withdraws old capacity before candidate installation. Join exact
    # retired workers, including ones spawned by the last completion service;
    # never assume that a completion record implies thread exit.
    predecessor = pending.predecessor
    while predecessor is not None:
        for worker in predecessor.workers:
            worker.join(3)
            assert not worker.is_alive()
        predecessor = predecessor.parent
    successor.ensure_snapshot_service(pending.api_generation, pending.reader_factory)
    owner = successor._source_coordinator
    assert not getattr(owner, 'pending_install', False)
    return successor, owner


def settle(owner, now):
    # Bounded manual drive of the actual service, no extra service threads or
    # sleep-based convergence. Failed acquisitions retain their finite budget.
    for rounds in range(1, 61):
        owner.step()
        for worker in list(owner.acquiring.values()) + list(owner.active.values()):
            worker.join(3)
            assert not worker.is_alive()
        owner.service()
        at = owner.retry_at()
        if at is not None and at > now[0]:
            now[0] = at
        if not owner.pending and not owner.active and not owner.acquiring and at is None:
            return rounds
    pytest.fail('reload failed to settle in 60 manual service rounds')


@pytest.mark.parametrize('external', ['healthy', 'unavailable', 'no_observation', 'diagnostic_clear_old_projection'])
def test_reload_after_observation_settles_at_unchanged_stamps(observed, monkeypatch, external):
    r, f, lane, c, remote, now = observed
    if external != 'no_observation':
        c.execute('UPDATE kanban_sync_state SET pulled_at=1800000001'); c.commit()
        lane._capture_one(r, lane.external, (r.board_stamps(), r.external_stamps()))
        assert f.bundles['S0']['board_projection'].observation_parent is not None
    drain(lane)
    successor, owner = install(f, r, now, monkeypatch)
    if external != 'healthy':
        def fail(*args, **kwargs):
            raise RuntimeError('strict unavailable')
        monkeypatch.setattr(r, 'external_capture', fail)
    if external == 'diagnostic_clear_old_projection':
        for held in successor.bundles.values():
            held.pop('board_projection', None)
    dispositions = []
    original = successor.publish_projections
    def publish(store, projections):
        out = original(store, projections)
        dispositions.extend(result.disposition for result in out.values())
        return out
    monkeypatch.setattr(successor, 'publish_projections', publish)
    try:
        successor.queue_keyed(['S0', 'S1'])
        assert settle(owner, now) < 60
        assert len(dispositions) < 20
        assert set(dispositions) <= {'stored', 'no_change'}
        for session in ('S0', 'S1'):
            projection = successor.bundles[session]['board_projection']
            assert projection.feed_owner is successor._projection_owner
            assert projection.feed_epoch == successor.epoch
    finally:
        successor.stopped.set(); owner.close()


@pytest.mark.parametrize('mode', ['removed', 'unchanged', 'diagnostic_filter_removed'])
def test_reload_reconciles_removed_source_retry(api, board, monkeypatch, mode):
    path, conn = board
    r = readers_for(api, path, monkeypatch)
    monkeypatch.setattr(r, 'external_capture', strict_empty)
    kanban_db.create_task(conn, title='initial', session_id='S')
    now = [1800000000.]
    f = feedmod.Feed(r, emit=lambda _: None, clock=lambda: now[0])
    f.watch(['S']); lane = compose(f, r)
    successor = None
    try:
        f.changes(['S']); drain(lane)
        real = r.capture_local
        def fail(*args, **kwargs):
            raise RuntimeError('local unavailable')
        monkeypatch.setattr(r, 'capture_local', fail)
        kanban_db.create_task(conn, title='new write', session_id='S')
        lane.step()
        for worker in list(lane.acquiring.values()) + list(lane.active.values()):
            worker.join(3)
        lane.service()
        assert lane.retry_at() is not None
        prior = {key: (a.count, a.retry_at) for key, a in lane.attempts.items()}
        if mode != 'unchanged':
            monkeypatch.setattr(r, 'boards', lambda: [])
        monkeypatch.setattr(r, 'capture_local', real)
        successor, owner = install(f, r, now, monkeypatch)
        if mode == 'diagnostic_filter_removed':
            owner.attempts = {s: a for s, a in owner.attempts.items() if s in owner.sources or s == owner.external}
            owner.scope_attempts = {s: a for s, a in owner.scope_attempts.items() if s in owner.attempts}
        if mode == 'unchanged':
            assert all((owner.attempts[s].count, owner.attempts[s].retry_at) == value for s, value in prior.items())
        now[0] += 100
        assert settle(owner, now) < 60
        assert all(s in owner.sources or s == owner.external for s in owner.attempts)
        assert all(s in owner.attempts for s in owner.scope_attempts)
        assert owner.retry_at() is None
    finally:
        f.stopped.set(); lane.close()
        if successor is not None:
            successor.stopped.set(); successor._source_coordinator.close()
        r.release(); conn.close()


@pytest.mark.parametrize('remove_source', [False, True])
@pytest.mark.parametrize('unavailable', [False, True])
def test_observation_reload_and_source_removal_compose_finitely(observed, monkeypatch, remove_source, unavailable):
    r, f, lane, c, remote, now = observed
    c.execute('UPDATE kanban_sync_state SET pulled_at=1799999999'); c.commit()
    lane._capture_one(r, lane.external, (r.board_stamps(), r.external_stamps()))
    assert f.bundles['S0']['board_projection'].observation_parent is not None
    drain(lane)
    if remove_source:
        # Establish actual failed-source custody before it leaves the inventory.
        original = r.capture_local
        def fail_local(*args, **kwargs):
            raise RuntimeError('local unavailable')
        monkeypatch.setattr(r, 'capture_local', fail_local)
        source = lane.sources[0]
        lane._capture_one(r, source, ('new local write',))
        assert lane.attempts[source].retry_at is not None
        monkeypatch.setattr(r, 'capture_local', original)
        monkeypatch.setattr(r, 'boards', lambda: [])
    successor, owner = install(f, r, now, monkeypatch)
    if unavailable:
        def fail_external(*args, **kwargs):
            raise RuntimeError('strict unavailable')
        monkeypatch.setattr(r, 'external_capture', fail_external)
    try:
        now[0] += 100
        successor.queue_keyed(['S0', 'S1'])
        assert settle(owner, now) < 60
        assert all(s in owner.sources or s == owner.external for s in owner.attempts)
        assert not owner.pending and not owner.active and not owner.acquiring
        assert owner.retry_at() is None
        for session in ('S0', 'S1'):
            projection = successor.bundles[session]['board_projection']
            assert projection.feed_owner is successor._projection_owner
            assert projection.feed_epoch == successor.epoch
            # A partial result may not certify missing current source scopes.
            assert owner.store.current(projection.admission)
    finally:
        successor.stopped.set(); owner.close()
