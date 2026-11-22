"""Real transactional source cursors, including unindexed remote-parent evidence."""
import pytest

pytest.importorskip('hermes_cli.kanban_db')
from test_core_readers import api, board, readers_for, kanban_db


def test_remote_parent_status_and_removal_admit_child_without_hydrated_parent(api, board, monkeypatch):
    path, conn = board
    from hermes_cli.kanban_db_sync import apply_sync_schema_steps
    apply_sync_schema_steps(conn)
    from hermes_cli import kanban_activity_revision as revision
    revision.ensure_revision(conn, revision.LOCAL_TABLES)
    conn.commit()
    child = kanban_db.create_task(conn, title='Waiting child', session_id='chat-one')
    parent = 't_abcdef123456'
    conn.execute('INSERT INTO task_links(parent_id,child_id) VALUES (?,?)', (parent, child))
    conn.execute('INSERT INTO kanban_sync_remote_parents(parent_id,status,refreshed_at) VALUES (?,?,?)',
                 (parent, 'scheduled', 1000))
    conn.commit()
    r = readers_for(api, path, monkeypatch)
    held = {'lineage': {'chat-one'}, 'refs': {child}, 'bundle': {
        'tasks': [{'id': child, 'board': 'default'}], 'cards': {}}}
    try:
        _, cursor = r.board_changes(None)
        conn.execute('UPDATE kanban_sync_remote_parents SET status=?,refreshed_at=? WHERE parent_id=?',
                     ('running', 1001, parent))
        conn.commit()
        changes, cursor = r.board_changes(cursor)
        assert not changes.full
        assert {parent, child} <= changes.sources[(str(path), 'default')]
        assert changes.affects(held)
        changes, cursor = r.board_changes(cursor)
        assert not changes.affects(held), 'stable evidence is not a new source event'
        conn.execute('DELETE FROM kanban_sync_remote_parents WHERE parent_id=?', (parent,))
        conn.commit()
        changes, _ = r.board_changes(cursor)
        assert changes.affects(held), 'removed evidence must retract the old status'
    finally:
        r.release()
        conn.close()
