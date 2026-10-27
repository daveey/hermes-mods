"""Read native producers, without promoting or running wake commands."""
from contextlib import closing
import sqlite3
import time
import pytest
from test_goal_ownership import owner, TID
from test_goal_ownership_liveness import ownership, certify

PARENT = 't_abcdef12'


def link_native(owner):
    from hermes_cli import kanban_db as kb
    with sqlite3.connect(owner[0]) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("UPDATE tasks SET status='ready', current_run_id=NULL")
        conn.execute("INSERT INTO tasks(id,title,status,created_at) VALUES (?, 'Prerequisite','ready',?)", (PARENT, int(time.time())))
        conn.commit()
        assert kb.link_tasks(conn, PARENT, TID)
        row = conn.execute('SELECT * FROM tasks WHERE id=?', (TID,)).fetchone()
        assert row['status'] == 'todo' and row['block_kind'] is None


def snapshot(owner):
    with closing(ownership._ro_connect(owner[0])) as conn:
        conn.execute('BEGIN')
        task = conn.execute('SELECT * FROM tasks WHERE id=?', (TID,)).fetchone()
        return ownership.waiting_readback(conn, task)


def test_native_link_is_dependency_even_without_block_kind(owner):
    link_native(owner)
    with pytest.raises(ValueError, match='owner_dispatcher_unverified'):
        certify(owner)


def test_native_dependency_readback_and_release(owner):
    from hermes_cli import kanban_db as kb
    link_native(owner)
    before = owner[0].read_bytes()
    assert snapshot(owner) == {'kind': 'dependency', 'parents': ((PARENT, 'ready'),)}
    assert owner[0].read_bytes() == before
    with sqlite3.connect(owner[0]) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("UPDATE tasks SET status='done' WHERE id=?", (PARENT,))
        conn.commit()
        assert kb.recompute_ready(conn) == 1
    with pytest.raises(ValueError, match='owner_wait_state_unverified'):
        snapshot(owner)


@pytest.mark.parametrize('producer', ['continue', 'block'])
def test_native_scheduled_producer_readback_does_not_run_wake(owner, producer):
    from hermes_cli import kanban_db as kb
    from hermes_cli.kanban_continuation import continue_task, wake_due_continuations
    marker = owner[0].parent / 'must-not-run'
    command = 'touch ' + str(marker)
    with sqlite3.connect(owner[0]) as conn:
        conn.row_factory = sqlite3.Row
        if producer == 'continue':
            result = continue_task(conn, TID, checkpoint='Wait for prerequisite', resume_after_seconds=600, wake_check=command)
            assert result['ok']
        else:
            assert kb.block_task(conn, TID, reason='Wait for prerequisite', kind='time', wake_check=command)
    before = owner[0].read_bytes()
    readback = snapshot(owner)
    assert readback['kind'] == 'scheduled' and readback['wake_at'] > time.time()
    assert owner[0].read_bytes() == before and not marker.exists()
    with pytest.raises(ValueError, match='owner_dispatcher_unverified'):
        certify(owner)
    assert not marker.exists()
    with sqlite3.connect(owner[0]) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute('DELETE FROM task_wake_checks')
        conn.commit()
        assert wake_due_continuations(conn, now=int(readback['wake_at'])+1) == [TID]
        assert conn.execute('SELECT status FROM tasks WHERE id=?', (TID,)).fetchone()[0] == 'ready'


@pytest.mark.parametrize('corruption', ['missing', 'cycle', 'too_many'])
def test_dependency_readback_rejects_corrupt_graph(owner, corruption):
    link_native(owner)
    with sqlite3.connect(owner[0]) as conn:
        if corruption == 'missing':
            conn.execute('DELETE FROM tasks WHERE id=?', (PARENT,))
        elif corruption == 'cycle':
            conn.execute('INSERT INTO task_links(parent_id,child_id) VALUES (?,?)', (TID, PARENT))
        else:
            for i in range(65):
                tid = f't_{i:08x}'
                conn.execute("INSERT INTO tasks(id,title,status,created_at) VALUES (?, 'Prerequisite','ready',1)", (tid,))
                conn.execute('INSERT INTO task_links(parent_id,child_id) VALUES (?,?)', (tid, TID))
    with pytest.raises(ValueError, match='owner_dependency_unverified'):
        snapshot(owner)


def test_shared_ancestor_is_not_a_cycle(owner):
    from hermes_cli import kanban_db as kb
    link_native(owner)
    sibling, ancestor = 't_abcdef13', 't_abcdef14'
    with sqlite3.connect(owner[0]) as conn:
        conn.row_factory = sqlite3.Row
        for tid in (sibling, ancestor):
            conn.execute("INSERT INTO tasks(id,title,status,created_at) VALUES (?, 'Prerequisite','ready',1)", (tid,))
        conn.commit()
        kb.link_tasks(conn, sibling, TID)
        kb.link_tasks(conn, ancestor, PARENT)
        kb.link_tasks(conn, ancestor, sibling)
    assert snapshot(owner)['parents'] == ((PARENT, 'todo'), (sibling, 'todo'))
    with pytest.raises(ValueError, match='owner_dispatcher_unverified'):
        certify(owner)


def test_terminal_parents_alone_do_not_certify_pending_child(owner):
    link_native(owner)
    with sqlite3.connect(owner[0]) as conn:
        conn.execute("UPDATE tasks SET status='archived' WHERE id=?", (PARENT,))
    with pytest.raises(ValueError, match='owner_dependency_unverified'):
        snapshot(owner)


def test_readback_rejects_dangling_transitive_parent(owner):
    link_native(owner)
    with sqlite3.connect(owner[0]) as conn:
        conn.execute('INSERT INTO task_links(parent_id,child_id) VALUES (?,?)', ('t_abcdef13', PARENT))
    with pytest.raises(ValueError, match='owner_dependency_unverified'):
        snapshot(owner)
