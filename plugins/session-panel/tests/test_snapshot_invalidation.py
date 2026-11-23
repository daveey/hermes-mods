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


def test_partial_capture_cannot_advance_cursor_over_stale_carried_rows(acquisition):
    r, cap, m, source, store, conn = acquisition
    a = kanban_db.create_task(conn, title='A', session_id='A')
    b = kanban_db.create_task(conn, title='B old', session_id='B')
    chats = [m.ChatKey('fixture', 'default', sid) for sid in ('A', 'B')]
    store.register([m.Watch(chat, '', (chat.session_id,), 0) for chat in chats])
    before, _ = acquire(acquisition)
    with conn:
        conn.execute('UPDATE tasks SET title=? WHERE id=?', ('B new', b))
    hint = cap.CaptureHints(store.version(source), store.owner_token(source), 1800000000.0)
    demands = store.demands(source)
    part = r.capture_local(source, before, {store.demand(source, chats[0]).id: demands[store.demand(source, chats[0]).id]}, hint)
    snapshot, _ = store.accept_local(part, hint.prior_version)
    assert store.pin(chats[1])[1].missing_scopes
    current, _ = acquire(acquisition, snapshot.parts[source])
    fresh, _ = acquire(acquisition)
    key = m.CardKey('fixture', 'default', 'default', b)
    assert current.rows[key] == fresh.rows[key]
    assert current.details[key] == fresh.details[key]


@pytest.mark.parametrize('membership', ['creator', 'subscriber'])
@pytest.mark.parametrize('intermediate', [False, True])
def test_partial_capture_revokes_empty_omitted_membership(acquisition, membership, intermediate):
    r, cap, m, source, store, conn = acquisition
    kanban_db.create_task(conn, title='A', session_id='A')
    target = kanban_db.create_task(conn, title='Unlinked', session_id='other')
    chats = [m.ChatKey('fixture', 'default', sid) for sid in ('A', 'B')]
    store.register([m.Watch(chat, '', (chat.session_id,), 0) for chat in chats])
    previous, _ = acquire(acquisition)
    empty_admission = store.pin(chats[1])[1]
    assert not empty_admission.missing_scopes
    demand = store.demand(source, chats[0])
    if intermediate:
        hint = cap.CaptureHints(store.version(source), store.owner_token(source), 1800000000.0)
        partial = r.capture_local(source, previous, {demand.id: demand}, hint)
        snapshot, _ = store.accept_local(partial, hint.prior_version)
        previous = snapshot.parts[source]
        assert store.current(empty_admission)  # unchanged observation may carry empty coverage
    with conn:
        if membership == 'creator':
            conn.execute('UPDATE tasks SET session_id=? WHERE id=?', ('B', target))
        else:
            conn.execute("INSERT INTO kanban_notify_subs(task_id,platform,chat_id,created_at) VALUES (?,'tui','B',1)", (target,))
    hint = cap.CaptureHints(store.version(source), store.owner_token(source), 1800000000.0)
    partial = r.capture_local(source, previous, {demand.id: demand}, hint)
    snapshot, admissions = store.accept_local(partial, hint.prior_version)
    assert chats[1] in {item.chat for item in admissions}
    assert store.pin(chats[1])[1].missing_scopes
    assert not store.current(empty_admission)
    successor, final = acquire(acquisition, snapshot.parts[source])
    assert not store.pin(chats[1])[1].missing_scopes
    assert [row['id'] for row in m.SnapshotReaders(final, chats[1]).linked(['B'])] == [target]


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
