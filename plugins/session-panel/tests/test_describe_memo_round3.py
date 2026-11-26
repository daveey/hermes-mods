"""Describe memo round-3 nits (cc/describe-memo @ 3f1b644e): no recorder user leaks when close() races refresh, an
explicit retire() for a replaced readers object, and two predicate shapes heartbeats move without the revision.
[daveey]"""
import pytest

from test_core_readers import api, board  # noqa: F401  (fixtures)
from test_reader_batch import graph  # noqa: F401  (fixture)
from test_describe_memo import REAL_DESCRIBE, calls, kas, run_pass  # noqa: F401
from test_describe_memo_rereview import _reset_recorder


@pytest.fixture(autouse=True)
def _fresh_recorder():
    _reset_recorder()
    yield
    _reset_recorder()


def test_a_close_during_refresh_leaks_no_recorder_user(graph, api, monkeypatch):
    r, local, _, _ = graph
    real = api._swallow_acquire

    def closing_meanwhile():
        r._describe_memo.close()  # the feed stops while this pass acquires
        return real()
    monkeypatch.setattr(api, '_swallow_acquire', closing_meanwhile)
    run_pass(r, local)
    assert api._SWALLOW.users == 0 and api._SWALLOW.tool is None
    assert not r._describe_memo.recorder


def test_retire_closes_the_memo_and_release_keeps_it(graph, api, calls):
    r, local, _, _ = graph
    run_pass(r, local)
    r.release()  # after every pass: the memo survives
    run_pass(r, local)
    assert calls.count(local) == 1 and api._SWALLOW.users == 1
    r.retire()  # a replaced readers object
    assert r._describe_memo.closed and api._SWALLOW.users == 0 and api._SWALLOW.tool is None


@pytest.mark.parametrize('sql', [
    "SELECT count(*) FROM task_events WHERE task_id='t_1'",
    "SELECT MAX(id) FROM task_events",
    "SELECT EXISTS(SELECT 1 FROM task_events WHERE task_id='t_1' AND id > 5)",
    "SELECT title, t.* FROM tasks t WHERE t.assignee='x'",
    "SELECT a.id, b.* FROM tasks a JOIN task_links b ON b.child_id = a.id WHERE a.status='ready'",
])
def test_shapes_heartbeats_move_are_rerun(api, sql):
    assert api._describe_statement(sql)[0] == 'rerun'


def test_a_plain_predicate_on_tasks_is_still_a_predicate(api):
    assert api._describe_statement("SELECT count(*) FROM tasks WHERE assignee='x'")[0] == 'predicate'


def test_an_unkeyed_event_aggregate_misses_on_a_heartbeat(graph, board, monkeypatch):
    r, local, _, _ = graph
    _, c = board

    def with_newest_event(conn, tid, **kw):
        row = dict(REAL_DESCRIBE(conn, tid, **kw))
        row['newest'] = conn.execute(f"SELECT MAX(id) FROM task_events WHERE task_id='{tid}'").fetchone()[0]
        return row
    monkeypatch.setattr(kas, 'describe', with_newest_event)
    before = run_pass(r, local)['newest']
    c.execute("INSERT INTO task_events (task_id, kind, payload, created_at) VALUES (?, 'heartbeat', '{}', 1)", (local,))
    c.commit()  # a heartbeat moves no revision
    assert run_pass(r, local)['newest'] > before
