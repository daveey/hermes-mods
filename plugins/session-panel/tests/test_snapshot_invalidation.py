"""Additional canonical invalidation and generation reset differentials."""
import pytest
from test_snapshot_acquisition import acquisition, acquire
from test_core_readers import api, board, kanban_db


def test_deep_enrichment_dependency_past_running_detail_stop(acquisition):
    r, cap, m, source, store, conn = acquisition
    blocked = kanban_db.create_task(conn, title='Distant blocked producer')
    with conn:
        conn.execute("UPDATE tasks SET status='blocked' WHERE id=?", (blocked,))
    middle = kanban_db.create_task(conn, title='Middle', parents=[blocked])
    running = kanban_db.create_task(conn, title='Running', parents=[middle], initial_status='running')
    child = kanban_db.create_task(conn, title='Dependent', session_id='S', parents=[running])
    with conn:
        conn.execute("UPDATE tasks SET status='ready' WHERE id IN (?,?)", (child, middle))
        conn.execute("UPDATE tasks SET status='running' WHERE id=?", (running,))
    chat = m.ChatKey('fixture', 'default', 'S')
    store.register([m.Watch(chat, '', ('S',), 0)])
    first, _ = acquire(acquisition)
    with conn:
        conn.execute("UPDATE tasks SET status='done' WHERE id=?", (blocked,))
    successor, _ = acquire(acquisition, first)
    assert successor.metrics['full'] is False
    fresh, _ = acquire(acquisition)
    key = m.CardKey('fixture', 'default', 'default', child)
    assert first.rows[key].get('blocked_upstream')
    assert successor.rows[key] == fresh.rows[key]
    assert successor.details[key] == fresh.details[key]


def test_terminal_capture_does_not_recertify_old_detail_after_reset(acquisition):
    r, cap, m, source, store, conn = acquisition
    tid = kanban_db.create_task(conn, title='Initially ready', session_id='S')
    chat = m.ChatKey('fixture', 'default', 'S')
    store.register([m.Watch(chat, '', ('S',), 0)])
    first, _ = acquire(acquisition)
    with conn:
        conn.execute("UPDATE tasks SET status='done' WHERE id=?", (tid,))
    store.reset(source)
    _, snapshot = acquire(acquisition, first)
    key = m.CardKey('fixture', 'default', 'default', tid)
    actual = m.SnapshotReaders(snapshot, chat).card_key(key)
    assert actual is None or actual['detail']['task']['status'] == 'done'
    assert key not in snapshot.parts[source].details
