"""Portable watched-task declarations: irrelevant writes skip, lost notifications do not."""
import json
import sqlite3

import pytest
from test_closeout import plugin
from test_waiting_inputs import world, ksa, poll, reset
from test_host_idle_skip import clock


@pytest.fixture
def certified(world, clock):
    import time
    clock.wall = time.time()
    from hermes_cli import kanban_activity_revision as rev
    with sqlite3.connect(world['board']) as conn:
        conn.execute('CREATE TABLE task_comments (id INTEGER PRIMARY KEY, task_id TEXT, body TEXT, author TEXT, created_at REAL)')
        conn.execute('CREATE TABLE task_events (id INTEGER PRIMARY KEY, task_id TEXT, kind TEXT, payload TEXT, created_at REAL)')
        rev.ensure_revision(conn, rev.LOCAL_TABLES)
        conn.execute("INSERT INTO tasks VALUES ('other-task', 'elsewhere', 'running', NULL, NULL)")
    return world


@pytest.mark.parametrize('kind', ['heartbeat', 'comment', 'status'])
def test_unrelated_writes_leave_declared_inputs_unchanged(certified, clock, kind):
    key = plugin.waiting_inputs()
    assert poll() == {'c1': ['t_1'], 'c2': ['t_2']}
    reset(certified['work'])
    with sqlite3.connect(certified['board']) as conn:
        if kind == 'status':
            conn.execute("UPDATE tasks SET status='done' WHERE id='other-task'")
        elif kind == 'comment':
            conn.execute("INSERT INTO task_comments VALUES (1, 'other-task', 'unrelated', 'agent', 100)")
        else:
            conn.execute("INSERT INTO task_events VALUES (1, 'other-task', 'heartbeat', '{}', 100)")
    assert plugin.waiting_inputs() == key
    assert poll() == {'c1': ['t_1'], 'c2': ['t_2']}
    assert certified['work'] == {'reconcile_done': [], 'read_cards': 0}


def test_relevant_change_moves_key_and_answer_without_notification(certified, clock):
    key = plugin.waiting_inputs()
    assert plugin.waiting_source() == {'c1': ['t_1'], 'c2': ['t_2']}
    with sqlite3.connect(certified['board']) as conn:
        conn.execute("UPDATE tasks SET status='running', completed_at=NULL WHERE id='t_2'")
    assert plugin.waiting_inputs() != key
    assert plugin.waiting_source() == {'c1': ['t_1']}


def test_cold_key_is_identical_after_unrelated_write(certified, clock):
    key = plugin.waiting_inputs()
    with sqlite3.connect(certified['board']) as conn:
        conn.execute("UPDATE tasks SET title='moved' WHERE id='other-task'")
    plugin._wi_reset()  # no cursor/history retained by a second observer
    assert json.loads(json.dumps(plugin.waiting_inputs())) == json.loads(json.dumps(key))


@pytest.mark.parametrize('failure', ['missing', 'coverage', 'error'])
def test_unknown_evidence_forces_fresh_even_inside_answer_cache(certified, clock, monkeypatch, failure):
    from hermes_cli import kanban_task_change as tc
    plugin.waiting_inputs()
    assert plugin.waiting_source() == {'c1': ['t_1'], 'c2': ['t_2']}
    with sqlite3.connect(certified['board']) as conn:
        conn.execute("UPDATE tasks SET status='running' WHERE id='t_2'")
        if failure == 'missing':
            conn.execute('DROP TABLE kanban_task_change')
        elif failure == 'coverage':
            conn.execute('DROP TRIGGER kanban_activity_rev_tasks_update')
    if failure == 'error':
        def broken(*args, **kwargs):
            raise sqlite3.OperationalError('injected read failure')
        monkeypatch.setattr(tc, 'changed_since', broken)
    with pytest.raises((plugin.wi.Unportable, LookupError, sqlite3.Error)):
        plugin.waiting_inputs()
    assert plugin.waiting_source() == {'c1': ['t_1']}


def test_shadow_task_inputs_ignore_other_tasks_but_not_batch(certified, clock, monkeypatch):
    from hermes_cli import kanban_activity_revision as rev
    root = certified['board'].parent
    monkeypatch.setattr(plugin, '_kanban_root', lambda: root)
    path = root / 'kanban' / 'sync-shadow' / 'example.db'
    path.parent.mkdir(parents=True)
    with sqlite3.connect(path) as conn:
        conn.execute('CREATE TABLE tasks (id TEXT PRIMARY KEY, title TEXT)')
        rev.ensure_revision(conn, rev.SHADOW_TABLES)
        conn.executemany('INSERT INTO tasks VALUES (?,?)', [('t_1', 'batch'), ('other', 'elsewhere')])
        conn.execute('CREATE TABLE kanban_sync_state (board_id TEXT, pulled_at REAL)')
    fresh = [True]
    monkeypatch.setattr(plugin, '_read_shadow', lambda board, ids: (fresh[0], plugin.core.shadow_rows(path, ids)[1]))
    key = plugin.waiting_inputs()
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE tasks SET title='unrelated' WHERE id='other'")
    assert plugin.waiting_inputs() == key
    plugin._wi_reset()
    assert plugin.waiting_inputs() == key
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE tasks SET title='relevant' WHERE id='t_1'")
    changed = plugin.waiting_inputs()
    assert changed != key
    fresh[0] = False
    assert plugin.waiting_inputs() != changed


@pytest.mark.parametrize('fresh', [False, True])
@pytest.mark.parametrize('mutation', ['update', 'insert', 'delete'])
def test_legacy_shadow_without_task_cursor_is_content_evidence(certified, clock, monkeypatch, fresh, mutation):
    """A dormant legacy shadow must not poison every declared-input probe (live fallback)."""
    root = certified['board'].parent
    monkeypatch.setattr(plugin, '_kanban_root', lambda: root)
    path = root / 'kanban' / 'sync-shadow' / 'example.db'
    path.parent.mkdir(parents=True)
    with sqlite3.connect(path) as conn:
        conn.execute('CREATE TABLE tasks (id TEXT PRIMARY KEY, title TEXT, status TEXT)')
        conn.execute('CREATE TABLE kanban_sync_state (board_id TEXT, pulled_at REAL)')
        conn.execute('INSERT INTO kanban_sync_state VALUES (?,?)', ('example', clock.wall if fresh else 0))
        conn.execute("INSERT INTO tasks VALUES ('other', 'unrelated', 'running')")
        if mutation != 'insert':
            conn.execute("INSERT INTO tasks VALUES ('t_1', 'watched', 'running')")
    # No revision or task-change table, just as an older shadow need not have task cursor coverage.
    key = plugin.waiting_inputs()
    assert plugin.waiting_inputs() == key
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE tasks SET title='churn' WHERE id='other'")
    assert plugin.waiting_inputs() == key
    with sqlite3.connect(path) as conn:
        if mutation == 'update':
            conn.execute("UPDATE tasks SET status='done' WHERE id='t_1'")
        elif mutation == 'delete':
            conn.execute("DELETE FROM tasks WHERE id='t_1'")
        else:
            conn.execute("INSERT INTO tasks VALUES ('t_1', 'watched', 'running')")
    assert plugin.waiting_inputs() != key  # lost notification: next read still sees the change


@pytest.mark.parametrize('mutation', ['delete', 'reinstall', 'regression', 'replace-file'])
def test_full_restore_and_deletion_never_adopt_old_waiting_answer(certified, clock, mutation):
    from hermes_cli import kanban_activity_revision as rev
    path = certified['board']
    first = plugin.waiting_inputs()
    assert plugin.waiting_source() == {'c1': ['t_1'], 'c2': ['t_2']}
    if mutation == 'replace-file':
        replacement = path.with_name('replacement.db')
        with sqlite3.connect(path) as src, sqlite3.connect(replacement) as dst:
            src.backup(dst)
            dst.execute("UPDATE tasks SET status='running' WHERE id='t_2'")
        replacement.replace(path)
    else:
        with sqlite3.connect(path) as conn:
            conn.execute("UPDATE tasks SET status='running' WHERE id='t_2'")
            conn.commit()
            if mutation == 'delete':
                conn.execute("DELETE FROM tasks WHERE id='t_1'")
            elif mutation == 'reinstall':
                rev.drop_triggers(conn)
                rev.ensure_revision(conn, rev.LOCAL_TABLES)
            else:
                # Same-file restored counters regress together, retaining MAX(rev)==revision certification.
                conn.execute(f'UPDATE {rev.CHANGE_TABLE} SET rev=rev-100')
                conn.execute(f'UPDATE {rev.REVISION_TABLE} SET revision=revision-100')
    if mutation == 'delete':
        assert plugin.waiting_inputs() != first
    else:
        with pytest.raises(plugin.wi.Unportable):
            plugin.waiting_inputs()
    assert 'c2' not in plugin.waiting_source()


def test_batch_tail_growth_and_floor_still_invalidate(certified, clock):
    first = plugin.waiting_inputs()
    certified['led'].write('c1', batch=['t_1', 'other-task'])
    grown = plugin.waiting_inputs()
    assert grown != first
    with sqlite3.connect(certified['board']) as conn:
        conn.execute("UPDATE tasks SET status='done' WHERE id='other-task'")
    changed = plugin.waiting_inputs()
    assert changed != grown
    clock.wall += plugin.wi.FLOOR_S
    assert plugin.waiting_inputs() != changed


def test_real_holder_gate_skips_source_during_irrelevant_write_storm(certified, clock, monkeypatch):
    """Compose the plugin with core PR1's actual holder gate; older cores still exercise the other tests.

    HERMES_WAITING_GATE_SOURCE can name the exact candidate module for pre-landing compatibility proof.
    Other annotation parts are constant here: their declarations belong to core's own tests.
    """
    import importlib.util
    import os
    from hermes_cli import kanban_annotation_host as host, kanban_annotation_parts as parts
    candidate = os.environ.get('HERMES_WAITING_GATE_SOURCE')
    if candidate:
        spec = importlib.util.spec_from_file_location('waiting_gate_candidate', candidate)
        parts = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(parts)
    if not hasattr(parts, 'waiting'):
        pytest.skip('core predates holder waiting declaration consumption')
    scope = ('fixture',)
    calls = []
    def source():
        calls.append(1)
        return plugin.waiting_source()
    monkeypatch.setattr(ksa, '_origin_scope', lambda: scope)
    monkeypatch.setattr(ksa, '_waiting_sources', {'closeout-fixture': source})
    monkeypatch.setattr(host, '_declared', {'closeout-fixture': (source, plugin.waiting_inputs)})
    monkeypatch.setattr(parts, 'keys', lambda scope: {
        **{p: 'constant' for p in parts.ALL if p != parts.WAITING}, parts.WAITING: parts._waiting_key()})
    def refresh(clock_due=False):
        forced, key = parts.plan(scope, clock=clock_due)
        monkeypatch.setattr(ksa._revalidate.force_read, 'scope', scope, raising=False)
        monkeypatch.setattr(ksa._revalidate.force_read, 'parts', forced, raising=False)
        answer = parts.waiting(ksa._extra_waiting)
        parts.done(scope, key)
        return answer
    parts.reset()
    try:
        assert refresh() == {'c1': {'t_1'}, 'c2': {'t_2'}}
        for n in range(20):
            with sqlite3.connect(certified['board']) as conn:
                conn.execute("INSERT INTO task_comments VALUES (?, 'other-task', 'churn', 'agent', 100)", (n+1,))
                conn.execute("INSERT INTO task_events VALUES (?, 'other-task', 'heartbeat', '{}', 100)", (n+1,))
            with sqlite3.connect(certified['state']) as conn:
                conn.execute("INSERT INTO messages (session_id, role, content, timestamp, active) "
                             "VALUES ('other', 'user', 'irrelevant', 200, 1)")
            assert refresh() == {'c1': {'t_1'}, 'c2': {'t_2'}}
        assert len(calls) == 1  # ZERO source calls for all twenty storms, not only zero reconciled rows
        with sqlite3.connect(certified['board']) as conn:
            conn.execute("UPDATE tasks SET status='running' WHERE id='t_2'")
        assert refresh() == {'c1': {'t_1'}}  # no notify callback: lost-notification positive control
        assert len(calls) == 2
        refresh(clock_due=True)
        assert len(calls) == 3
    finally:
        parts.reset()
