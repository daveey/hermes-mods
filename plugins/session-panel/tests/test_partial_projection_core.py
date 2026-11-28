"""B2a real CoreReaders/ActionStore composition; no production route cutover."""
import collections
import contextlib
import importlib.util
import sqlite3
import socket
import sys
import threading
import uuid
from pathlib import Path

import pytest

from test_core_readers import api, board, kanban_db
from test_snapshot_acquisition import acquisition, acquire
from test_reader_batch import graph
from test_feed import feedmod


def canonical(chat, inputs=None):
    return {'session': chat.session_id, 'lineage': [chat.session_id], 'requests': [], 'outcome_json': {},
            'canonical_seq': 1, 'computed_at': 1., **(inputs or {})}


def test_real_new_local_write_after_held_strict_entry_projects_before_release(acquisition, monkeypatch):
    r, cap, m, local, store, conn = acquisition
    chat = m.ChatKey('fixture', 'default', 'S')
    store.register([m.Watch(chat, '', ('S',), 0)])
    task = kanban_db.create_task(conn, title='Before strict entry', session_id='S')
    previous, _ = acquire(acquisition)
    external = m.SourceKey('fixture', 'default', 'default', 'hub-links', '/example/external')
    store.add_source(external)
    entered, release = threading.Event(), threading.Event()
    def strict(held, *, include_inventory=False):
        entered.set()
        assert release.wait(5)
        # The strict source boundary is held; never contact the user's services.
        captured = {sid: () for sid in held}
        inventory = {'origin': [], 'history': [], 'replica': None, 'gateway': 'fixture', 'profile': 'default'}
        return (captured, inventory) if include_inventory else captured
    monkeypatch.setattr(r, 'external_capture', strict)
    ds = store.demands(external)
    hint = cap.CaptureHints(store.version(external), store.owner_token(external), 1800000000.)
    result = []
    def run():
        try:
            result.extend(r.capture_external(ds, {}, {}, hints={external: hint}))
        except Exception as exc:
            result.append(exc)
        finally:
            r.release()
    worker = threading.Thread(target=run)
    worker.start()
    try:
        assert entered.wait(2)
        conn.execute('UPDATE tasks SET title=?,status=? WHERE id=?', ('NEW after strict entry', 'scheduled', task))
        conn.commit()
        part, _ = acquire(acquisition, previous)
        f = feedmod.Feed(r, emit=lambda _: None, clock=lambda: 1800000000.)
        f.watch(['S'])
        p = f.project_board(store, chat, canonical_base=canonical(chat))
        assert f.publish_projections(store, {'S': p})['S'].emit_succeeded
        assert f.bundles['S']['bundle']['tasks'][0]['title'] == 'NEW after strict entry'
        assert p.missing_scopes == {external: store.demand(external, chat)}
        assert f.bundles['S']['source_stale'] and not release.is_set()
    finally:
        release.set(); worker.join(5)
    assert not worker.is_alive()


@pytest.mark.parametrize('count', [1, 8, 64])
def test_board_full_and_catchup_share_real_materialization_with_no_projection_io(acquisition, monkeypatch, count):
    r, cap, m, source, store, conn = acquisition
    task = kanban_db.create_task(conn, title='Shared immutable work', session_id='S0')
    conn.executemany("INSERT INTO kanban_notify_subs(task_id,platform,chat_id,created_at) VALUES (?,'tui',?,1)",
                     [(task, f'S{i}') for i in range(1, count)])
    conn.commit()
    chats = [m.ChatKey('fixture', 'default', f'S{i}') for i in range(count)]
    store.register([m.Watch(c, '', (c.session_id,), 0) for c in chats])
    reads = collections.Counter()
    original = r._detail
    def detail(*args, **kwargs):
        reads['details'] += 1
        return original(*args, **kwargs)
    monkeypatch.setattr(r, '_detail', detail)
    # Receipt time is the projector's computation clock; strict compute uses
    # the Feed clock. Pin both, rather than ageing a current real receipt by
    # months on public cores whose execution annotations expose that gap.
    monkeypatch.setattr(cap.time, 'time_ns', lambda: 1800000000_000000000)
    part, _ = acquire(acquisition)
    assert reads['details'] == part.metrics['details_materialized'] == 1
    f = feedmod.Feed(r, emit=lambda _: None, clock=lambda: 1800000000.)
    f.watch([c.session_id for c in chats])
    # Compare complete canonical board fields, model and owner actions at pinned
    # time. Canonical decisions are acquired separately, not in the projector.
    strict = {c: f.compute(c.session_id, readers=m.SnapshotReaders(store.pin(c)[0], c), base=canonical(c)) for c in chats}
    def forbidden(*args, **kwargs):
        raise AssertionError('per-chat projection performed SQLite/network I/O')
    monkeypatch.setattr(sqlite3, 'connect', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    monkeypatch.setattr(r, 'card', forbidden)
    monkeypatch.setattr(r, 'panel_inputs', forbidden)
    for route in ('board', 'full', 'catchup'):
        ps = f.project_boards(store, {c: canonical(c) for c in chats})
        for c in chats:
            actual = ps[c.session_id]
            for field in ('tasks', 'done', 'cards'):
                assert cap.snapshot.thaw(actual.bundle[field]) == strict[c][field], (route, field)
            assert actual.bundle['model']['sections'] == m.freeze(strict[c]['model']['sections'])
        f.publish_projections(store, ps)
    assert reads['details'] == 1


@pytest.mark.parametrize('terminal', ['done', 'archived'])
def test_real_terminal_immediately_after_old_pin_cannot_be_revived_by_later_seq(acquisition, terminal):
    r, cap, m, source, store, conn = acquisition
    task = kanban_db.create_task(conn, title='Waiting before old pin', session_id='S')
    chat = m.ChatKey('fixture', 'default', 'S')
    store.register([m.Watch(chat, '', ('S',), 0)])
    previous, _ = acquire(acquisition)
    old = store.pin(chat)
    conn.execute('UPDATE tasks SET status=? WHERE id=?', (terminal, task)); conn.commit()
    acquire(acquisition, previous)
    f = feedmod.Feed(r, emit=lambda _: None, clock=lambda: 1800000000.)
    f.watch(['S'])
    latest = f.project_board(store, chat, canonical_base=canonical(chat), seq=10)
    assert f.publish_projections(store, {'S': latest})['S'].disposition == 'stored'
    stale = f.project_board(store, chat, canonical_base=canonical(chat), pin=old, seq=1000)
    assert f.publish_projections(store, {'S': stale})['S'].disposition == 'superseded'
    assert not f.bundles['S']['bundle']['model']['sections']['waiting']


def action_core():
    path = Path(__file__).resolve().parents[2] / 'user-steps' / 'core.py'
    name = '_b2a_real_action_store'
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
    return sys.modules[name]


@pytest.mark.parametrize('retirement', ['withdraw', 'deleted_session', 'approve'])
def test_real_current_actionstore_cannot_be_revived_or_errors_cleared_by_board(acquisition, tmp_path, monkeypatch, retirement):
    from hermes_state import SessionDB
    r, cap, m, source, store, conn = acquisition
    db = SessionDB(db_path=tmp_path / 'state.db')
    db.create_session('S', 'desktop')
    core = action_core()
    owner = core.server_owner('S')
    actions = core.ActionStore()
    rid = str(uuid.uuid4())
    actions.create(owner, dict(schemaVersion=2, owner=dict(owner), requestId=rid, revision=1,
        kind='approval', title='Authorize exactly the synthetic scope',
        scope=dict(markdown='Only change example.py; run the tests.', complete=True, sourceRefs=[]),
        limits=[dict(key='cost', value='0', permissionRequired=True)], exclusions=['No network changes'],
        outcomes=dict(approve='Authorize this scope only', revise='Keep blocked', decline='Cancel'),
        choices=[dict(id=e, label=e.title(), effect=e) for e in ('approve', 'revise', 'decline')], aliases=[], state='open'))
    chat = m.ChatKey('fixture', 'default', 'S')
    store.register([m.Watch(chat, '', ('S',), 0)])
    task = kanban_db.create_task(conn, title='Board work', session_id='S')
    previous, _ = acquire(acquisition)
    f = feedmod.Feed(r, emit=lambda _: None, clock=lambda: 1800000000.)
    f.watch(['S'])
    try:
        inputs = r.decision_inputs('S', ['S'])
        assert len(inputs['requests']) == 1 and inputs['requests'][0]['requestId'] == rid
        p = f.project_board(store, chat, canonical_base=canonical(chat, inputs))
        f.publish_projections(store, {'S': p})
        assert f.bundles['S']['bundle']['model']['sections']['needs_you']
        old_base = f.bundles['S']['bundle']
        if retirement == 'withdraw':
            core.withdraw_request(actions, None, owner, rid, by='agent', reason='fixture scope is moot')
        elif retirement == 'deleted_session':
            assert db.delete_session('S')
        else:
            operation = actions.decide(owner, dict(requestId=rid, expectedRevision=1, choiceId='approve',
                idempotencyKey=str(uuid.uuid4())))['operation']
            assert operation['intent']['executionAuthorized']
            assert operation['dispatchState'] == 'pending'
        current = r.decision_inputs('S', ['S'])
        f._publish({'S': {**old_base, **current, 'canonical_seq': 50}}, canonical_only=True)
        assert not f.bundles['S']['bundle']['model']['sections']['needs_you']
        conn.execute('UPDATE tasks SET title=? WHERE id=?', ('New board title', task)); conn.commit()
        acquire(acquisition, previous)
        stale = f.project_board(store, chat, canonical_base=old_base, seq=100)
        f.publish_projections(store, {'S': stale})
        b = f.bundles['S']['bundle']
        assert b['requests'] == current['requests'] and not b['model']['sections']['needs_you']
        assert b['canonical_seq'] == 50
        assert b['computed_at'] == stale.bundle['computed_at']
        f._decision_failures.add('S')
        f.publish_projections(store, {'S': stale})
        assert 'S' in f._decision_failures
    finally:
        db.close()


def graph_store(graph):
    r, local, remote, ksa = graph
    cap = r.snapshot_modules()
    m, store = cap.snapshot, cap.snapshot.SnapshotStore()
    board_path = str(r.boards()[0][1].resolve())
    source = m.SourceKey('local-test', 'default', 'default', 'local', board_path)
    external = m.SourceKey('local-test', 'default', 'default', 'hub-links', '/example/external')
    chat = m.ChatKey('local-test', 'default', 'S0')
    for s in (source, external):
        store.add_source(s)
    store.register([m.Watch(chat, '', ('S0',), 0)])
    hint = cap.CaptureHints(store.version(source), store.owner_token(source), 1800000000.)
    part = r.capture_local(source, None, store.demands(source), hint)
    store.accept_local(part, hint.prior_version)
    hint = cap.CaptureHints(store.version(external), store.owner_token(external), 1800000000.)
    part = r.capture_external(store.demands(external), {}, {}, hints={external: hint})[0]
    store.accept_external(part, hint.prior_version)
    return cap, m, store, chat


def test_real_replica_observation_pull_and_clock_regression_preserve_age_and_fence_old_producer(graph, monkeypatch):
    r, local, remote, ksa = graph
    now = [1800000000.]
    monkeypatch.setattr(ksa.time, 'time', lambda: now[0])
    path = r.ksc.shadow_path('default')
    with contextlib.closing(sqlite3.connect(path)) as c, c:
        columns = {row[1] for row in c.execute('PRAGMA table_info(kanban_sync_state)')}
        if 'pulled_at' not in columns:
            c.execute('ALTER TABLE kanban_sync_state ADD COLUMN pulled_at REAL')
        if not c.execute('SELECT 1 FROM kanban_sync_state').fetchone():
            c.execute("INSERT INTO kanban_sync_state(board_id,gateway_id,hlc_last,mode,pulled_at) VALUES ('default','remote-test','0','replica',?)", (now[0],))
        c.execute('UPDATE kanban_sync_state SET pulled_at=?', (now[0],))
    cap, m, store, chat = graph_store(graph)
    raw = r.external_capture({'S0': {'lineage': {'S0'}, 'refs': set()}})['S0']
    f = feedmod.Feed(r, emit=lambda _: None, clock=lambda: now[0])
    f.watch(['S0'])
    p = f.project_board(store, chat, canonical_base=canonical(chat), external_capture=raw)
    f.publish_projections(store, {'S0': p})
    before = f.bundles['S0']['bundle']
    old = f.project_board(store, chat, canonical_base=before, external_capture=raw, seq=1000)
    held, release = threading.Event(), threading.Event()
    results = []
    def producer():
        held.set(); assert release.wait(5)
        results.append(f.publish_projections(store, {'S0': old})['S0'])
    worker = threading.Thread(target=producer); worker.start()
    try:
        assert held.wait(2)
        def no_hydration(*args, **kwargs):
            raise AssertionError('clock-only pull hydrated board details')
        monkeypatch.setattr(r, '_detail', no_hydration)
        for stamp in (1800000001., 1799999999.):
            with contextlib.closing(sqlite3.connect(path)) as c, c:
                c.execute('UPDATE kanban_sync_state SET pulled_at=?', (stamp,))
            captured = r.external_capture({'S0': {'lineage': {'S0'}, 'refs': set()}})['S0']
            observed = f.carry_board_observations(store, p, captured)
            assert f.publish_projections(store, {'S0': observed})['S0'].disposition == 'stored'
            assert f.publish_projections(store, {'S0': observed})['S0'].disposition == 'superseded'
            p = observed
            after = f.bundles['S0']['bundle']
            assert next(t for t in after['tasks'] if t['id'] == remote)['synced_at'] == stamp
            for field in ('computed_at', 'computed_seq', 'cards'):
                assert after[field] == before[field]
            # A legacy observation captured before opt-in cannot overwrite the
            # current token-qualified carrier, even with a later sequence.
            f._publish({}, age_only=True, observations={'S0': (10000, raw)})
            after_legacy = f.bundles['S0']['bundle']
            assert next(t for t in after_legacy['tasks'] if t['id'] == remote)['synced_at'] == stamp
            assert f.bundles['S0']['board_projection'].publication_token is observed.publication_token
            for field in ('computed_at', 'computed_seq', 'cards'):
                assert after_legacy[field] == before[field]
        with contextlib.closing(sqlite3.connect(path)) as c, c:
            c.execute("UPDATE tasks SET title='semantic source change' WHERE id=?", (remote,))
        changed = r.external_capture({'S0': {'lineage': {'S0'}, 'refs': set()}})['S0']
        with pytest.raises(ValueError, match='identical canonical source semantics'):
            f.carry_board_observations(store, p, changed)
    finally:
        release.set(); worker.join(5)
    assert results[0].disposition == 'superseded'
    assert next(t for t in f.bundles['S0']['bundle']['tasks'] if t['id'] == remote)['synced_at'] == 1799999999.


@pytest.mark.parametrize('depth', [1, 2])
def test_real_remote_direct_and_multihop_parent_actions_keep_exact_owner(graph, monkeypatch, depth):
    from hermes_cli.kanban_db_connect import connect
    r, local, remote, ksa = graph
    monkeypatch.setattr(ksa.time, 'time', lambda: 1800000000.)
    with contextlib.closing(connect(r.ksc.shadow_path('default'))) as c:
        leaf = kanban_db.create_task(c, title='Remote exact leaf', session_id='elsewhere')
        c.execute("UPDATE tasks SET created_on='remote-test',status='ready' WHERE id=?", (leaf,))
        parent = leaf
        if depth == 2:
            parent = kanban_db.create_task(c, title='Intermediate wait', session_id='elsewhere')
            c.execute("UPDATE tasks SET created_on='remote-test',status='todo' WHERE id=?", (parent,))
            c.execute('INSERT INTO task_links(parent_id,child_id) VALUES (?,?)', (leaf, parent))
        c.execute("UPDATE tasks SET status='todo' WHERE id=?", (remote,))
        c.execute('INSERT INTO task_links(parent_id,child_id) VALUES (?,?)', (parent, remote)); c.commit()
    cap, m, store, chat = graph_store(graph)
    f = feedmod.Feed(r, emit=lambda _: None, clock=lambda: 1800000000.)
    f.watch(['S0'])
    p = f.project_board(store, chat, canonical_base=canonical(chat))
    assert p.bundle['cards'][leaf]['gateway'] == 'remote-test'
    row = next(row for row in p.bundle['model']['sections']['waiting'] if row['id'] == remote)
    targets = [dict(a['target']) for a in row['actions'] if a['kind'] == 'open_card']
    assert not p.missing_scopes
    assert {'id': leaf, 'gateway': 'remote-test', 'profile': 'default', 'board': 'default'} in targets
