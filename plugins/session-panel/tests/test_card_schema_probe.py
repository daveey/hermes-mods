"""A card record's `ready_since` probe reads the tasks columns in one result row, not one row per column. [daveey]

`_detail` runs for every held card on every feed pass. `PRAGMA table_info(tasks)` hands Python one row per column
(59 on the hub board) and CPython releases the GIL on each, so the probe alone was ~36k handoffs a hub pass.
"""
import builtins
import sqlite3

import pytest

from test_core_readers import api, board, readers_for  # noqa: F401  (fixtures)

kanban_db = pytest.importorskip('hermes_cli.kanban_db')
pytest.importorskip('hermes_cli.kanban_table_columns')


def _traced(r, path):
    c = r.conn(path)
    seen = []
    c.set_trace_callback(seen.append)
    return seen


def _ready_card(conn):
    tid = kanban_db.create_task(conn, title='Ready work', assignee='default')
    conn.execute("UPDATE tasks SET status='ready' WHERE id=?", (tid,))
    if 'ready_since' in {r[1] for r in conn.execute('PRAGMA table_info(tasks)')}:
        conn.execute('UPDATE tasks SET ready_since=1234567 WHERE id=?', (tid,))
    conn.commit()
    return tid


def test_card_detail_never_steps_table_info_rows(api, board, monkeypatch):
    path, conn = board
    tid = _ready_card(conn)
    r = readers_for(api, path, monkeypatch)
    seen = _traced(r, path)
    rec = r.card(tid, 'default')
    assert rec is not None
    assert not [s for s in seen if 'table_info(tasks)' in s.replace(' ', '').lower()
                and 'json_group_array' not in s.lower()], seen


def test_ready_since_matches_the_pragma_probe(api, board, monkeypatch):
    path, conn = board
    tid = _ready_card(conn)
    cols = {r[1] for r in conn.execute('PRAGMA table_info(tasks)')}
    expected = conn.execute('SELECT ready_since FROM tasks WHERE id=?', (tid,)).fetchone()[0] \
        if 'ready_since' in cols else None
    r = readers_for(api, path, monkeypatch)
    assert r.card(tid, 'default')['detail']['task']['ready_since'] == expected
    assert api._task_columns(r.conn(path)) == frozenset(cols)


def _hide_panel_schema_helper(api, monkeypatch):
    """Simulate the panel's optional import, not a broken mixed-version core.

    Current actionability also imports table_columns. Poisoning sys.modules makes
    that independent real producer fail and changes the complete card payload.
    Keep it running; only the panel's compatibility lookup should see ImportError.
    """
    original = builtins.__import__
    attempts = []

    def importing(name, globals=None, locals=None, fromlist=(), level=0):
        if name == 'hermes_cli.kanban_table_columns' and (globals or {}).get('__name__') == api.__name__:
            attempts.append(name)
            raise ImportError('older panel core has no table-columns helper')
        return original(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, '__import__', importing)
    return attempts, original


def test_older_core_without_the_helper_keeps_the_pragma(api, board, monkeypatch):
    path, conn = board
    tid = _ready_card(conn)
    r = readers_for(api, path, monkeypatch)
    with_helper = r.card(tid, 'default')
    attempts, _ = _hide_panel_schema_helper(api, monkeypatch)
    monkeypatch.setattr(api, '_TABLE_COLUMNS', None)  # a fresh process on that older core
    r2 = readers_for(api, path, monkeypatch)
    seen = _traced(r2, path)
    assert r2.card(tid, 'default') == with_helper
    assert attempts == ['hermes_cli.kanban_table_columns']
    assert any('PRAGMA table_info(tasks)' in s for s in seen)


def test_a_missing_helper_is_looked_up_once_per_process(api, board, monkeypatch):
    path, conn = board
    tid = _ready_card(conn)
    monkeypatch.setattr(api, '_TABLE_COLUMNS', None)
    attempts, original = _hide_panel_schema_helper(api, monkeypatch)
    r = readers_for(api, path, monkeypatch)
    expected = r.card(tid, 'default')
    assert api._TABLE_COLUMNS is False
    assert attempts == ['hermes_cli.kanban_table_columns']
    # The helper "appears" later in the process: the cached answer stands, the PRAGMA fallback keeps working.
    monkeypatch.setattr(builtins, '__import__', original)
    seen = _traced(r, path)
    assert r.card(tid, 'default') == expected
    assert any('PRAGMA table_info(tasks)' in s for s in seen)
    assert api._TABLE_COLUMNS is False
