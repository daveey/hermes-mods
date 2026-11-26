"""Describe memo review fixes (cc/describe-memo @ bcafbe17): replica answer and identity read before describe,
swallowed evidence errors not stored, unwatched-table predicates re-run, any id format, unsafe parses, review config
read before describe, and the checks each must fail without. [daveey]"""
import contextlib
import os
import sqlite3

from test_core_readers import api, board, kanban_db  # noqa: F401  (fixtures)
from test_reader_batch import graph  # noqa: F401  (fixture)
from test_describe_memo import REAL_DESCRIBE, _enroll, _mid_pass, calls, fresh, kas, run_pass  # noqa: F401


def test_any_id_format_is_a_dependency(api):
    """Non-t_ ids exist on the hub (archived cards, hwp-task-* replica cards)."""
    assert api._describe_statement("SELECT * FROM tasks WHERE id='hwp-task-7'") == ('keyed', ('hwp-task-7',))
    assert api._describe_statement("SELECT parent_id FROM task_links WHERE child_id='a''b' ORDER BY parent_id") == \
        ('keyed', ("a'b",))


def test_unparseable_or_hidden_tables_are_never_keyed(api):
    assert api._describe_statement('SELECT * FROM "tasks" WHERE id=\'t_1\'')[0] == 'predicate'
    assert api._describe_statement("SELECT * FROM tasks, task_links WHERE id='t_1'")[0] == 'unsafe'
    assert api._describe_statement("SELECT * FROM tasks t, task_links l WHERE t.id='t_1'")[0] == 'unsafe'
    assert api._describe_statement("SELECT * FROM main.tasks WHERE id='t_1'")[0] == 'unsafe'
    assert api._describe_statement('SELECT * FROM "scratch" WHERE x=1')[0] == 'rerun'
    assert api._describe_statement('DELETE FROM tasks')[0] == 'unsafe'


def test_a_keyed_statement_on_a_non_t_parent_tracks_it(graph, board, calls):
    r, _, _, _ = graph
    path, c = board
    child = kanban_db.create_task(c, title='Child of hwp', assignee='default')
    cols = [row[1] for row in c.execute('PRAGMA table_info(tasks)')]
    c.execute(f"INSERT INTO tasks ({','.join(cols)}) SELECT "
              + ','.join("'hwp-task-7'" if col == 'id' else col for col in cols) + ' FROM tasks WHERE id=?', (child,))
    c.execute("INSERT INTO task_links (parent_id, child_id) VALUES ('hwp-task-7', ?)", (child,))
    other = kanban_db.create_task(c, title='Read first', assignee='default')
    c.commit()
    run_pass(r, child)

    def write():
        c.execute("UPDATE tasks SET status='blocked' WHERE id='hwp-task-7'")
        c.commit()
    after = _mid_pass(r, other, write, child)
    assert calls.count(child) == 2
    assert after == fresh(path, child)


def test_a_mid_pass_schema_change_misses(graph, board, calls):
    r, local, _, _ = graph
    _, c = board
    other = kanban_db.create_task(c, title='Read first', assignee='default')
    c.commit()
    run_pass(r, local)

    def alter():
        c.execute('ALTER TABLE task_wake_checks ADD COLUMN probe_note TEXT')
        c.commit()
    _mid_pass(r, other, alter, local)
    assert calls.count(local) == 2


def test_an_inconsistent_revision_counter_misses(graph, board, calls):
    from hermes_cli import kanban_activity_revision as rev
    r, local, _, _ = graph
    _, c = board
    other = kanban_db.create_task(c, title='Read first', assignee='default')
    c.commit()
    run_pass(r, local)

    def bump():  # a bump no change row explains: the counter no longer vouches for "nothing changed"
        c.execute(f'UPDATE {rev.REVISION_TABLE} SET revision = revision + 5 WHERE id = 1')
        c.commit()
    _mid_pass(r, other, bump, local)
    assert calls.count(local) == 2


def test_a_hit_is_a_private_copy(graph, board):
    r, local, _, _ = graph
    path, _ = board
    run_pass(r, local)
    memo = r._describe_memo
    (key, entry), = [(k, e) for k, e in memo.entries.items() if k[1] == local]
    conn = r.conn(path)
    conn.execute('BEGIN')
    try:
        first = memo.get(r, key[0], entry['ino'], local, conn)
        first['action'] = 'mutated by a reader'
        second = memo.get(r, key[0], entry['ino'], local, conn)
    finally:
        conn.rollback()
    assert second is not None and second['action'] != 'mutated by a reader'


def _foreign_child(c, parent):
    child = kanban_db.create_task(c, title='Waits on a foreign parent', assignee='default')
    c.execute('INSERT INTO task_links (parent_id, child_id) VALUES (?, ?)', (parent, child))
    c.commit()
    return child


def test_sync_state_select_star_narrowing(graph, board, calls):
    """sync_state()'s `SELECT *` runs for a missing parent: its clocks never miss, mode/gateway_id/board_id do."""
    r, _, remote, _ = graph
    path, c = board
    _enroll(c)
    child = _foreign_child(c, remote)
    run_pass(r, child)
    (entry,) = [e for k, e in r._describe_memo.entries.items() if k[1] == child]
    assert any(sql == 'SELECT mode, gateway_id, board_id FROM kanban_sync_state LIMIT 1' for sql, _ in entry['rerun'])
    c.execute('UPDATE kanban_sync_state SET pulled_at=12345, cursor=cursor+1')
    c.commit()
    run_pass(r, child)
    assert calls.count(child) == 1
    c.execute("UPDATE kanban_sync_state SET gateway_id='another-gateway'")
    c.commit()
    assert run_pass(r, child) == fresh(path, child)
    assert calls.count(child) == 2


def test_a_replica_that_appears_later_misses(graph, board, calls, tmp_path, monkeypatch):
    from hermes_cli import kanban_sync_client as ksc
    r, _, remote, _ = graph
    path, c = board
    _enroll(c)
    late = tmp_path / 'late-replica.db'
    monkeypatch.setattr(ksc, 'shadow_path', lambda slug: late)  # no replica yet (before the first pull)
    child = _foreign_child(c, remote)
    before = run_pass(r, child)
    src = sqlite3.connect(tmp_path / 'replica.db')
    dest = sqlite3.connect(late)
    src.backup(dest)
    src.close()
    dest.execute("UPDATE tasks SET status='done' WHERE id=?", (remote,))
    dest.commit()
    dest.close()
    after = run_pass(r, child)
    assert calls.count(child) == 2
    assert after == fresh(path, child) and after != before


def test_a_swallowed_replica_error_is_not_stored(graph, board, calls, monkeypatch):
    from hermes_cli import kanban_foreign_card as foreign
    r, _, remote, _ = graph
    path, c = board
    _enroll(c)
    child = _foreign_child(c, remote)
    real = foreign._ro
    failures = [1]

    def flaky(p):
        if failures:
            failures.pop()
            raise sqlite3.OperationalError('transient replica open failure')
        return real(p)
    monkeypatch.setattr(foreign, '_ro', flaky)
    run_pass(r, child)
    assert not failures  # the failure really happened inside describe
    after = run_pass(r, child)
    assert calls.count(child) == 2
    assert after == fresh(path, child)


def test_a_pull_racing_describe_misses(graph, board, tmp_path, monkeypatch):
    """The replica moves after describe read it: the entry carries the replica revision from BEFORE describe."""
    r, _, remote, _ = graph
    path, c = board
    _enroll(c)
    child = _foreign_child(c, remote)
    seen = []

    def describe_then_pull(conn, tid, **kw):
        seen.append(tid)
        out = REAL_DESCRIBE(conn, tid, **kw)
        if len(seen) == 1:
            rc = sqlite3.connect(tmp_path / 'replica.db')
            rc.execute("UPDATE tasks SET status='done' WHERE id=?", (remote,))
            rc.commit()
            rc.close()
        return out
    monkeypatch.setattr(kas, 'describe', describe_then_pull)
    run_pass(r, child)
    after = run_pass(r, child)
    assert seen.count(child) == 2
    assert after == fresh(path, child)


def test_a_rebuilt_replica_misses(graph, board, calls, tmp_path):
    """Core rebuilds a replica by unlink + recreate: same schema cookie, a counter at or above the old one."""
    from hermes_cli import kanban_activity_revision as rev
    r, _, remote, _ = graph
    path, c = board
    _enroll(c)
    child = _foreign_child(c, remote)
    run_pass(r, child)
    replica = tmp_path / 'replica.db'
    old = sqlite3.connect(replica)
    top = old.execute(f'SELECT revision FROM {rev.REVISION_TABLE} WHERE id=1').fetchone()[0]
    cookie = old.execute('PRAGMA schema_version').fetchone()[0]
    rebuilt = tmp_path / 'rebuilt.db'
    dest = sqlite3.connect(rebuilt)
    old.backup(dest)
    old.close()
    # The rebuilt file's counter and change rows look exactly like the old file's: only its identity differs.
    was = dest.execute(f'SELECT rev FROM {rev.CHANGE_TABLE} WHERE task_id=?', (remote,)).fetchone()
    dest.execute("UPDATE tasks SET status='done' WHERE id=?", (remote,))
    if was is None:
        dest.execute(f'DELETE FROM {rev.CHANGE_TABLE} WHERE task_id=?', (remote,))
    else:
        dest.execute(f'UPDATE {rev.CHANGE_TABLE} SET rev=? WHERE task_id=?', (was[0], remote))
    dest.execute(f'UPDATE {rev.REVISION_TABLE} SET revision=? WHERE id=1', (top,))
    assert dest.execute(f'SELECT MAX(rev) FROM {rev.CHANGE_TABLE}').fetchone()[0] == top
    dest.commit()
    dest.execute('PRAGMA journal_mode=DELETE')
    dest.execute(f'PRAGMA schema_version={cookie}')
    dest.close()
    os.remove(replica)
    for suffix in ('-wal', '-shm'):
        with contextlib.suppress(FileNotFoundError):
            os.remove(f'{replica}{suffix}')
    os.replace(rebuilt, replica)
    after = run_pass(r, child)
    assert calls.count(child) == 2
    assert after == fresh(path, child)


def test_a_predicate_over_an_unwatched_table_reruns(graph, board, monkeypatch):
    """Like quarantine's WITH RECURSIVE over containment_sync_quarantine_tasks + task_links: no trigger marks it."""
    r, local, _, _ = graph
    _, c = board
    c.execute('CREATE TABLE scratch_quarantine (task_id TEXT)')
    c.commit()

    def with_quarantine(conn, tid, **kw):
        row = dict(REAL_DESCRIBE(conn, tid, **kw))
        row['quarantined'] = conn.execute(
            'WITH RECURSIVE up(id) AS (SELECT task_id FROM scratch_quarantine UNION '
            'SELECT child_id FROM task_links JOIN up ON task_links.parent_id = up.id) SELECT count(*) FROM up '
            f"WHERE id='{tid}'").fetchone()[0]
        return row
    monkeypatch.setattr(kas, 'describe', with_quarantine)
    assert run_pass(r, local)['quarantined'] == 0
    c.execute('INSERT INTO scratch_quarantine VALUES (?)', (local,))
    c.commit()
    assert run_pass(r, local)['quarantined'] == 1


def test_review_config_is_read_before_describe(graph, board, monkeypatch):
    r, local, _, _ = graph
    _, c = board
    c.execute("UPDATE tasks SET status='review' WHERE id=?", (local,))
    c.commit()
    setting = {'value': True}
    monkeypatch.setattr(kas, '_review_dispatch', lambda: setting['value'])
    seen = []

    def describe_then_flip(conn, tid, **kw):
        seen.append(tid)
        out = REAL_DESCRIBE(conn, tid, **kw)
        setting['value'] = False  # the config changed right after describe read it
        return out
    monkeypatch.setattr(kas, 'describe', describe_then_flip)
    run_pass(r, local)
    run_pass(r, local)
    assert seen.count(local) == 2
