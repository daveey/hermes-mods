"""Describe memo re-review fixes (cc/describe-memo @ d4dfa68d): one process-wide swallow recorder across module
generations, a recorder that can never fail a card read, unreserved monitoring ids only, released on stop, replica
counter regression, replica identity and counters read on one connection, bookkeeping guard, unsafe end to end.
[daveey]"""
import contextlib
import importlib.util
import os
import sqlite3
import sys
from pathlib import Path

import pytest

from test_core_readers import api, board, kanban_db, readers_for  # noqa: F401  (fixtures)
from test_reader_batch import graph  # noqa: F401  (fixture)
from test_describe_memo import REAL_DESCRIBE, _enroll, calls, fresh, kas, run_pass  # noqa: F401
from test_describe_memo_review import _foreign_child

monitoring = sys.monitoring


def _reset_recorder():
    """Return the process recorder to unused (test readers are dropped without a feed stop)."""
    ns = sys.modules.get('_session_panel_describe_swallow')
    if ns is None:
        return
    with ns.lock:
        if ns.tool is not None and monitoring.get_tool(ns.tool) == 'session-panel-describe-memo':
            monitoring.set_events(ns.tool, 0)
            monitoring.register_callback(ns.tool, monitoring.events.EXCEPTION_HANDLED, None)
            monitoring.free_tool_id(ns.tool)
        ns.tool, ns.users = None, 0
        ns.epoch += 1


@pytest.fixture(autouse=True)
def _fresh_recorder():
    _reset_recorder()
    yield
    _reset_recorder()


def _swallow_in_core():
    """A real hermes_cli function that handles an exception internally (a malformed JSON payload)."""
    assert kas.record('{not json') == {}


def _second_copy():
    spec = importlib.util.spec_from_file_location(
        '_spf_api_second_generation', Path(__file__).resolve().parents[1] / 'dashboard' / 'plugin_api.py')
    mod = importlib.util.module_from_spec(spec)
    sys.modules['_spf_api_second_generation'] = mod
    spec.loader.exec_module(mod)
    return mod


def test_two_module_generations_share_one_recorder(api):
    other = _second_copy()
    try:
        assert api._swallow_acquire()        # generation A installs
        assert other._swallow_acquire()      # generation B: must not replace A's recorder with its own
        assert api._swallow_acquire()        # and A again, alternating
        for mod in (api, other, api):
            epoch = mod._swallow_begin()
            _swallow_in_core()
            hits = mod._swallow_end(epoch)
            assert hits, f'{mod.__name__} recorded nothing'
    finally:
        for mod in (api, other, api):
            mod._swallow_release()
        sys.modules.pop('_spf_api_second_generation', None)


def test_a_store_in_one_generation_sees_a_swallow_installed_by_another(graph, board, calls, monkeypatch):
    r, local, _, _ = graph
    path, _ = board
    other = _second_copy()
    try:
        assert other._swallow_acquire()  # a newer generation (re)installs while this one's pass runs

        def swallowing(conn, tid, **kw):
            calls.append(tid)
            _swallow_in_core()
            return REAL_DESCRIBE(conn, tid, **kw)
        monkeypatch.setattr(kas, 'describe', swallowing)
        run_pass(r, local)
        run_pass(r, local)
        assert calls.count(local) == 2  # a degraded describe is never stored
    finally:
        other._swallow_release()
        sys.modules.pop('_spf_api_second_generation', None)


def test_a_recorder_that_cannot_install_turns_the_memo_off(graph, calls, monkeypatch):
    r, local, _, _ = graph

    def refuse(*_):
        raise ValueError('tool id taken by someone else')
    monkeypatch.setattr(monitoring, 'use_tool_id', refuse)
    monkeypatch.setattr(monitoring, 'get_tool', lambda i: None)
    first = run_pass(r, local)
    second = run_pass(r, local)
    assert first == second and first is not None  # the card still reads
    assert calls.count(local) == 2                # describe afresh, nothing stored


def test_only_unreserved_monitoring_ids_are_used(api, monkeypatch):
    taken = {3: 'someone', 4: 'someone else'}
    monkeypatch.setattr(monitoring, 'get_tool', lambda i: taken.get(i))
    assert api._swallow_acquire() is False  # 0 (debugger), 1, 2 and 5 are never borrowed
    monkeypatch.undo()
    assert api._swallow_acquire()
    try:
        assert api._SWALLOW.tool in (3, 4)
    finally:
        api._swallow_release()


def test_the_last_release_frees_the_tool_id(api):
    assert api._swallow_acquire()
    tool = api._SWALLOW.tool
    assert api._swallow_acquire()
    api._swallow_release()
    assert monitoring.get_tool(tool) == api._SWALLOW_NAME  # one user left
    api._swallow_release()
    assert monitoring.get_tool(tool) is None and api._SWALLOW.tool is None


def test_stop_releases_the_feed_memo(graph, api, monkeypatch):
    r, local, _, _ = graph
    run_pass(r, local)
    tool = api._SWALLOW.tool
    assert tool is not None and r._describe_memo.recorder
    feed = api._load_feed().Feed(r, emit=lambda _: None)
    monkeypatch.setattr(api._load_feed(), 'PROCESS_FEED', feed)
    api.stop()
    assert r._describe_memo.closed and not r._describe_memo.recorder
    assert monitoring.get_tool(tool) is None


def test_a_replica_restored_in_place_with_an_older_counter_misses(graph, board, calls, tmp_path):
    from hermes_cli import kanban_activity_revision as rev
    r, _, remote, _ = graph
    path, c = board
    _enroll(c)
    child = _foreign_child(c, remote)
    run_pass(r, child)
    (entry,) = [e for k, e in r._describe_memo.entries.items() if k[1] == child]
    stored = entry['replica'][5]
    rc = sqlite3.connect(tmp_path / 'replica.db')  # same file, same inode: rolled back to an older counter
    rc.execute("UPDATE tasks SET status='done' WHERE id=?", (remote,))
    rc.execute(f'UPDATE {rev.CHANGE_TABLE} SET rev=? WHERE task_id=?', (stored - 1, remote))
    rc.execute(f'DELETE FROM {rev.CHANGE_TABLE} WHERE rev >= ?', (stored,))
    rc.execute(f'UPDATE {rev.REVISION_TABLE} SET revision=? WHERE id=1', (stored - 1,))
    rc.commit()
    assert rc.execute(f'SELECT MAX(rev) FROM {rev.CHANGE_TABLE}').fetchone()[0] == stored - 1
    rc.close()
    after = run_pass(r, child)
    assert calls.count(child) == 2
    assert after == fresh(path, child)


def test_replica_identity_and_counters_come_from_one_connection(graph, board, calls, tmp_path, monkeypatch):
    """A hit opens the replica once and checks that connection's file is the one at the path."""
    r, _, remote, _ = graph
    _, c = board
    _enroll(c)
    child = _foreign_child(c, remote)
    run_pass(r, child)
    replica = str(tmp_path / 'replica.db')
    opened = []
    real = r.conn

    def counting(p):
        if str(p) == replica:
            opened.append(p)
        return real(p)
    monkeypatch.setattr(r, 'conn', counting)
    run_pass(r, child)
    assert calls.count(child) == 1
    assert len(opened) == 1, len(opened)


def test_a_stale_replica_connection_misses(graph, board, calls, tmp_path, monkeypatch):
    """The connection reads a file that is no longer the one at the path: never vouched for."""
    r, _, remote, _ = graph
    path, c = board
    _enroll(c)
    child = _foreign_child(c, remote)
    run_pass(r, child)
    replica = tmp_path / 'replica.db'
    stale = r.conn(replica)
    copy_path = tmp_path / 'copy.db'
    dest = sqlite3.connect(copy_path)
    stale.backup(dest)
    dest.close()
    os.replace(copy_path, replica)
    real = r.conn
    monkeypatch.setattr(r, 'conn', lambda p: stale if str(p) == str(replica) else real(p))
    run_pass(r, child)
    assert calls.count(child) == 2


def test_bookkeeping_reads_are_never_predicates(api):
    assert api._describe_statement("SELECT * FROM tasks WHERE assignee='x'")[0] == 'rerun'
    assert api._describe_statement("SELECT claim_lock FROM tasks WHERE status='running'")[0] == 'rerun'
    assert api._describe_statement("SELECT count(*) FROM task_events WHERE kind='heartbeat'")[0] == 'rerun'
    assert api._describe_statement("SELECT title FROM tasks WHERE assignee='x'")[0] == 'predicate'


def test_a_heartbeat_read_by_predicate_misses(graph, board, monkeypatch):
    r, local, _, _ = graph
    _, c = board

    def with_beats(conn, tid, **kw):
        row = dict(REAL_DESCRIBE(conn, tid, **kw))
        row['beats'] = conn.execute("SELECT count(*) FROM task_events WHERE kind='heartbeat'").fetchone()[0]
        return row
    monkeypatch.setattr(kas, 'describe', with_beats)
    before = run_pass(r, local)['beats']
    c.execute("INSERT INTO task_events (task_id, kind, payload, created_at) VALUES (?, 'heartbeat', '{}', 1)", (local,))
    c.commit()  # a heartbeat moves no revision
    assert run_pass(r, local)['beats'] == before + 1


def test_an_unsafe_statement_is_never_stored(graph, monkeypatch):
    r, local, _, _ = graph
    seen = []

    def with_comma_join(conn, tid, **kw):
        seen.append(tid)
        row = dict(REAL_DESCRIBE(conn, tid, **kw))
        row['pairs'] = conn.execute('SELECT count(*) FROM tasks, task_links').fetchone()[0]
        return row
    monkeypatch.setattr(kas, 'describe', with_comma_join)
    run_pass(r, local)
    run_pass(r, local)
    assert seen.count(local) == 2
    assert r._describe_memo.stats['not_stored_unsafe'] >= 2
