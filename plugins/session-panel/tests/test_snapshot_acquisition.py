"""Real core/SQLite acquisition and memory-only scoped projection controls."""
from collections import Counter
import contextlib
import sqlite3

import pytest

from test_core_readers import api, board, kanban_db, readers_for


@pytest.fixture
def acquisition(api, board, monkeypatch):
    path, conn = board
    r = readers_for(api, path, monkeypatch)
    mod = r.snapshot_modules()
    m = mod.snapshot
    source = m.SourceKey('fixture', 'default', 'default', 'local', str(path))
    store = m.SnapshotStore()
    store.add_source(source)
    try:
        yield r, mod, m, source, store, conn
    finally:
        r.release()
        conn.close()


def acquire(values, previous=None):
    r, mod, m, source, store, _ = values
    hints = mod.CaptureHints(store.version(source), store.owner_token(source), 1800000000.0)
    part = r.capture_local(source, previous, store.demands(source), hints)
    snapshot, admissions = store.accept_local(part, hints.prior_version)
    return part, snapshot


@pytest.mark.parametrize('chats', [1, 8, 64, 501, 4096])
def test_real_union_acquisition_shared_materialization_and_999_bindings(acquisition, chats, monkeypatch):
    r, mod, m, source, store, conn = acquisition
    task = kanban_db.create_task(conn, title='Shared work', session_id='S0', initial_status='running')
    # Core connect is autocommit: keep bulk fixture setup out of acquisition
    # timing without paying one durable transaction per subscription.
    conn.execute('BEGIN')
    conn.execute("UPDATE tasks SET status='ready' WHERE id=?", (task,))
    conn.executemany("INSERT INTO kanban_notify_subs (task_id,platform,chat_id,created_at) VALUES (?,'tui',?,1)",
                     [(task, f'S{i}') for i in range(1, chats)])
    conn.commit()
    # 4096 lineage identifiers are a single scope, not 4096 panel payloads.
    watches = [m.Watch(m.ChatKey('fixture', 'default', f'S{i}'), '', (f'S{i}',), 0)
               for i in range(chats if chats <= 64 else 1)]
    if chats > 64:
        watches = [m.Watch(watches[0].chat, '', tuple(f'S{i}' for i in range(chats)), 0)]
    store.register(watches)
    counts = Counter()
    original = r._detail
    def detail(conn, tid, *args, **kwargs):
        counts[tid] += 1
        return original(conn, tid, *args, **kwargs)
    monkeypatch.setattr(r, '_detail', detail)
    part, snapshot = acquire(acquisition)
    assert counts[task] == 1
    assert part.metrics['membership_pairs'] == chats
    assert part.metrics['opens'] == 1
    assert part.metrics['details_materialized'] == 1
    def forbidden(*args, **kwargs):
        raise AssertionError('projection attempted SQLite I/O')
    monkeypatch.setattr(sqlite3, 'connect', forbidden)
    for index, watch in enumerate(watches):
        sr = m.SnapshotReaders(snapshot, watch.chat)
        ids = sr.lineage(watch.chat.session_id)
        assert sr.linked(ids)[0]['via'] == ('created' if index == 0 else 'subscribed')
        assert sr.card_key(m.CardKey('fixture', 'default', 'default', task))['detail']['task']['id'] == task



def test_source_generation_reset_forces_real_rebuild(acquisition):
    r, mod, m, source, store, conn = acquisition
    kanban_db.create_task(conn, title='Work', session_id='S')
    chat = m.ChatKey('fixture', 'default', 'S')
    store.register([m.Watch(chat, '', ('S',), 0)])
    first, _ = acquire(acquisition)
    store.reset(source)
    successor, _ = acquire(acquisition, first)
    assert successor.metrics['full'] is True
    assert successor.metrics['details_materialized'] == 1


def test_parent_change_invalidates_child_enrichment_and_detail(acquisition):
    r, mod, m, source, store, conn = acquisition
    parent = kanban_db.create_task(conn, title='Original producer', initial_status='running')
    child = kanban_db.create_task(conn, title='Dependent', session_id='S', parents=[parent])
    chat = m.ChatKey('fixture', 'default', 'S')
    store.register([m.Watch(chat, '', ('S',), 0)])
    first, _ = acquire(acquisition)
    with conn:
        conn.execute('UPDATE tasks SET title=? WHERE id=?', ('Changed producer', parent))
    successor, _ = acquire(acquisition, first)
    fresh, _ = acquire(acquisition)
    key = m.CardKey('fixture', 'default', 'default', child)
    assert successor.rows[key] == fresh.rows[key]
    assert successor.details[key] == fresh.details[key]


@pytest.mark.parametrize('operation', ['insert', 'update', 'delete'])
def test_remote_parent_projection_changes_invalidate_without_hydrated_parent(acquisition, operation):
    r, cap, m, source, store, conn = acquisition
    child = kanban_db.create_task(conn, title='Dependent', session_id='S')
    with conn:
        conn.execute('CREATE TABLE IF NOT EXISTS kanban_sync_remote_parents '
                     '(parent_id TEXT PRIMARY KEY,status TEXT NOT NULL,refreshed_at INTEGER NOT NULL)')
        conn.execute('INSERT INTO task_links(parent_id,child_id) VALUES (?,?)', ('remote-parent', child))
        if operation != 'insert':
            conn.execute('INSERT INTO kanban_sync_remote_parents VALUES (?,?,?)', ('remote-parent', 'running', 1))
    chat = m.ChatKey('fixture', 'default', 'S')
    store.register([m.Watch(chat, '', ('S',), 0)])
    first, _ = acquire(acquisition)
    with conn:
        if operation == 'insert':
            conn.execute('INSERT INTO kanban_sync_remote_parents VALUES (?,?,?)', ('remote-parent', 'running', 2))
        elif operation == 'update':
            conn.execute('UPDATE kanban_sync_remote_parents SET status=?,refreshed_at=?', ('done', 3))
        else:
            conn.execute('DELETE FROM kanban_sync_remote_parents')
    changed, _ = acquire(acquisition, first)
    fresh, _ = acquire(acquisition)
    assert changed.receipt.remote_parent_digest != first.receipt.remote_parent_digest
    assert changed.metrics['details_materialized'] >= 1
    key = m.CardKey('fixture', 'default', 'default', child)
    assert changed.rows[key] == fresh.rows[key]
    assert changed.details[key] == fresh.details[key]
    assert changed.remote_parents == fresh.remote_parents


def test_unchanged_and_changed_only_detail_reuse(acquisition):
    r, mod, m, source, store, conn = acquisition
    tasks = [kanban_db.create_task(conn, title=name, session_id='S', initial_status='running') for name in ('one', 'two')]
    conn.execute("UPDATE tasks SET status='ready'")
    conn.commit()
    chat = m.ChatKey('fixture', 'default', 'S')
    store.register([m.Watch(chat, '', ('S',), 0)])
    first, _ = acquire(acquisition)
    second, _ = acquire(acquisition, first)
    assert second.metrics['full'] is False
    assert second.metrics['details_materialized'] == 0
    for key in first.details:
        assert second.details[key] is first.details[key]
    conn.execute('UPDATE tasks SET title=? WHERE id=?', ('changed', tasks[0]))
    conn.commit()
    third, snapshot = acquire(acquisition, second)
    assert third.metrics['details_materialized'] == 1
    unchanged = m.CardKey('fixture', 'default', 'default', tasks[1])
    assert third.details[unchanged] is second.details[unchanged]
    assert m.SnapshotReaders(snapshot, chat).card(tasks[0], 'default')['detail']['task']['title'] == 'changed'


def test_unrelated_real_board_write_does_not_admit_covered_chat(acquisition):
    r, mod, m, source, store, conn = acquisition
    kanban_db.create_task(conn, title='Watched', session_id='S')
    other = kanban_db.create_task(conn, title='Unwatched', session_id='elsewhere')
    chat = m.ChatKey('fixture', 'default', 'S')
    store.register([m.Watch(chat, '', ('S',), 0)])
    first, _ = acquire(acquisition)
    _, served = store.pin(chat)
    assert store.acknowledge(served)
    with conn:
        conn.execute('UPDATE tasks SET title=? WHERE id=?', ('Unrelated edit', other))
    hints = mod.CaptureHints(store.version(source), store.owner_token(source), 1800000000.0)
    second = r.capture_local(source, first, store.demands(source), hints)
    _, admissions = store.accept_local(second, hints.prior_version)
    assert admissions == ()
    assert store.version(source) == second.receipt.version


def test_membership_removal_is_authoritative_and_missing_source_not_empty(acquisition):
    r, mod, m, source, store, conn = acquisition
    task = kanban_db.create_task(conn, title='Work', session_id='elsewhere', initial_status='running')
    conn.execute("INSERT INTO kanban_notify_subs(task_id,platform,chat_id,created_at) VALUES (?,'tui','S',1)", (task,))
    conn.commit()
    chat = m.ChatKey('fixture', 'default', 'S')
    store.register([m.Watch(chat, '', ('S',), 0)])
    first, _ = acquire(acquisition)
    conn.execute('DELETE FROM kanban_notify_subs WHERE task_id=?', (task,))
    conn.commit()
    second, snapshot = acquire(acquisition, first)
    assert m.SnapshotReaders(snapshot, chat).linked(['S']) == []
    store.fail(source, store.version(source).source_generation, 'unreadable', store.demands(source))
    with pytest.raises(m.CoverageUnavailable):
        m.SnapshotReaders(store.pin(chat)[0], chat).linked(['S'])


def test_real_canonical_local_differential_pinned_clock(acquisition, monkeypatch):
    from hermes_cli import kanban_session_activity as ksa
    from hermes_cli.kanban_execution_observations import add_cards
    r, mod, m, source, store, conn = acquisition
    tasks = [kanban_db.create_task(conn, title=name, session_id='S', initial_status='running')
             for name in ('one', 'two')]
    conn.execute("UPDATE tasks SET status='ready'")
    conn.execute("INSERT INTO kanban_notify_subs(task_id,platform,chat_id,created_at) VALUES (?,'tui','B',1)", (tasks[0],))
    conn.commit()
    monkeypatch.setattr(ksa.time, 'time', lambda: 1800000000.0)
    chats = [m.ChatKey('fixture', 'default', name) for name in ('S', 'B')]
    store.register([m.Watch(chat, '', (chat.session_id,), 0) for chat in chats])
    baseline = {}
    for chat in chats:
        rows = ksa._read_session_tasks(r.boards()[0][1], [chat.session_id], tuple(ksa.LISTED_STATUSES))
        for row in rows:
            row.setdefault('key', f"default/{row['id']}")
            row.pop('last_heartbeat_at', None)
        baseline[chat] = add_cards(rows, now=1800000000.0)
    part, snapshot = acquire(acquisition)
    for chat in chats:
        actual = m.SnapshotReaders(snapshot, chat).linked([chat.session_id])
        assert actual == baseline[chat]
        for row in actual:
            detail = r._detail(conn, row['id'], 'default', False, snapshot_at=1800000000.0, parent_connection=None)
            assert detail.pop('actionability')['version'] == 1
            assert detail.pop('actionability_inputs')
            captured = m.SnapshotReaders(snapshot, chat).card(row['id'], 'default')['detail']
            assert 'actionability' not in captured, 'inactive acquisition must not certify diagnostics'
            assert 'actionability_inputs' not in captured
            assert captured == detail
