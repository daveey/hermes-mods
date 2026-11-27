"""Disposable real CoreReaders/Feed races for the unactivated source cutover."""
import threading

import pytest

from test_core_readers import api, board, kanban_db, readers_for as core_readers_for
from test_feed import feedmod
from test_snapshot_acquisition import acquisition
from test_partial_projection_core import action_core


def readers_for(api, path, monkeypatch):
    # Core's hub_db_path does not follow HERMES_HOME. Real replica-stat changes
    # on the executing worker are genuine renewing inputs, not quiet retries.
    monkeypatch.setenv('HERMES_HUB_STATE', str(path.parent / 'hub-state'))
    return core_readers_for(api, path, monkeypatch)


@pytest.mark.parametrize('retirement', ['withdraw', 'deleted_session', 'approve'])
def test_all_route_cutover_retains_real_current_actionstore_and_consent(acquisition, tmp_path, monkeypatch, retirement):
    import uuid
    from hermes_state import SessionDB
    r, cap, m, source, store, conn = acquisition
    monkeypatch.setattr(r, 'external_capture', strict_empty)
    db = SessionDB(db_path=tmp_path / 'state.db')
    db.create_session('S', 'desktop')
    core = action_core()
    owner, rid = core.server_owner('S'), str(uuid.uuid4())
    actions = core.ActionStore()
    actions.create(owner, dict(schemaVersion=2, owner=dict(owner), requestId=rid, revision=1,
        kind='approval', title='Authorize the precise fixture scope',
        scope=dict(markdown='Only change example.py.', complete=True, sourceRefs=[]),
        limits=[dict(key='cost', value='0', permissionRequired=True)], exclusions=['No network changes'],
        outcomes=dict(approve='Authorize only this scope', revise='Keep blocked', decline='Cancel'),
        choices=[dict(id=e, label=e.title(), effect=e) for e in ('approve', 'revise', 'decline')], aliases=[], state='open'))
    task = kanban_db.create_task(conn, title='Unstarted board work', session_id='S')
    f = feedmod.Feed(r, emit=lambda _: None)
    f.watch(['S'])
    lane = compose(f, r)
    operation = None
    read_entered, read_release = threading.Event(), threading.Event()
    caller = None
    returned = []
    try:
        f.changes(['S']); drain(lane)
        base = f.bundles['S']['bundle']
        ask = next(row for row in base['model']['sections']['needs_you'] if row.get('request_id') == rid)
        assert ask['options'] and base['requests'][0]['requestId'] == rid
        assert actions.get(owner, rid)['state'] == 'open' and not actions.get(owner, rid).get('resolution'), 'board acquisition executed consent'
        original_inputs = r.panel_inputs
        def held_inputs(*args):
            value = original_inputs(*args)
            if threading.current_thread().name == 'fixture-stale-full':
                read_entered.set(); assert read_release.wait(5)
            return value
        monkeypatch.setattr(r, 'panel_inputs', held_inputs)
        caller = threading.Thread(target=lambda: returned.append(f.compute('S')), name='fixture-stale-full')
        caller.start()
        assert read_entered.wait(2)
        if retirement == 'withdraw':
            core.withdraw_request(actions, None, owner, rid, by='agent', reason='fixture request is moot')
        elif retirement == 'deleted_session':
            assert db.delete_session('S')
        else:
            operation = actions.decide(owner, dict(requestId=rid, expectedRevision=1, choiceId='approve',
                idempotencyKey=str(uuid.uuid4())))['operation']
            assert operation['intent']['executionAuthorized'] and operation['dispatchState'] == 'pending'
        f.refresh_decisions()
        current = f.bundles['S']['bundle']['requests']
        assert not f.bundles['S']['bundle']['model']['sections']['needs_you']
        read_release.set(); caller.join(3)
        assert not caller.is_alive() and returned[0]['requests'] == current, 'full route returned obsolete consent'
        # A board-only publication from a held old canonical base cannot reopen
        # a recorded decision, clear canonical failure, or manufacture Running.
        with lane.lock:
            lane.bases['S'] = base
        f._decision_failures.add('S')
        conn.execute('UPDATE tasks SET title=? WHERE id=?', ('After recorded decision', task)); conn.commit()
        drain(lane)
        value = f.bundles['S']['bundle']
        assert value['requests'] == current and not value['model']['sections']['needs_you']
        assert not value['model']['sections']['running'] and 'S' in f._decision_failures
        if retirement == 'approve':
            assert operation is not None
            assert actions.operation(owner, oid=operation['operationId'])['operation']['dispatchState'] == 'pending'
        elif retirement == 'withdraw':
            assert actions.get(owner, rid)['state'] == 'withdrawn'
    finally:
        read_release.set()
        if caller is not None:
            caller.join(3)
        f.stopped.set(); lane.close(); r.release(); db.close()


@pytest.mark.parametrize('terminal', ['done', 'archived'])
def test_real_stale_full_route_later_sequence_cannot_resurrect_waiting(api, board, monkeypatch, terminal):
    path, conn = board
    r = readers_for(api, path, monkeypatch)
    monkeypatch.setattr(r, 'external_capture', strict_empty)
    task = kanban_db.create_task(conn, title='Waiting work', session_id='S')
    # Ready cards have their own section; use an actual clock wait instead.
    assert kanban_db.schedule_task(conn, task, reason='Fixture release window', clock_only=True,
                                   awaiting={'kind': 'clock', 'why': 'Fixture release window'})
    f = feedmod.Feed(r, emit=lambda _: None)
    f.watch(['S'])
    lane = compose(f, r)
    try:
        f.changes(['S']); drain(lane)
        assert f.bundles['S']['bundle']['model']['sections']['waiting']
        pin = lane.store.pin(lane.chats['S'])
        base = f.bundles['S']['bundle']
        conn.execute('UPDATE tasks SET status=? WHERE id=?', (terminal, task)); conn.commit()
        drain(lane)
        assert not any(row['id'] == task for row in f.bundles['S']['bundle']['model']['sections']['waiting'])
        # A later STARTED full sweep still using the older materialization must
        # lose, not simply one with an older computed_seq.
        stale = f.project_board(lane.store, lane.chats['S'], canonical_base=base, pin=pin, seq=100000)
        values = lane.full(['S'])
        values['S'] = type(values['S'])(stale, lane.module)
        f._publish(values)
        assert not any(row['id'] == task for row in f.bundles['S']['bundle']['model']['sections']['waiting'])
        assert lane.store.current(stale.admission) is False
    finally:
        f.stopped.set(); lane.close(); r.release(); conn.close()


def test_source_failure_budget_survives_full_and_beat_without_false_fresh(api, board, monkeypatch):
    path, conn = board
    r = readers_for(api, path, monkeypatch)
    now = [1800000000.]
    calls, pushes = [], []
    def fail(held, *, include_inventory=False):
        calls.append((r.board_stamps(), r.external_stamps()))
        raise RuntimeError('strict fixture unavailable')
    monkeypatch.setattr(r, 'external_capture', fail)
    kanban_db.create_task(conn, title='Known local work', session_id='S')
    f = feedmod.Feed(r, emit=pushes.append, clock=lambda: now[0])
    f.watch(['S'])
    lane = compose(f, r)
    try:
        f.changes(['S']); drain(lane)
        assert f.bundles['S']['bundle']['tasks']
        for _ in range(3):
            now[0] = lane.retry_at()
            assert now[0] is not None
            drain(lane)
        assert len(calls) == 4 and lane.retry_at() is None, (now, lane.attempts, lane.scope_attempts, lane.role_attempts, lane.projection_attempts)
        for _ in range(3):
            f._publish(f._compute_all(['S']))
            now[0] += 30
            f.beat()
            assert 'S' not in pushes[-1]['fresh']
        assert len(calls) == 4, ('full/beat renewed an exhausted strict-source budget', calls)
        assert lane.store.pin(lane.chats['S'])[1].missing_scopes
        resumed_pushes = []
        monkeypatch.setattr(feedmod, 'SNAPSHOT_READER_FACTORY', lambda: r)
        resumed = feedmod.Feed(r, emit=resumed_pushes.append, clock=lambda: now[0]).resume(f)
        pending = resumed._source_coordinator
        resumed.ensure_snapshot_service(pending.api_generation, pending.reader_factory)
        assert 'S' in resumed._board_capture_failures
        now[0] += 30
        resumed.beat()
        assert 'S' not in resumed_pushes[-1]['fresh']
        resumed.stopped.set()
    finally:
        f.stopped.set(); lane.close(); r.release(); conn.close()


def test_projection_failure_has_finite_completion_driven_retries(api, board, monkeypatch):
    path, conn = board
    r = readers_for(api, path, monkeypatch)
    monkeypatch.setattr(r, 'external_capture', strict_empty)
    kanban_db.create_task(conn, title='Pure work', session_id='S')
    now = [1800000000.]
    f = feedmod.Feed(r, emit=lambda _: None, clock=lambda: now[0])
    f.watch(['S'])
    lane = compose(f, r)
    f.changes(['S']); drain(lane)
    original = f.project_board
    calls = []
    def fail(*args, **kwargs):
        calls.append(1)
        raise RuntimeError('pure projector unavailable')
    monkeypatch.setattr(f, 'project_board', fail)
    try:
        conn.execute('UPDATE tasks SET title=?', ('Changed local work',)); conn.commit()
        drain(lane)
        for _ in range(3):
            now[0] = lane.retry_at()
            assert now[0] is not None, 'projection failure stranded without finite retry'
            drain(lane)
        assert len(calls) == 4 and lane.retry_at() is None, (now, lane.attempts, lane.scope_attempts, lane.role_attempts, lane.projection_attempts)
        monkeypatch.setattr(f, 'project_board', original)
        conn.execute('UPDATE tasks SET title=?', ('Recovery source event',)); conn.commit()
        drain(lane)
        assert f.bundles['S']['bundle']['tasks'][0]['title'] == 'Recovery source event'
        assert 'S' not in f._board_failures
    finally:
        f.stopped.set(); lane.close(); r.release(); conn.close()


def strict_empty(held, *, include_inventory=False):
    captured = {sid: () for sid in held}
    inventory = {'origin': [], 'history': [], 'replica': None, 'gateway': 'fixture', 'profile': 'default'}
    return (captured, inventory) if include_inventory else captured


def test_changed_key_does_not_reproject_unrelated_chat(api, board, monkeypatch):
    path, conn = board
    r = readers_for(api, path, monkeypatch)
    monkeypatch.setattr(r, 'external_capture', strict_empty)
    task = kanban_db.create_task(conn, title='A original', session_id='A')
    kanban_db.create_task(conn, title='B unrelated', session_id='B')
    f = feedmod.Feed(r, emit=lambda _: None)
    f.watch(['A', 'B']); lane = compose(f, r)
    f.changes(['A', 'B']); drain(lane)
    before = f.bundles['B']['bundle']
    project = f.project_board
    projected = []
    def record(store, chat, **kwargs):
        projected.append(chat.session_id)
        return project(store, chat, **kwargs)
    monkeypatch.setattr(f, 'project_board', record)
    try:
        conn.execute('UPDATE tasks SET title=? WHERE id=?', ('A changed', task)); conn.commit()
        drain(lane)
        assert 'A' in projected and 'B' not in projected
        assert f.bundles['A']['bundle']['tasks'][0]['title'] == 'A changed'
        assert f.bundles['B']['bundle']['computed_seq'] == before['computed_seq']
    finally:
        f.stopped.set(); lane.close(); r.release(); conn.close()


def test_new_full_chat_during_older_local_registration_is_not_lost(api, board, monkeypatch):
    path, conn = board
    r = readers_for(api, path, monkeypatch)
    monkeypatch.setattr(r, 'external_capture', strict_empty)
    f = feedmod.Feed(r, emit=lambda _: None)
    f.watch(['A']); lane = compose(f, r)
    f.changes(['A']); drain(lane)
    entered, release = threading.Event(), threading.Event()
    lineage = r.lineage
    def slow(s):
        if s == 'A':
            entered.set(); assert release.wait(5)
        return lineage(s)
    monkeypatch.setattr(r, 'lineage', slow)
    task = kanban_db.create_task(conn, title='New B while A registers', session_id='B')
    lane.step(); assert entered.wait(2)
    values, errors = [], []
    def compute():
        try:
            values.append(f.compute('B'))
        except Exception as exc:
            errors.append(exc)
        finally:
            r.release()
    caller = threading.Thread(target=compute)
    caller.start()
    try:
        # watch() is the caller's first operation; explicitly register B before
        # releasing A so the race does not depend on thread startup speed.
        f.watch(['B'])
        release.set(); caller.join(5)
        assert not caller.is_alive() and not errors, errors
        assert values[0]['tasks'][0]['id'] == task
    finally:
        release.set(); f.stopped.set(); lane.close(); caller.join(3); r.release(); conn.close()


def test_forget_releases_waiting_full_ticket_instead_of_hanging(api, board, monkeypatch):
    path, conn = board
    r = readers_for(api, path, monkeypatch)
    monkeypatch.setattr(r, 'external_capture', strict_empty)
    f = feedmod.Feed(r, emit=lambda _: None)
    f.watch(['S']); lane = compose(f, r)
    f.changes(['S']); drain(lane)
    lane.capacity = 0
    queued, finished = threading.Event(), threading.Event()
    queue_chat = lane._queue
    def observe(sessions):
        queue_chat(sessions)
        if lane.tickets.get('S'):
            queued.set()
    monkeypatch.setattr(lane, '_queue', observe)
    result = []
    def full():
        try:
            result.append(lane.full(['S']))
        finally:
            r.release(); finished.set()
    caller = threading.Thread(target=full)
    caller.start()
    try:
        assert queued.wait(2)
        with f.lock:
            f._forget('S')
        drain(lane)
        assert finished.wait(2), 'forgotten full request retained an unserviceable ticket'
        assert result == [{}]
    finally:
        f.stopped.set(); lane.close(); caller.join(3); r.release(); conn.close()


def test_external_reader_factory_failure_cannot_spin_unbounded(api, board, monkeypatch):
    path, conn = board
    r = readers_for(api, path, monkeypatch)
    monkeypatch.setattr(r, 'external_capture', strict_empty)
    now = [1800000000.]
    f = feedmod.Feed(r, emit=lambda _: None, clock=lambda: now[0])
    f.watch(['S']); lane = compose(f, r)
    f.changes(['S']); drain(lane)
    calls = []
    def factory():
        if threading.current_thread().name.endswith('external'):
            calls.append(1)
            raise RuntimeError('external reader construction unavailable')
        return r
    lane.reader_factory = factory
    try:
        kanban_db.create_task(conn, title='Changed source', session_id='S')
        lane.step()
        for worker in list(lane.acquiring.values()):
            worker.join(2)
        lane.service()
        assert lane.retry_at() is not None, 'reader construction failure had no finite retry'
        for _ in range(3):
            now[0] = lane.retry_at(); drain(lane)
        for _ in range(8):
            drain(lane)
        assert len(calls) == 4 and lane.retry_at() is None, (now, lane.attempts, lane.scope_attempts, lane.role_attempts, lane.projection_attempts)
    finally:
        f.stopped.set(); lane.close(); r.release(); conn.close()


def test_registration_failure_has_finite_source_owned_retry(api, board, monkeypatch):
    path, conn = board
    r = readers_for(api, path, monkeypatch)
    monkeypatch.setattr(r, 'external_capture', strict_empty)
    now, attempts = [1800000000.], []
    def fail(s):
        attempts.append(s)
        raise RuntimeError('fixture lineage unavailable')
    monkeypatch.setattr(r, 'lineage', fail)
    f = feedmod.Feed(r, emit=lambda _: None, clock=lambda: now[0])
    f.watch(['S'])
    lane = compose(f, r)
    try:
        lane.step()
        for worker in list(lane.acquiring.values()):
            worker.join(2)
        lane.service()
        assert lane.retry_at() is not None, 'lineage source failure has no bounded retry'
        for _ in range(3):
            now[0] = lane.retry_at(); drain(lane)
        for _ in range(8):
            drain(lane)
        assert attempts == ['S'] * 4 and lane.retry_at() is None
        assert 'S' in f._board_capture_failures
    finally:
        f.stopped.set(); lane.close(); r.release(); conn.close()


def test_saturated_64_fifo_coalescing_refills_while_next_strict_capture_is_held(api, board, monkeypatch):
    path, conn = board
    r = readers_for(api, path, monkeypatch)
    monkeypatch.setattr(r, 'external_capture', strict_empty)
    task = kanban_db.create_task(conn, title='Before saturation', session_id='S00')
    sessions = [f'S{i:02}' for i in range(64)]
    conn.executemany("INSERT INTO kanban_notify_subs(task_id,platform,chat_id,created_at) VALUES (?,'tui',?,1)",
                     [(task, s) for s in sessions[1:]])
    conn.commit()
    f = feedmod.Feed(r, emit=lambda _: None)
    f.watch(sessions)
    lane = compose(f, r)
    f.changes(sessions); drain(lane)
    lane.capacity = 0
    # This is a service-order proof, not an acquisition latency benchmark. The
    # deliberately held adapter must not consume the real 2s capture budget and
    # invalidate all 64 in-flight admissions on a loaded CI runner. Replace only
    # this capture module's clock (not process-global time or scheduler clocks).
    import time
    from types import SimpleNamespace
    capture_time = SimpleNamespace(**vars(time))
    capture_time.monotonic = lambda: 100.
    monkeypatch.setattr(lane.capture, 'time', capture_time)
    entered, release, served = threading.Event(), threading.Event(), threading.Event()
    publications = {s: threading.Event() for s in sessions}
    published = set()
    def emit(payload):
        completed = {s for s, b in payload.get('bundles', {}).items()
                     if any(t.get('title') == 'Saturated shared change' for t in b.get('tasks', ()))}
        published.update(completed)
        if published == set(sessions):
            served.set()
        for s in completed:
            publications[s].set()
    f.emit = emit
    def strict(held, *, include_inventory=False):
        entered.set(); release.wait()
        return strict_empty(held, include_inventory=include_inventory)
    monkeypatch.setattr(r, 'external_capture', strict)
    seen, lock = [], threading.Lock()
    project = f.project_board
    def record(store, chat, **kwargs):
        result = project(store, chat, **kwargs)
        with lock:
            seen.append(chat.session_id)
            assert len(lane.active) <= 2
        return result
    monkeypatch.setattr(f, 'project_board', record)
    try:
        conn.execute('UPDATE tasks SET title=? WHERE id=?', ('Saturated shared change', task)); conn.commit()
        lane.step()
        local = lane.acquiring['local']; local.join(3); assert not local.is_alive()
        lane.step(); assert entered.wait(2)
        with lane.lock:
            quiet_age = lane.pending['S00']
            hot_age = lane.pending['S63']
            for _ in range(64):
                lane._queue(['S63'])
            assert lane.pending['S00'] == quiet_age and lane.pending['S63'] == hot_age
            assert len(lane.pending) == 64
            lane.capacity = 2
        lane.start_service()
        # Bound each completion hand-off, not the aggregate wall time of 64
        # real reducers competing with unrelated -j4 work. No retries or sleeps.
        for s in sessions:
            assert publications[s].wait(5), 'completed pure slots were withheld behind strict acquisition'
        assert served.is_set()
        assert seen.index('S00') < seen.index('S63')
        assert not release.is_set() and set(lane.acquiring) == {'external'}
        assert all(f.bundles[s]['bundle']['tasks'][0]['title'] == 'Saturated shared change' for s in sessions)
    finally:
        release.set(); drain(lane)
        f.stopped.set(); lane.close(); r.release(); conn.close()


def test_new_scope_does_not_reset_exhausted_peer_source_budget(api, board, monkeypatch):
    path, conn = board
    r = readers_for(api, path, monkeypatch)
    def fail(*args, **kwargs):
        raise RuntimeError('strict unavailable')
    monkeypatch.setattr(r, 'external_capture', fail)
    now = [1800000000.]
    f = feedmod.Feed(r, emit=lambda _: None, clock=lambda: now[0])
    f.watch(['A'])
    lane = compose(f, r)
    try:
        f.changes(['A']); drain(lane)
        for _ in range(3):
            now[0] = lane.retry_at(); drain(lane)
        assert lane.retry_at() is None
        old = lane.store.demand(lane.external, lane.chats['A']).id
        # Registration only: no source mutation renews A's budget.
        f.watch(['B']); drain(lane)
        assert lane.scope_attempts[lane.external][old].count == 4
        assert lane.scope_attempts[lane.external][old].retry_at is None
        new = lane.store.demand(lane.external, lane.chats['B']).id
        assert lane.scope_attempts[lane.external][new].count == 1
    finally:
        f.stopped.set(); lane.close(); r.release(); conn.close()


@pytest.mark.parametrize('different_board', [False, True])
def test_slow_pure_projection_does_not_block_new_peer(api, board, monkeypatch, different_board):
    path, conn = board
    r = readers_for(api, path, monkeypatch)
    second = None
    if different_board:
        from hermes_cli.kanban_db_connect import connect
        other = path.with_name('other.db')
        second = connect(other)
        monkeypatch.setattr(r, 'boards', lambda: [('default', path), ('other', other)])
    monkeypatch.setattr(r, 'external_capture', strict_empty)
    a = kanban_db.create_task(conn, title='A original', session_id='A')
    b = kanban_db.create_task(second if second is not None else conn, title='B original', session_id='B')
    emitted = threading.Event()
    f = feedmod.Feed(r, emit=lambda p: emitted.set() if any(t.get('title') == 'B changed'
        for t in p.get('bundles', {}).get('B', {}).get('tasks', ())) else None)
    f.watch(['A', 'B'])
    lane = compose(f, r)
    f.changes(['A', 'B']); drain(lane)
    entered, release, acquired = threading.Event(), threading.Event(), threading.Event()
    project, accept = f.project_board, lane.store.accept_local
    def accepted(part, prior):
        result = accept(part, prior)
        if any(row.get('title') == 'B changed' for row in part.rows.values()):
            acquired.set()
        return result
    monkeypatch.setattr(lane.store, 'accept_local', accepted)
    def slow(store, chat, **kwargs):
        if chat.session_id == 'A':
            entered.set()
            assert release.wait(15)
        return project(store, chat, **kwargs)
    monkeypatch.setattr(f, 'project_board', slow)
    lane.start_service()
    try:
        conn.execute('UPDATE tasks SET title=? WHERE id=?', ('A changed', a)); conn.commit()
        lane.step()
        assert entered.wait(5)
        writer = second if second is not None else conn
        writer.execute('UPDATE tasks SET title=? WHERE id=?', ('B changed', b)); writer.commit()
        lane.step()
        # Async capture (including two independent boards) precedes projection.
        # Bound each stage separately, and keep A held throughout both stages.
        assert acquired.wait(5), 'B source was not acquired while A projected'
        assert emitted.wait(5), 'slow A projection withheld accepted new B'
        assert not release.is_set()
        assert len(lane.active) <= 2 and set(lane.acquiring) <= {'local', 'external'}
    finally:
        release.set(); drain(lane)
        f.stopped.set(); lane.close(); r.release(); conn.close()
        if second is not None:
            second.close()


def drain(lane):
    # Join only owned bounded work; event barriers position races, never sleeps.
    for _ in range(40):
        lane.step()
        workers = list(lane.active.values()) + list(getattr(lane, 'acquiring', {}).values())
        for worker in workers:
            worker.join(3)
        lane.service()
        # Completion can leave a fresh registration/demand wake with no live
        # worker. Consume it before declaring the manually driven lane quiescent.
        if (not lane.active and not lane.pending and not getattr(lane, 'acquiring', {})
                and not lane.feed._board.is_set()):
            return
    raise AssertionError('source coordinator did not drain')


def compose(f, r):
    method = getattr(f, 'compose_board_sources', None)
    return method(lambda: r, gateway='fixture', profile='default') if method else feedmod.BoardLane(f, lambda: r)


def test_manual_drain_consumes_completion_wake_before_quiescence():
    from types import SimpleNamespace
    wake = threading.Event()
    steps = []
    def step():
        wake.clear()
        steps.append(len(steps))
    def service():
        # Deterministic completion-after-step, before the idle check. The next
        # turn must observe newly established demand even with no live worker.
        if len(steps) == 1:
            wake.set()
    lane = SimpleNamespace(step=step, service=service, active={}, pending={},
                           acquiring={}, feed=SimpleNamespace(_board=wake))
    drain(lane)
    assert steps == [0, 1]
    assert not wake.is_set()


@pytest.mark.parametrize('route', ['compute', 'full', 'tick', 'catchup', 'board', 'keyed'])
def test_real_all_routes_share_materialization_and_pure_projection(api, board, monkeypatch, route):
    import collections
    import sqlite3
    path, conn = board
    r = readers_for(api, path, monkeypatch)
    monkeypatch.setattr(r, 'external_capture', strict_empty)
    # Real board capture is shared by eight lineages/subscriptions.
    task = kanban_db.create_task(conn, title='Shared source row', session_id='S0')
    conn.executemany("INSERT INTO kanban_notify_subs(task_id,platform,chat_id,created_at) VALUES (?,'tui',?,1)",
                     [(task, f'S{i}') for i in range(1, 8)])
    conn.commit()
    counts = collections.Counter()
    detail = r._detail
    def counted(*args, **kwargs):
        counts['details'] += 1
        return detail(*args, **kwargs)
    monkeypatch.setattr(r, '_detail', counted)
    connect = sqlite3.connect
    def no_projection_io(*args, **kwargs):
        assert not threading.current_thread().name.startswith('session-panel-projection-'), 'projection performed SQLite I/O'
        return connect(*args, **kwargs)
    monkeypatch.setattr(sqlite3, 'connect', no_projection_io)
    f = feedmod.Feed(r, emit=lambda _: None)
    sessions = [f'S{i}' for i in range(8)]
    f.watch(sessions)
    lane = compose(f, r)
    try:
        f.changes(sessions)
        drain(lane)
        original = counts['details']
        assert original == 1
        def forbidden(*args, **kwargs):
            raise AssertionError('legacy per-chat board reader invoked')
        for name in ('linked', 'done', 'card', 'board_changes'):
            monkeypatch.setattr(r, name, forbidden)
        if route == 'compute':
            for s in sessions:
                f._publish({s: f.compute(s)})
        elif route == 'full':
            f._publish(f._compute_all(sessions, refresh=True))
        elif route == 'tick':
            f.tick()
        elif route == 'keyed':
            f.queue_keyed(set(sessions))
            assert f._keyed_pending == set(sessions)
            f.refresh_keyed()
            assert not getattr(f, '_keyed_pending', set())
        elif route == 'catchup':
            assert set(f.changes(sessions)['bundles']) == set(sessions)
        else:
            assert feedmod.BoardLane(f, lambda: r) is lane
            drain(lane)
        assert counts['details'] == original
        assert all(f.bundles[s]['bundle']['tasks'][0]['id'] == task for s in sessions)
        assert all('board_admission' in f.bundles[s] for s in sessions)
    finally:
        f.stopped.set(); lane.close(); r.release(); conn.close()


def test_composed_keyed_recovery_keeps_real_watcher_alive(api, board, monkeypatch):
    from types import SimpleNamespace
    path, conn = board
    r = readers_for(api, path, monkeypatch)
    monkeypatch.setattr(r, 'external_capture', strict_empty)
    kanban_db.create_task(conn, title='Watcher recovery', session_id='S')
    f = feedmod.Feed(r, emit=lambda _: None)
    f.watch(['S']); lane = compose(f, r)
    f.changes(['S']); drain(lane)
    continued, closed = threading.Event(), threading.Event()
    class Hints:
        next_reconcile = 0.
        reader = SimpleNamespace(source_dirty=False)
        calls = 0
        def due(self):
            self.calls += 1
            return self.calls == 1
    hints = Hints()
    monkeypatch.setattr(api, '_keyed_hints', lambda: hints)
    class Watch:
        stale = False
        calls = 0
        def set_paths(self, *args, **kwargs):
            pass
        def wait(self, timeout):
            self.calls += 1
            if self.calls > 1:
                continued.set()
                f.stopped.wait(5)
            return False
        def close(self):
            closed.set()
    thread = threading.Thread(target=api._watch, args=(f, Watch(), lambda: [path]),
                              name='fixture-composed-keyed-watch')
    f._watching = True
    try:
        thread.start()
        assert continued.wait(2), 'keyed recovery killed the actual watcher before its next wait'
        assert thread.is_alive() and not closed.is_set() and f._watching
        assert f._keyed_pending == {'S'}
        f.refresh_keyed()
        assert not getattr(f, '_keyed_pending', set())
        assert f.bundles['S']['bundle']['tasks'][0]['title'] == 'Watcher recovery'
    finally:
        f.stopped.set(); thread.join(3)
        lane.close(); r.release(); conn.close()
    assert not thread.is_alive() and closed.is_set()


def test_keyed_admission_failure_does_not_mutate_pending(api, board, monkeypatch):
    path, conn = board
    r = readers_for(api, path, monkeypatch)
    monkeypatch.setattr(r, 'external_capture', strict_empty)
    f = feedmod.Feed(r, emit=lambda _: None)
    f.watch(['S']); lane = compose(f, r)
    try:
        f.changes(['S']); drain(lane)
        f._keyed_pending = {'existing'}
        def fail(*args):
            raise RuntimeError('admission refused')
        monkeypatch.setattr(lane, '_admit', fail, raising=False)
        with pytest.raises(RuntimeError, match='admission refused'):
            f.queue_keyed({'S'})
        assert f._keyed_pending == {'existing'}
    finally:
        f.stopped.set(); lane.close(); r.release(); conn.close()


@pytest.mark.parametrize('new_chat', [False, True])
def test_real_new_local_write_after_strict_entry_publishes_before_release(api, board, monkeypatch, new_chat):
    path, conn = board
    monkeypatch.setenv('HERMES_PANEL_BOARD_COALESCE', '0')
    r = readers_for(api, path, monkeypatch)
    monkeypatch.setattr(r, 'external_capture', strict_empty)
    task = kanban_db.create_task(conn, title='Before strict entry', session_id='S')
    emitted = threading.Event()
    target = 'NEW' if new_chat else 'S'
    def emit(payload):
        b = payload.get('bundles', {}).get(target, {})
        if any(t.get('title') == 'NEW after strict entry' for t in b.get('tasks', ())):
            emitted.set()
    f = feedmod.Feed(r, emit=emit)
    f.watch(['S'])
    lane = compose(f, r)
    f.changes(['S'])
    drain(lane)  # Warm and drain BEFORE holding the next strict acquisition.
    entered, release = threading.Event(), threading.Event()
    def strict(held, *, include_inventory=False):
        entered.set()
        assert release.wait(5), 'cleanup must release strict capture'
        return strict_empty(held, include_inventory=include_inventory)
    monkeypatch.setattr(r, 'external_capture', strict)
    # A source change enters strict capture; the tested local write is LATER.
    conn.execute('UPDATE tasks SET title=? WHERE id=?', ('Capture trigger', task)); conn.commit()
    lane.start_service()
    def owner():
        try:
            while not f.stopped.is_set():
                lane.step()
                f._board.wait()
        finally:
            r.release()
    thread = threading.Thread(target=owner)
    thread.start()
    try:
        assert entered.wait(2)
        if new_chat:
            kanban_db.create_task(conn, title='NEW after strict entry', session_id=target)
            f.watch([target])
        else:
            conn.execute('UPDATE tasks SET title=?,status=? WHERE id=?', ('NEW after strict entry', 'scheduled', task))
            conn.commit()
        f._board.set()
        assert emitted.wait(2), 'new local write waits for strict external capture'
        assert not release.is_set()
        assert any(t['title'] == 'NEW after strict entry' for t in f.bundles[target]['bundle']['tasks'])
    finally:
        f.stopped.set(); release.set(); f._board.set()
        thread.join(5); lane.close(); r.release(); conn.close()
    assert not thread.is_alive()
