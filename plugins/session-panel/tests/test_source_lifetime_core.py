"""Real-reader lifetime and no-traffic predecessor controls, still UNACTIVATED."""
import json
import importlib.util
import sys
import threading
from pathlib import Path

import pytest

from test_core_readers import api, board, kanban_db
from test_feed import feedmod
from test_routes import _hot, _restore
from test_source_coordinator_core import compose, drain, readers_for, strict_empty


def test_composed_owner_consumes_http_interest_without_canonical_sweep(api, board, monkeypatch):
    path, conn = board
    r = readers_for(api, path, monkeypatch)
    monkeypatch.setattr(r, 'external_capture', strict_empty)
    now = [1800000000.]
    f = feedmod.Feed(r, emit=lambda _: None, clock=lambda: now[0])
    owner = compose(f, r)
    try:
        for i in range(50):
            payload = json.loads(f.changes_bytes([f'S{i}'], subscriber=f'panel-{i}', visible_session=f'S{i}'))
            assert payload['bundles'] == {}
        # The card service, not the full canonical sweep, owns HTTP interest.
        owner.service()
        assert f._board_demands.empty() and f._panel_updates.empty()
        assert f._registrations.empty()
        assert set(f.interest) == {f'S{i}' for i in range(50)}
        assert set(f._board_requested) == set(f.interest)
        assert len(f._panel_subscribers) == 50
        now[0] += 76
        owner.service()
        assert not f._board_requested and not f._panel_subscribers
        assert not owner.service_worker, 'manually driven composition started a service'
    finally:
        f.stopped.set(); owner.close(); r.release(); conn.close()


@pytest.mark.parametrize('before_registration', [True, False])
def test_saved_predecessor_existing_owner_registration_starts_service_without_current_api(
        api, board, monkeypatch, before_registration):
    path, conn = board
    r = readers_for(api, path, monkeypatch)
    monkeypatch.setattr(r, 'external_capture', strict_empty)
    task = kanban_db.create_task(conn, title='Original local state', session_id='S')
    current_api, core = _hot(monkeypatch, 'snapshot_predecessor')
    f = core.Feed(r, emit=lambda _: None)
    f.watch(['S'])
    owner = compose(f, r)
    f.changes(['S']); drain(owner)
    assert owner.service_worker is None
    registering, register, entered, release, published = (threading.Event() for _ in range(5))
    original_lane = core.BoardLane

    class PausedLane(original_lane):
        def __new__(cls, feed, factory):
            if before_registration:
                registering.set()
                assert register.wait(5)
            return original_lane.__new__(cls, feed, factory)

    ns = dict(current_api.__dict__)
    ns.update(CoreReaders=lambda: r, write_timing=lambda _: None,
              _run_decisions=lambda feed: feed.stopped.wait(5))
    source = (Path(__file__).parent / 'fixtures/board_entry_before_service.py').read_text()
    exec(compile(source, 'board_entry_before_service.py', 'exec'), ns)
    original_step = owner.step

    def held_step():
        original_step()
        if threading.current_thread() is f._board_thread:
            entered.set()
            assert release.wait(5)

    def emit(payload):
        value = payload.get('bundles', {}).get('S', {})
        if any(row.get('title') == 'Changed after predecessor entry' for row in value.get('tasks', ())):
            published.set()

    monkeypatch.setattr(core, 'BoardLane', PausedLane)
    monkeypatch.setattr(owner, 'step', held_step)
    f.emit = emit
    core.PROCESS_FEED = f
    core.RESTART = ns['feed']
    try:
        assert core.RESTART() is f
        board_thread = f._board_thread
        if before_registration:
            assert registering.wait(2)
            # No current API entry or repair call, in either registration order.
            assert owner.service_worker is None
            register.set()
        assert entered.wait(2)
        assert owner.service_worker is not None and owner.service_worker.is_alive(), \
            'saved predecessor never established the current composed service'
        # Capture entry is held; only the actual completion service can refill.
        conn.execute('UPDATE tasks SET title=? WHERE id=?', ('Changed after predecessor entry', task))
        conn.commit()
        # Signal the same owner as a source notification, not a new API request.
        owner.service_wake.set()
        assert published.wait(3)
        assert f._board_thread is board_thread and not release.is_set()
    finally:
        register.set(); release.set(); f.stopped.set()
        if getattr(f, '_board_thread', None):
            f._board_thread.join(3)
        owner.close()
        if owner.service_worker:
            assert not owner.service_worker.is_alive()
        r.release(); conn.close()
        _restore(current_api, core)


def test_close_retires_snapshot_before_joining_old_projection(api, board, monkeypatch):
    path, conn = board
    r = readers_for(api, path, monkeypatch)
    monkeypatch.setattr(r, 'external_capture', strict_empty)
    kanban_db.create_task(conn, title='Retire this exact owner', session_id='S')
    f = feedmod.Feed(r, emit=lambda _: None)
    owner = compose(f, r)
    f.changes(['S']); drain(owner)
    projection = f.project_board(owner.store, owner.chats['S'], canonical_base=f.bundles['S']['bundle'])
    entered, release, joining = (threading.Event() for _ in range(3))
    original_base = owner._base
    def held_base(*args):
        result = original_base(*args)
        entered.set()
        assert release.wait(5)
        return result
    monkeypatch.setattr(owner, '_base', held_base)
    with owner.lock:
        owner._queue(['S'])
    owner.service()
    closer = None
    try:
        assert entered.wait(2)
        worker = owner.active['S']
        original_join = worker.join
        def observed_join(*args, **kwargs):
            joining.set()
            return original_join(*args, **kwargs)
        monkeypatch.setattr(worker, 'join', observed_join)
        closer = threading.Thread(target=owner.close)
        closer.start()
        assert joining.wait(2)
        assert not f.stopped.is_set(), 'close must retire just the source owner, not canonical authority'
        result = f.publish_projections(owner.store, {'S': projection})['S']
        assert result.disposition == 'superseded', 'closed owner still certified an exact pin during joins'
    finally:
        release.set()
        if closer:
            closer.join(3)
            assert not closer.is_alive()
        f.stopped.set(); owner.close(); r.release(); conn.close()


@pytest.mark.parametrize('before_registration', [True, False])
@pytest.mark.parametrize('frozen_owner', [True, False])
def test_genuine_saved_restart_resumes_explicit_owner_without_current_api(
        api, board, monkeypatch, before_registration, frozen_owner):
    path, conn = board
    r = readers_for(api, path, monkeypatch)
    monkeypatch.setattr(r, 'external_capture', strict_empty)
    task = kanban_db.create_task(conn, title='Before natural reload', session_id='S')
    current_api, core = _hot(monkeypatch, 'source_successor_predecessor')
    old = core.Feed(r, emit=lambda _: None)
    old.watch(['S'])
    if frozen_owner:
        # Exact parent merged62ae5021 module, not today's class with old names.
        path = Path(__file__).parent / 'fixtures/source_coordinator_before_lifetime.py'
        name = r.snapshot_modules().__package__ + '.before_lifetime'
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, name, module)
        spec.loader.exec_module(module)
        previous = module.SourceCoordinator(old, lambda: r, gateway='fixture', profile='default')
        old._source_coordinator = old._board_lane = previous
        assert not hasattr(previous, 'retire') and not hasattr(previous, 'drained')
    else:
        previous = compose(old, r)
    old.changes(['S']); drain(previous)
    old._board_capture_failures.add('S')
    old_epoch, old_rev = old.epoch, old.rev
    old.stopped.set()
    registering, register, entered, release, published = (threading.Event() for _ in range(5))
    original_lane = core.BoardLane

    class PausedLane(original_lane):
        def __new__(cls, feed, factory):
            if before_registration:
                registering.set(); assert register.wait(5)
            owner = original_lane.__new__(cls, feed, factory)
            assert owner is not previous
            original_step = owner.step
            def held_step():
                original_step()
                if threading.current_thread() is feed._board_thread:
                    entered.set(); assert release.wait(5)
            monkeypatch.setattr(owner, 'step', held_step)
            return owner

    def emit(payload):
        value = payload.get('bundles', {}).get('S', {})
        if any(row.get('title') == 'After natural reload' for row in value.get('tasks', ())):
            published.set()

    ns = dict(current_api.__dict__)
    ns.update(CoreReaders=lambda: r, _emit=emit, write_timing=lambda _: None,
              _run=lambda feed: feed.stopped.wait(5),
              _run_decisions=lambda feed: feed.stopped.wait(5))
    source = (Path(__file__).parent / 'fixtures/board_entry_before_service.py').read_text()
    exec(compile(source, 'board_entry_before_service.py', 'exec'), ns)
    monkeypatch.setattr(core, 'BoardLane', PausedLane)
    monkeypatch.setattr(core, 'SNAPSHOT_READER_FACTORY', lambda: r, raising=False)
    core.PROCESS_FEED, core.RESTART = old, ns['feed']
    new = None
    try:
        new = core.RESTART()
        assert new is not old and new.epoch == old_epoch and new.rev >= old_rev
        owner = getattr(new, '_source_coordinator', None)
        assert owner is not None, 'saved RESTART discarded explicit source ownership'
        assert owner is not previous and previous.closed
        if before_registration:
            assert registering.wait(2)
            assert owner.service_worker is None
            register.set()
        assert entered.wait(2)
        assert owner.service_worker is not None and owner.service_worker.is_alive()
        conn.execute('UPDATE tasks SET title=? WHERE id=?', ('After natural reload', task)); conn.commit()
        owner.service_wake.set()
        assert published.wait(3), 'no current API traffic repairs the predecessor entry'
        assert not release.is_set() and old._successor is new
    finally:
        register.set(); release.set()
        if new is not None:
            new.stopped.set()
            if getattr(new, '_board_thread', None):
                new._board_thread.join(3)
            if getattr(new, '_source_coordinator', None):
                new._source_coordinator.close()
        old.stopped.set(); previous.close(); r.release(); conn.close()
        _restore(current_api, core)


def test_successor_reserves_all_four_roles_until_old_readers_and_projections_release(
        api, board, monkeypatch):
    path, conn = board
    r = readers_for(api, path, monkeypatch)
    monkeypatch.setattr(r, 'external_capture', strict_empty)
    tasks = [kanban_db.create_task(conn, title='Initial ' + session, session_id=session)
             for session in ('A', 'B')]
    old = feedmod.Feed(r, emit=lambda _: None)
    previous = compose(old, r)
    old.changes(['A', 'B']); drain(previous)
    entered = {key: threading.Event() for key in ('local', 'external', 'A', 'B')}
    release = {key: threading.Event() for key in entered}
    original_local, original_external, original_base = r.capture_local, r.capture_external, previous._base
    def local(*args, **kwargs):
        value = original_local(*args, **kwargs)
        entered['local'].set(); assert release['local'].wait(10)
        return value
    def external(*args, **kwargs):
        value = original_external(*args, **kwargs)
        entered['external'].set(); assert release['external'].wait(10)
        return value
    def base(session, lineage):
        value = original_base(session, lineage)
        entered[session].set(); assert release[session].wait(10)
        return value
    monkeypatch.setattr(r, 'capture_local', local)
    monkeypatch.setattr(r, 'capture_external', external)
    monkeypatch.setattr(previous, '_base', base)
    monkeypatch.setattr(r, 'external_stamps', lambda: ('changed external source',))
    for task in tasks:
        conn.execute('UPDATE tasks SET title=? WHERE id=?', ('Changed during retirement', task))
    conn.commit()
    with previous.lock:
        previous._queue(['A', 'B'])
    previous.step()
    new = None
    factories = []
    def current_reader():
        factories.append(threading.current_thread().name)
        return r
    monkeypatch.setattr(feedmod, 'SNAPSHOT_READER_FACTORY', current_reader)
    try:
        assert all(event.wait(3) for event in entered.values())
        assert set(previous.acquiring) == {'local', 'external'} and len(previous.active) == 2
        old_workers = {**previous.acquiring, **previous.active}
        new = feedmod.Feed(r, emit=lambda _: None).resume(old)
        owner = new._source_coordinator
        assert previous.closed and not owner.predecessor.ready()
        # Retirement does not join under the writer lock or borrow any new role.
        for key in ('local', 'external', 'A'):
            owner.step()
            assert not factories and not owner.acquiring and not owner.active
            release[key].set(); old_workers[key].join(3)
            assert not old_workers[key].is_alive()
            assert not owner.predecessor.ready(), 'remaining old projection lost its slot custody'
        release['B'].set(); old_workers['B'].join(3)
        assert not old_workers['B'].is_alive() and owner.predecessor.ready()
        drain(owner)
        assert factories and owner.drained.is_set()
        assert all(not worker.is_alive() for worker in old_workers.values())
        assert all(row['title'] == 'Changed during retirement'
                   for held in new.bundles.values() for row in held['bundle']['tasks'])
    finally:
        for event in release.values():
            event.set()
        old.stopped.set(); previous.close()
        if new is not None:
            new.stopped.set(); new._source_coordinator.close()
        r.release(); conn.close()
