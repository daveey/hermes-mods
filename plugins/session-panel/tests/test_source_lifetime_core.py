"""Real-reader lifetime and no-traffic predecessor controls, still UNACTIVATED."""
import json
import importlib.util
import sys
import threading
import types
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
@pytest.mark.parametrize('current_registered', [True, False])
def test_genuine_saved_restart_resumes_explicit_owner_without_current_api(
        api, board, monkeypatch, before_registration, frozen_owner, current_registered):
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

    retired = RetiredReaders()
    ns = dict(current_api.__dict__)
    ns.update(CoreReaders=lambda: retired, _emit=emit, write_timing=lambda _: None,
              _run=lambda feed: feed.stopped.wait(5),
              _run_decisions=lambda feed: feed.stopped.wait(5))
    source = (Path(__file__).parent / 'fixtures/board_entry_before_service.py').read_text()
    exec(compile(source, 'board_entry_before_service.py', 'exec'), ns)
    monkeypatch.setattr(core, 'BoardLane', PausedLane)
    monkeypatch.setattr(core, 'SNAPSHOT_READER_FACTORY', (lambda: r) if current_registered else None, raising=False)
    monkeypatch.setattr(current_api._LOADER, 'snapshot_reader_factory', None, raising=False)
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
        # Resume only publishes registration under the saved API's PROCESS_LOCK.
        # The unchanged saved board step installs off that lifecycle lock.
        if current_registered:
            owner = new._source_coordinator
        if not current_registered:
            assert owner.pending_install and not owner.service_worker
            assert current_api.register_snapshot_readers(lambda: r) is new._source_coordinator
            owner = new._source_coordinator
        assert owner.service_worker is not None and owner.service_worker.is_alive()
        assert new.readers is r and not retired.accesses and retired.released == 1
        # A genuine old decision loop must not recover its retired factory either.
        new.refresh_decisions(readers=retired)
        assert not retired.accesses
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


def test_resume_transfers_exact_scope_age_and_exhausted_source_budget(api, board, monkeypatch):
    path, conn = board
    current = readers_for(api, path, monkeypatch)
    monkeypatch.setattr(current, 'external_capture', strict_empty)
    now = [1800000000.]
    task = kanban_db.create_task(conn, title='Retain exact custody', session_id='S')
    old = feedmod.Feed(current, emit=lambda _: None, clock=lambda: now[0])
    previous = compose(old, current)
    old.changes(['S']); drain(previous)
    ref = previous.module.CardKey('fixture', 'default', 'default', task)
    previous.require('S', [ref]); drain(previous)
    calls = []
    def unavailable(*args, **kwargs):
        calls.append('strict capture')
        raise RuntimeError('fixture exact source unavailable')
    monkeypatch.setattr(current, 'external_capture', unavailable)
    monkeypatch.setattr(current, 'external_stamps', lambda: ('one changed source',))
    for _ in range(6):
        drain(previous)
        now[0] += 31
    assert calls == ['strict capture'] * 4
    prior_pin, prior_admission = previous.store.pin(previous.chats['S'])
    with previous.lock:
        previous._queue(['S'])
        age = previous.pending['S']
    monkeypatch.setattr(feedmod, 'SNAPSHOT_READER_FACTORY', lambda: current)
    new = feedmod.Feed(RetiredReaders(), emit=lambda _: None, clock=lambda: now[0])
    try:
        new.resume(old)
        pending = new._source_coordinator
        new.ensure_snapshot_service(pending.api_generation, pending.reader_factory)
        owner = new._source_coordinator
        assert owner.store.epoch == previous.store.epoch, 'resume replaced compatible store identity'
        assert new._watch_tokens['S'] is old._watch_tokens['S']
        pin, admission = owner.store.pin(owner.chats['S'])
        assert admission.watch_token == prior_admission.watch_token
        assert admission.required_scopes == prior_admission.required_scopes
        assert admission.first_admitted_mono_ns == prior_admission.first_admitted_mono_ns
        assert admission.receipt_ids == prior_admission.receipt_ids
        assert owner.pending['S'] == age
        assert pin.parts == prior_pin.parts
        assert owner.store.owner_token(owner.external) != previous.store.owner_token(previous.external)
        assert 'S' in new._carried and not new._bundle_fresh('S', now[0])
        for _ in range(5):
            drain(owner)
            now[0] += 31
        assert len(calls) == 4, 'resume reset an exhausted unchanged-source retry budget'
        assert not new._bundle_fresh('S', now[0])
    finally:
        old.stopped.set(); previous.close(); new.stopped.set()
        if getattr(new, '_source_coordinator', None):
            new._source_coordinator.close()
        current.release(); conn.close()


@pytest.mark.parametrize('failure_kind', ['registration', 'projection'])
def test_resume_retains_exhausted_role_and_projection_budgets(api, board, monkeypatch, failure_kind):
    path, conn = board
    current = readers_for(api, path, monkeypatch)
    monkeypatch.setattr(current, 'external_capture', strict_empty)
    now = [1800000000.]
    old = feedmod.Feed(current, emit=lambda _: None, clock=lambda: now[0])
    previous = compose(old, current)
    old.changes(['S']); drain(previous)
    calls = []
    def unavailable(*args, **kwargs):
        calls.append(failure_kind)
        raise RuntimeError('fixture unchanged ' + failure_kind + ' failure')
    queued_age = None
    if failure_kind == 'registration':
        monkeypatch.setattr(current, 'lineage', unavailable)
        monkeypatch.setattr(current, 'board_stamps', lambda: ('changed registration stamp',))
    else:
        monkeypatch.setattr(feedmod.Feed, 'project_board', unavailable)
        with previous.lock:
            previous._queue(['S'])
            queued_age = previous.pending['S']
    for _ in range(6):
        drain(previous)
        now[0] += 31
    assert len(calls) == 4
    if failure_kind == 'projection':
        assert previous._active_ages.get('S') == queued_age, 'retry renewed the first-unserved FIFO age'
    prior = (previous.role_attempts['local'] if failure_kind == 'registration'
             else previous.projection_attempts['S'])
    assert prior.count == 4 and prior.retry_at is None
    monkeypatch.setattr(feedmod, 'SNAPSHOT_READER_FACTORY', lambda: current)
    new = feedmod.Feed(RetiredReaders(), emit=lambda _: None, clock=lambda: now[0])
    try:
        new.resume(old)
        pending = new._source_coordinator
        new.ensure_snapshot_service(pending.api_generation, pending.reader_factory)
        owner = new._source_coordinator
        held = (owner.role_attempts['local'] if failure_kind == 'registration'
                else owner.projection_attempts['S'])
        assert held.count == 4 and held.retry_at is None
        if failure_kind == 'projection':
            assert owner._active_ages['S'] == queued_age
            assert 'S' not in owner.pending
        for _ in range(5):
            drain(owner)
            now[0] += 31
        assert len(calls) == 4, 'reload reset exhausted unchanged-input retry custody'
        assert not new._bundle_fresh('S', now[0])
    finally:
        old.stopped.set(); previous.close(); new.stopped.set()
        if getattr(new, '_source_coordinator', None):
            new._source_coordinator.close()
        current.release(); conn.close()


def test_resume_after_finished_projection_before_completion_service(api, board, monkeypatch):
    path, conn = board
    current = readers_for(api, path, monkeypatch)
    monkeypatch.setattr(current, 'external_capture', strict_empty)
    old = feedmod.Feed(current, emit=lambda _: None)
    previous = compose(old, current)
    old.changes(['S']); drain(previous)
    with previous.lock:
        previous._queue(['S'])
    previous.service()
    worker = previous.active['S']
    worker.join(5)
    assert not worker.is_alive()
    assert 'S' in previous.active and 'S' not in previous.projection_attempts
    monkeypatch.setattr(feedmod, 'SNAPSHOT_READER_FACTORY', lambda: current)
    new = feedmod.Feed(RetiredReaders(), emit=lambda _: None)
    try:
        new.resume(old)
        pending = new._source_coordinator
        new.ensure_snapshot_service(pending.api_generation, pending.reader_factory)
        owner = new._source_coordinator
        owner.service()
        assert 'S' not in owner.pending, 'already finished projection was recreated as unserved work'
        drain(owner)
        assert not owner.active
    finally:
        old.stopped.set(); previous.close(); new.stopped.set()
        if getattr(new, '_source_coordinator', None):
            new._source_coordinator.close()
        current.release(); conn.close()


def test_transfer_capacity_failure_does_not_consume_predecessor(api, board, monkeypatch):
    path, conn = board
    current = readers_for(api, path, monkeypatch)
    monkeypatch.setattr(current, 'external_capture', strict_empty)
    old = feedmod.Feed(current, emit=lambda _: None)
    previous = compose(old, current)
    old.changes(['S']); drain(previous)
    watches, _, tokens, receipts = previous.store._limits
    previous.store._limits = (watches, 2, tokens, receipts)
    original_boards = current.boards
    monkeypatch.setattr(current, 'boards', lambda: [*original_boards(), ('extra', path)])
    monkeypatch.setattr(feedmod, 'SNAPSHOT_READER_FACTORY', lambda: current)
    new = feedmod.Feed(RetiredReaders(), emit=lambda _: None)
    try:
        new.resume(old)
        pending = new._source_coordinator
        new.ensure_snapshot_service(pending.api_generation, pending.reader_factory)
        assert new._snapshot_install_pending
        assert pending.failed_registration == (pending.api_generation, pending.reader_factory)
        assert not getattr(previous.store, '_transferred', False)
        monkeypatch.setattr(current, 'boards', original_boards)
        owner = new.ensure_snapshot_service('recovered coordinates', lambda: current)
        assert not new._snapshot_install_pending
        assert owner.store.epoch == previous.store.epoch
        drain(owner)
    finally:
        old.stopped.set(); previous.close(); new.stopped.set()
        if getattr(new, '_source_coordinator', None):
            new._source_coordinator.close()
        current.release(); conn.close()


class RetiredReaders:
    """A genuinely saved API may construct readers with no current capability."""
    def __init__(self):
        self.released = 0
        self.accesses = []

    def release(self):
        self.released += 1

    def __getattr__(self, name):
        self.accesses.append(name)
        raise AssertionError('retired canonical reader used: ' + name)


@pytest.mark.parametrize('registered', [True, False])
def test_successor_uses_current_readers_or_waits_without_old_factory(
        api, board, monkeypatch, registered):
    path, conn = board
    current = readers_for(api, path, monkeypatch)
    monkeypatch.setattr(current, 'external_capture', strict_empty)
    kanban_db.create_task(conn, title='Current reader only', session_id='S')
    old = feedmod.Feed(current, emit=lambda _: None)
    previous = compose(old, current)
    old.changes(['S']); drain(previous)
    calls = []
    def poison():
        calls.append('old factory')
        raise AssertionError('saved reader factory is not registration authority')
    previous.reader_factory = poison
    loader = sys.modules.get('_session_panel_feed_loader')
    if loader is not None:
        monkeypatch.setattr(loader, 'snapshot_reader_factory', None, raising=False)
    monkeypatch.setattr(feedmod, 'SNAPSHOT_READER_FACTORY', (lambda: current) if registered else None)
    retired = RetiredReaders()
    new = feedmod.Feed(retired, emit=lambda _: None)
    try:
        new.resume(old)
        assert not calls and not retired.accesses
        assert retired.released == 1, 'unused saved-API reader was not released on its owning thread'
        if registered:
            assert new._snapshot_install_pending
            new._source_coordinator.step()  # manually drive the saved off-lock board entry
        if not registered:
            pending = new._source_coordinator
            assert new._snapshot_install_pending
            assert pending.full(['S']) == {} and pending.tick() == 0
            pending.step()
            assert not new._bundle_fresh('S', new.clock())
            assert not calls and not retired.accesses
            # Registration repairs the existing opted-in Feed, not a new API read.
            api.ensure_snapshot_service(new, 'current-generation', lambda: current)
        assert new.readers is current
        owner = new._source_coordinator
        assert not getattr(new, '_snapshot_install_pending', False)
        assert api.ensure_snapshot_service(new, 'current-generation', lambda: current) is owner
        assert owner.service_worker is None, 'manual resume/registration started a service'
        drain(owner)
        assert any(row['title'] == 'Current reader only' for row in new.bundles['S']['bundle']['tasks'])
        assert not calls and not retired.accesses
    finally:
        old.stopped.set(); previous.close(); new.stopped.set()
        owner = getattr(new, '_source_coordinator', None)
        if owner is not None:
            owner.close()
        current.release(); conn.close()


@pytest.mark.parametrize('during_install', ['stop', 'close', 'new_registration'])
def test_reader_install_revalidates_stop_and_registration_without_duplicate_roles(
        api, board, monkeypatch, during_install):
    path, conn = board
    current = readers_for(api, path, monkeypatch)
    monkeypatch.setattr(current, 'external_capture', strict_empty)
    old = feedmod.Feed(current, emit=lambda _: None)
    previous = compose(old, current)
    old.changes(['S']); drain(previous)
    monkeypatch.setattr(feedmod, 'SNAPSHOT_READER_FACTORY', None)
    loader = sys.modules.get('_session_panel_feed_loader')
    if loader is not None:
        monkeypatch.setattr(loader, 'snapshot_reader_factory', None, raising=False)
    new = feedmod.Feed(RetiredReaders(), emit=lambda _: None).resume(old)
    pending = new._source_coordinator
    entered, release = threading.Event(), threading.Event()
    calls, releases, answers = [], [], []
    original_release = current.release
    def release_reader():
        assert not new.lock._is_owned(), 'reader release ran under Feed writer lock'
        releases.append(threading.get_ident())
        original_release()
    monkeypatch.setattr(current, 'release', release_reader)
    def factory():
        calls.append(threading.get_ident())
        entered.set(); assert release.wait(5)
        return current
    caller = threading.Thread(target=lambda: answers.append(api.ensure_snapshot_service(new, 'first', factory)))
    try:
        caller.start(); assert entered.wait(2)
        # The same registration cannot construct a second candidate or add slots.
        assert api.ensure_snapshot_service(new, 'first', factory) is pending
        assert len(calls) == 1 and not hasattr(pending, 'acquiring')
        if during_install == 'stop':
            new.stopped.set()
        elif during_install == 'close':
            pending.close()
        else:
            api.ensure_snapshot_service(new, 'second', lambda: current)
        release.set(); caller.join(3)
        assert not caller.is_alive()
        assert new._source_coordinator is pending and answers == [pending]
        assert len(releases) == 1 and not pending.service_worker
        if during_install == 'new_registration':
            pending.step()
            assert new._source_coordinator is not pending
            assert new._snapshot_api_generation == 'second'
            assert not new._source_coordinator.service_worker
        else:
            assert api.ensure_snapshot_service(new, 'second', lambda: current) in (None, pending)
            assert new._source_coordinator is pending
    finally:
        release.set(); caller.join(3)
        new.stopped.set(); new._source_coordinator.close()
        old.stopped.set(); previous.close(); current.release(); conn.close()


@pytest.mark.parametrize('barrier', ['factory', 'release'])
@pytest.mark.parametrize('resumes', [1, 2])
def test_pending_reader_install_custody_survives_consecutive_resumes(
        api, board, monkeypatch, barrier, resumes):
    path, conn = board
    current = readers_for(api, path, monkeypatch)
    monkeypatch.setattr(current, 'external_capture', strict_empty)
    old = feedmod.Feed(current, emit=lambda _: None)
    previous = compose(old, current)
    monkeypatch.setattr(feedmod, 'SNAPSHOT_READER_FACTORY', None)
    loader = sys.modules.get('_session_panel_feed_loader')
    if loader is not None:
        monkeypatch.setattr(loader, 'snapshot_reader_factory', None, raising=False)
    first = feedmod.Feed(RetiredReaders(), emit=lambda _: None).resume(old)
    entered, release = threading.Event(), threading.Event()
    calls, errors = [], []
    original_release = current.release
    def held_release():
        if barrier == 'release' and threading.current_thread() is installer:
            entered.set(); assert release.wait(15)
        original_release()
    monkeypatch.setattr(current, 'release', held_release)
    def factory():
        calls.append(threading.get_ident())
        if len(calls) == 1 and barrier == 'factory':
            entered.set(); assert release.wait(15)
        return current
    def install():
        try:
            api.ensure_snapshot_service(first, 'first', factory)
        except BaseException as exc:
            errors.append(exc)
    installer = threading.Thread(target=install)
    feeds = [old, first]
    pending = first._source_coordinator
    installer.start()
    try:
        assert entered.wait(5)
        for _ in range(resumes):
            successor = feedmod.Feed(RetiredReaders(), emit=lambda _: None).resume(feeds[-1])
            feeds.append(successor)
            pending = successor._source_coordinator
            assert api.ensure_snapshot_service(successor, 'current', factory) is pending
            pending.step()
            assert len(calls) == 1, 'overlapping reader candidates across resume'
            assert pending.retry_at() is not None
            assert pending.service_worker is None
        release.set(); installer.join(5)
        assert not installer.is_alive() and not errors
        assert first._source_coordinator.pending_install
        # The saved pending loop revisits after release without new HTTP traffic.
        pending.step()
        owner = feeds[-1]._source_coordinator
        assert owner is not pending
        # step also starts legitimate source acquisitions on their owned roles;
        # exactly one candidate is constructed on this installation caller.
        assert calls.count(threading.get_ident()) == 1
        assert owner.service_worker is None
        assert owner.predecessor.ready()
    finally:
        release.set(); installer.join(5)
        for f in reversed(feeds):
            f.stopped.set(); f._source_coordinator.close()
        current.release(); conn.close()
        assert not installer.is_alive() and not errors


def test_unsupported_registration_is_unknown_and_not_retried_by_beat_full_or_step(api, board, monkeypatch):
    path, conn = board
    current = readers_for(api, path, monkeypatch)
    monkeypatch.setattr(current, 'external_capture', strict_empty)
    old = feedmod.Feed(current, emit=lambda _: None)
    previous = compose(old, current)
    old.changes(['S']); drain(previous)
    monkeypatch.setattr(feedmod, 'SNAPSHOT_READER_FACTORY', None)
    loader = sys.modules.get('_session_panel_feed_loader')
    if loader is not None:
        monkeypatch.setattr(loader, 'snapshot_reader_factory', None, raising=False)
    new = feedmod.Feed(RetiredReaders(), emit=lambda _: None).resume(old)
    pending = new._source_coordinator
    unsupported = types.SimpleNamespace(release=lambda: None)
    calls = []
    def factory():
        calls.append('unsupported')
        return unsupported
    try:
        assert api.ensure_snapshot_service(new, 'unsupported', factory) is pending
        for _ in range(8):
            pending.step(); pending.tick(); pending.full(['S'])
            new.refresh_decisions(readers=RetiredReaders())
        assert calls == ['unsupported'] and not new._bundle_fresh('S', new.clock())
        assert new._carried == {'S'} and new._snapshot_install_pending
        assert api.ensure_snapshot_service(new, 'replacement', lambda: current) is not pending
        drain(new._source_coordinator)
    finally:
        new.stopped.set(); new._source_coordinator.close()
        old.stopped.set(); previous.close(); current.release(); conn.close()


def test_registration_never_opts_in_normal_feed(api, board, monkeypatch):
    path, conn = board
    current = readers_for(api, path, monkeypatch)
    f = feedmod.Feed(current, emit=lambda _: None)
    calls = []
    try:
        assert api.ensure_snapshot_service(f, 'unused', lambda: calls.append('factory')) is None
        assert not calls and not hasattr(f, '_source_coordinator')
    finally:
        f.stopped.set(); current.release(); conn.close()


def test_successor_reserves_all_four_roles_until_old_readers_and_projections_release(
        api, board, monkeypatch):
    path, conn = board
    r = readers_for(api, path, monkeypatch)
    monkeypatch.setattr(r, 'external_capture', strict_empty)
    tasks = [kanban_db.create_task(conn, title='Initial ' + session, session_id=session)
             for session in ('A', 'B')]
    now = [1800000000.]
    old = feedmod.Feed(r, emit=lambda _: None, clock=lambda: now[0])
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
        new = feedmod.Feed(r, emit=lambda _: None, clock=lambda: now[0]).resume(old)
        owner = new._source_coordinator
        assert previous.closed and not owner.predecessor.ready()
        # Retirement does not join under the writer lock or borrow any new role.
        for key in ('local', 'external', 'A'):
            owner.step()
            assert not factories and not getattr(owner, 'acquiring', {}) and not getattr(owner, 'active', {})
            release[key].set(); old_workers[key].join(3)
            assert not old_workers[key].is_alive()
            assert not owner.predecessor.ready(), 'remaining old projection lost its slot custody'
        release['B'].set(); old_workers['B'].join(3)
        assert not old_workers['B'].is_alive() and owner.predecessor.ready()
        owner.step()
        owner = new._source_coordinator
        assert all(a.count for group in owner.scope_attempts.values() for a in group.values())
        # Cancelled captures retain their consumed attempt and scheduled backoff.
        # Advance the injected clock, never sleep to substitute for release.
        now[0] += 31
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
