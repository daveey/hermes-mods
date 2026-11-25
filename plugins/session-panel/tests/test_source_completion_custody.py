"""Completion notification is not thread/role retirement: deterministic exit-gap controls."""
import threading
import types

import pytest
from test_core_readers import api, board, kanban_db
from test_feed import feedmod
from test_source_coordinator_core import compose, drain, readers_for, strict_empty


@pytest.mark.parametrize('role', ['projection', 'local', 'external'])
@pytest.mark.parametrize('retire', [False, True])
def test_completed_worker_keeps_role_until_actual_thread_exit(api, board, monkeypatch, role, retire):
    path, conn = board
    r = readers_for(api, path, monkeypatch)
    monkeypatch.setattr(r, 'external_capture', strict_empty)
    kanban_db.create_task(conn, title='Owned work', session_id='S')
    f = feedmod.Feed(r, emit=lambda _: None)
    f.watch(['S'])
    lane = compose(f, r)
    f.changes(['S']); drain(lane)
    entered, release = threading.Event(), threading.Event()
    target = '_project' if role == 'projection' else '_acquisition'
    original = getattr(lane, target)
    errors = []

    def held(*args):
        try:
            original(*args)
            entered.set()
            assert release.wait(10), 'post-completion exit barrier not released'
        except BaseException as exc:
            errors.append(exc)

    monkeypatch.setattr(lane, target, held)
    worker = None
    try:
        if role == 'projection':
            lane.capacity = 1
            with lane.lock:
                lane._queue(['S'])
            lane.service()
            worker = lane.active['S']
        else:
            with lane.lock:
                setattr(lane, '_' + role + '_requested', True)
            lane.step()
            worker = lane.acquiring[role]
        assert entered.wait(5) and worker.is_alive()
        if retire:
            owner = type(lane).__module__
            import sys
            retiring = sys.modules[owner].RetiringOwner(lane)
            assert not retiring.ready(), 'predecessor released live completed thread capacity'
            release.set(); worker.join(5)
            assert not worker.is_alive() and retiring.ready()
        else:
            if role == 'projection':
                with lane.lock:
                    lane._queue(['S'])
                lane.service()
                assert lane.active.get('S') is worker, 'completed projection slot reused before exit'
            else:
                with lane.lock:
                    setattr(lane, '_' + role + '_requested', True)
                lane.step()
                assert lane.acquiring.get(role) is worker, 'completed acquisition slot reused before exit'
            release.set(); worker.join(5)
            assert not worker.is_alive()
            drain(lane)
        assert not errors, errors
    finally:
        release.set()
        if worker is not None:
            worker.join(5)
        f.stopped.set(); lane.close(); r.release(); conn.close()


def test_inherited_reported_ready_cannot_release_live_worker_or_spin_expired_retry(api, board, monkeypatch):
    path, conn = board
    r = readers_for(api, path, monkeypatch)
    monkeypatch.setattr(r, 'external_capture', strict_empty)
    f = feedmod.Feed(r, emit=lambda _: None, clock=lambda: 100.)
    f.watch(['S'])
    lane = compose(f, r)
    entered, release = threading.Event(), threading.Event()
    def held():
        entered.set()
        assert release.wait(5)
    worker = threading.Thread(target=held)
    worker.start()
    try:
        assert entered.wait(2)
        # Compatibility contract of a retirement which already consumed its
        # completion marker; it has no new-generation workers snapshot.
        predecessor = types.SimpleNamespace(
            owner=types.SimpleNamespace(acquiring={'local': worker}, active={}),
            parent=None, remaining=set(), ready=lambda: True, bind=lambda _: None)
        lane.predecessor = predecessor
        lane.role_attempts['local'].retry_at = 99.
        assert not lane._available(), 'old readiness bypassed actual exit custody'
        assert lane.retry_at() == 100.01, 'expired inherited retry caused a zero-delay spin'
        source = f._source_module()
        pending = source.PendingSourceOwner(f, gateway=lane.gateway, profile=lane.profile,
                                            predecessor=predecessor)
        f._source_coordinator = pending
        factories = []
        def factory():
            factories.append(1)
            raise AssertionError('reader constructed before predecessor exit')
        assert f.ensure_snapshot_service('current', factory) is pending
        assert not factories, 'old ready admitted metadata/reader hydration'
        assert pending.retry_at() == 100.01, 'pending installer lost last exit wake'
        f._source_coordinator = lane
        release.set(); worker.join(3)
        assert not worker.is_alive() and lane._available()
        assert lane.retry_at() == 99., 'drain erased the inherited retry deadline'
    finally:
        release.set(); worker.join(3); lane.predecessor = None
        f.stopped.set(); lane.close(); r.release(); conn.close()


def test_automatic_service_refills_after_exit_gap_without_new_traffic(api, board, monkeypatch):
    path, conn = board
    r = readers_for(api, path, monkeypatch)
    monkeypatch.setattr(r, 'external_capture', strict_empty)
    kanban_db.create_task(conn, title='Autonomous work', session_id='S')
    f = feedmod.Feed(r, emit=lambda _: None)
    f.watch(['S'])
    lane = compose(f, r)
    f.changes(['S']); drain(lane)
    lane.capacity = 1
    entered, release, refilled = threading.Event(), threading.Event(), threading.Event()
    original = lane._project
    count = []
    def held(*args):
        count.append(args[0])
        original(*args)
        if len(count) == 1:
            entered.set()
            assert release.wait(5)
        else:
            refilled.set()
    monkeypatch.setattr(lane, '_project', held)
    try:
        with lane.lock:
            lane._queue(['S'])
        lane.service()
        first = lane.active['S']
        assert entered.wait(3)
        with lane.lock:
            lane._queue(['S'])
        lane.service()  # observe completed-but-live; sets the autonomous exit timer
        lane.start_service()
        assert lane.active['S'] is first and first.is_alive()
        release.set()
        assert refilled.wait(5), 'autonomous service lost post-completion exit wake'
        assert not first.is_alive()
    finally:
        release.set(); f.stopped.set(); lane.close(); r.release(); conn.close()
