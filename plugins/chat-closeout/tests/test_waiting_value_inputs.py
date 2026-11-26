"""Waiting's local key selects settlement values, not a task's aggregate write mark."""
import sqlite3

import pytest

from test_closeout import plugin
from test_waiting_inputs import world, poll, reset  # noqa: F401
from test_waiting_task_inputs import certified  # noqa: F401
from test_host_idle_skip import clock  # noqa: F401


@pytest.fixture(params=['installed', 'legacy', 'uncertified'])
def settlement_only(certified, monkeypatch, request):
    """Exercise narrow support and both conservative compatibility boundaries, without skips."""
    from hermes_cli import kanban_task_change as tc
    if request.param == 'legacy':
        monkeypatch.delattr(tc, 'settlement_marks', raising=False)
    elif request.param == 'uncertified':
        monkeypatch.setattr(tc, 'settlement_marks', lambda *a: None, raising=False)
    probe = getattr(tc, 'settlement_marks', None)
    with sqlite3.connect(certified['board']) as conn:
        conn.execute('BEGIN')
        _, cursor = tc.changed_since(conn, None)
        narrow = probe is not None and probe(conn, {'t_1', 't_2'}, cursor) is not None
        conn.rollback()
    return narrow


def test_selected_comment_leaves_key_and_actual_waiting_reader_idle(certified, clock, settlement_only):
    key = plugin.waiting_inputs()
    assert poll() == {'c1': ['t_1'], 'c2': ['t_2']}
    reset(certified['work'])
    with sqlite3.connect(certified['board']) as conn:
        conn.execute("INSERT INTO task_comments VALUES (1,'t_1','bookkeeping','agent',100)")
    assert (plugin.waiting_inputs() == key) is settlement_only
    assert poll() == {'c1': ['t_1'], 'c2': ['t_2']}
    assert (certified['work']['read_cards'] == 0) is settlement_only
    assert certified['work']['reconcile_done'] == []


T = {'t_1', 't_2'}


def bkey(board, tails=T):
    return plugin._waiting_board_key(board, set(tails))


def answer():
    plugin.waiting_inputs()  # the holder declares inputs before the source read, as in production
    return plugin.waiting_source()


def sql(board, *statements):
    with sqlite3.connect(board) as conn:
        for s in statements:
            conn.execute(*s) if isinstance(s, tuple) else conn.execute(s)


def test_nonselected_fields_leave_key_and_answer_stable(certified, clock, settlement_only):
    """Comment, event, title, run summary and last_failure_error are not settlement values."""
    board = certified['board']
    sql(board, 'ALTER TABLE tasks ADD COLUMN last_failure_error TEXT',
        'CREATE TABLE task_runs (id INTEGER PRIMARY KEY, task_id TEXT, summary TEXT)')
    from hermes_cli import kanban_activity_revision as rev
    with sqlite3.connect(board) as conn:  # recertify the final schema before any key is observed
        rev.drop_triggers(conn)
        rev.ensure_revision(conn, rev.LOCAL_TABLES)
    key, board_key = plugin.waiting_inputs(), bkey(board)
    assert poll() == {'c1': ['t_1'], 'c2': ['t_2']}
    reset(certified['work'])
    sql(board, "INSERT INTO task_comments VALUES (1,'t_1','c','agent',1)",
        "INSERT INTO task_events VALUES (1,'t_1','heartbeat','{}',1)",
        "INSERT INTO task_runs VALUES (1,'t_1','summary')",
        "UPDATE tasks SET title='renamed' WHERE id='t_1'",
        "UPDATE tasks SET last_failure_error='boom' WHERE id='t_2'")
    assert (bkey(board) == board_key) is settlement_only
    assert (plugin.waiting_inputs() == key) is settlement_only
    assert poll() == {'c1': ['t_1'], 'c2': ['t_2']}
    assert (certified['work']['read_cards'] == 0) is settlement_only
    assert certified['work']['reconcile_done'] == []


@pytest.mark.parametrize('change, settled', [
    ("UPDATE tasks SET status='running', completed_at=NULL WHERE id='t_2'", False),
    ("UPDATE tasks SET status='blocked', block_kind='human', completed_at=NULL WHERE id='t_2'", False),
    ("UPDATE tasks SET block_kind='human' WHERE id='t_2'", None),  # key must move; answer is the reader's
    ("UPDATE tasks SET completed_at=200.0 WHERE id='t_2'", None),
    ("DELETE FROM tasks WHERE id='t_2'", False),
])
def test_settlement_value_changes_move_key_and_answer(certified, clock, change, settled):
    board = certified['board']
    key, board_key = plugin.waiting_inputs(), bkey(board)
    assert plugin.waiting_source() == {'c1': ['t_1'], 'c2': ['t_2']}
    sql(board, change)
    assert bkey(board) != board_key
    assert plugin.waiting_inputs() != key
    if settled is not None:
        assert ('c2' in answer()) is settled


def test_reopen_and_reclose_restores_value_and_answer(certified, clock):
    board = certified['board']
    first = bkey(board)
    sql(board, "UPDATE tasks SET status='running', completed_at=NULL WHERE id='t_2'")
    assert bkey(board) != first
    assert 'c2' not in answer()
    sql(board, "UPDATE tasks SET status='done', completed_at=100.0 WHERE id='t_2'")
    assert bkey(board) != first  # equal values, newer generation: never certify an intermediate answer
    assert answer() == {'c1': ['t_1'], 'c2': ['t_2']}


def test_selected_absence_insert_and_delete_reinsert(certified, clock):
    board = certified['board']
    missing = bkey(board, {'t_1', 'later'})
    sql(board, "INSERT INTO tasks VALUES ('later','x','done',NULL,1.0)")
    assert bkey(board, {'t_1', 'later'}) != missing
    present = bkey(board, {'t_1', 'later'})
    sql(board, "DELETE FROM tasks WHERE id='later'")
    assert bkey(board, {'t_1', 'later'}) != missing  # retained delete generation
    sql(board, "INSERT INTO tasks VALUES ('later','y','done',NULL,1.0)")  # title differs, values identical
    assert bkey(board, {'t_1', 'later'}) != present
    baseline = bkey(board)
    assert 'c2' in answer()
    sql(board, "DELETE FROM tasks WHERE id='t_2'")
    assert 'c2' not in answer()
    sql(board, "INSERT INTO tasks VALUES ('t_2','new title','done',NULL,100.0)")
    assert bkey(board) != baseline
    assert 'c2' in answer()


def test_same_tail_on_second_board_collision(certified, clock, monkeypatch, tmp_path):
    from hermes_cli import kanban_activity_revision as rev
    first = certified['board']
    second = tmp_path / 'second.db'
    with sqlite3.connect(second) as conn:
        conn.execute('CREATE TABLE tasks (id TEXT, title TEXT, status TEXT, block_kind TEXT, completed_at REAL)')
        conn.commit()
        rev.ensure_revision(conn, rev.LOCAL_TABLES)
    monkeypatch.setattr(plugin, 'board_paths', lambda: [first, second])
    key = plugin.waiting_inputs()
    assert 'c2' in plugin.waiting_source()
    sql(second, "INSERT INTO tasks VALUES ('t_2','copy','running',NULL,NULL)")  # same tail appears elsewhere
    appeared = plugin.waiting_inputs()
    assert appeared != key
    assert 'c2' not in plugin.waiting_source()
    sql(second, "UPDATE tasks SET status='done', completed_at=5.0 WHERE id='t_2'")
    assert plugin.waiting_inputs() != appeared
    sql(second, "DELETE FROM tasks WHERE id='t_2'")
    assert plugin.waiting_inputs() != key  # returning values do not erase intervening generations


def test_cold_and_warm_keys_equal_and_selected_set_changes(certified, clock, settlement_only):
    board = certified['board']
    warm_small, warm_big = bkey(board, {'t_1'}), bkey(board, T)
    assert warm_small != warm_big  # growth is visible, then shrink returns exactly
    sql(board, "INSERT INTO task_comments VALUES (1,'t_1','c','agent',1)")
    assert (bkey(board, {'t_1'}) == warm_small) is settlement_only
    current_small, current_big = bkey(board, {'t_1'}), bkey(board, T)
    plugin._wi_reset()
    assert bkey(board, T) == current_big  # cold reader: no cursor/history in either mode
    assert bkey(board, {'t_1'}) == current_small


def test_optional_columns_null_float_text_blob_are_distinct_and_safe(certified, clock):
    board = certified['board']
    keys = []
    for value in [None, 1.5, float('inf'), 'abc', 'x', b'\x00\xff', 7]:
        sql(board, ("UPDATE tasks SET completed_at=? WHERE id='t_2'", (value,)))
        keys.append(bkey(board))
    assert len(set(keys)) == len(keys)
    for value in [None, 'human', b'\x01', 3]:
        sql(board, ("UPDATE tasks SET block_kind=? WHERE id='t_2'", (value,)))
        bkey(board)  # must not raise TypeError on readable scalars
    sql(board, ("UPDATE tasks SET block_kind=NULL, completed_at=NULL WHERE id='t_2'",))
    null_all = bkey(board)
    sql(board, ("UPDATE tasks SET block_kind='' WHERE id='t_2'",))
    assert bkey(board) != null_all  # NULL is not empty text


def test_absent_optional_columns_key_as_null(certified, tmp_path):
    from hermes_cli import kanban_activity_revision as rev
    def make(name, columns, rows):
        path = tmp_path / name
        with sqlite3.connect(path) as conn:
            conn.execute(f'CREATE TABLE tasks ({columns})')
            conn.executemany(f'INSERT INTO tasks VALUES ({",".join("?" * len(rows[0]))})', rows)
            conn.commit()
            rev.ensure_revision(conn, rev.LOCAL_TABLES)
        return path
    sparse = make('sparse.db', 'id TEXT, status TEXT', [('t_1', 'done')])
    full = make('full.db', 'id TEXT, status TEXT, block_kind TEXT, completed_at REAL', [('t_1', 'done', None, None)])
    assert bkey(sparse, {'t_1'}) != bkey(full, {'t_1'})  # schema identity differs, but neither raises


@pytest.mark.parametrize('columns', ['id TEXT, title TEXT', 'status TEXT, title TEXT'])
def test_missing_required_columns_are_unportable(certified, tmp_path, columns):
    from hermes_cli import kanban_activity_revision as rev
    path = tmp_path / 'bad.db'
    with sqlite3.connect(path) as conn:
        conn.execute(f'CREATE TABLE tasks ({columns})')
        conn.commit()
        rev.ensure_revision(conn, rev.LOCAL_TABLES)
    with pytest.raises(plugin.wi.Unportable):
        bkey(path, {'t_1'})


def test_large_tail_set_uses_fixed_task_value_statements(certified, monkeypatch):
    import contextlib
    tails = {f'missing-{i}' for i in range(1300)} | T
    statements = []
    original = plugin._waiting_board_read
    @contextlib.contextmanager
    def traced(path):
        with original(path) as conn:
            conn.set_trace_callback(statements.append)
            try:
                yield conn
            finally:
                conn.set_trace_callback(None)
    monkeypatch.setattr(plugin, '_waiting_board_read', traced)
    bkey(certified['board'], tails)
    assert len([s for s in statements if 'FROM tasks WHERE id IN' in s]) == 1
    assert not [s for s in statements if "id = 'missing" in s or 'id = ?' in s and 'tasks' in s]
    assert len(statements) <= 20  # value batch + independently certified generation batch; no per-tail SQL


def test_production_value_query_uses_id_index(certified, monkeypatch):
    import contextlib
    from hermes_cli import kanban_activity_revision as rev
    board = certified['board']
    with sqlite3.connect(board) as conn:
        conn.execute('CREATE UNIQUE INDEX fixture_task_id ON tasks(id)')
        conn.commit()
        rev.ensure_revision(conn, rev.LOCAL_TABLES)
    statements = []
    original = plugin._waiting_board_read
    @contextlib.contextmanager
    def traced(path):
        with original(path) as conn:
            conn.set_trace_callback(statements.append)
            try:
                yield conn
            finally:
                conn.set_trace_callback(None)
    monkeypatch.setattr(plugin, '_waiting_board_read', traced)
    bkey(board, T | {f'missing-{i}' for i in range(1300)})
    queries = [s for s in statements if 'FROM tasks WHERE id IN' in s]
    assert len(queries) == 1
    with sqlite3.connect(board) as conn:
        plan = [r[3].upper() for r in conn.execute('EXPLAIN QUERY PLAN ' + queries[0])]
    assert any('SEARCH TASKS USING INDEX FIXTURE_TASK_ID' in p for p in plan), plan
    assert not any('SCAN TASKS' in p for p in plan), plan
