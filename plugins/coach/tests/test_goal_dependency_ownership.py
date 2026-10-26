"""Native dependency edges compose live ancestors and child return receipts."""
import sqlite3
import os
import time
import sys
from types import SimpleNamespace
import pytest
from test_goal_ownership import owner, SID, TID, gd, REQUEST
from test_goal_scheduled_ownership import scheduled, dispatcher, ownership
from goal_consumer_fixture import consumer

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="native flock proof requires Linux")

PARENT = "t_abcdef12"

@pytest.fixture
def dependency(scheduled, dispatcher):
    from hermes_cli import kanban_db as kb
    from hermes_cli.kanban_db_dispatch import _process_fingerprint
    from hermes_cli.kanban_host_identity import claim_prefix
    with sqlite3.connect(scheduled[0]) as c:
        c.row_factory = sqlite3.Row
        c.execute("UPDATE tasks SET status='ready'")
        c.execute("INSERT INTO tasks(id,title,assignee,status,created_at,current_run_id) VALUES (?, 'Prerequisite','default','running',1,2)", (PARENT,))
        c.execute("INSERT INTO task_runs(id,task_id,profile,status,claim_lock,claim_expires,worker_pid,worker_started_at,started_at) VALUES (2,?,'default','running',?,?,?,?,1)", (PARENT,claim_prefix()+str(os.getpid()),time.time()+300,os.getpid(),_process_fingerprint(os.getpid())))
        c.commit()
        assert kb.link_tasks(c, PARENT, TID)
    return scheduled


def certify(dependency):
    ownership.authoritative_owner(dependency[1], dependency[2], SID)


def test_native_dependency_owner_and_public_gate(dependency, consumer, monkeypatch):
    before = {p: p.read_bytes() for p in dependency[0].parent.glob("*.db")}
    certify(dependency)
    events = []
    monkeypatch.setattr(gd, "settings", lambda: {"mode": "enforce"})
    monkeypatch.setattr(gd, "aux_judge", lambda *_: dependency[1])
    gate = gd.build_gate(SimpleNamespace(log=events.append))
    assert gate(session_id=SID, final_response="Prerequisite continuing", turn_messages=[dict(role="user", content=REQUEST)]) is None
    assert events[-1]["disposition"] == "owned_continuing"
    assert all(p.read_bytes() == data for p, data in before.items())


@pytest.mark.parametrize("sql", [
    "UPDATE task_runs SET claim_expires=1",
    "UPDATE task_runs SET claim_lock='foreign:1'",
    "UPDATE task_runs SET status='failed'",
    "UPDATE task_runs SET outcome='spawn_failed'",
    "UPDATE task_runs SET ended_at=1",
    "UPDATE task_runs SET worker_started_at='wrong|1'",
    "UPDATE task_runs SET worker_pid=99999999",
    "UPDATE task_runs SET profile='other'",
    "UPDATE tasks SET assignee='other' WHERE id='t_abcdef12'",
    "UPDATE tasks SET status='blocked' WHERE id='t_abcdef12'",
    "UPDATE tasks SET status='ready' WHERE id='t_abcdef12'",
    "UPDATE tasks SET completed_at=1 WHERE id='t_abcdef12'",
    "DELETE FROM tasks WHERE id='t_abcdef12'",
    "INSERT INTO task_links VALUES ('t_1234abcd','t_abcdef12')",
    "INSERT INTO task_links VALUES ('t_deadbeef','t_abcdef12')",
    "DELETE FROM kanban_notify_subs WHERE task_id='t_1234abcd'",
])
def test_unverified_ancestor_or_return_unknown(dependency, consumer, sql):
    with sqlite3.connect(dependency[0]) as c:
        c.execute(sql)
    with pytest.raises(ValueError):
        certify(dependency)


def test_dependency_requires_fresh_dispatcher(dependency, consumer, dispatcher):
    certify(dependency)
    dispatcher.close()
    with pytest.raises(ValueError, match="owner_dispatcher_unverified"):
        certify(dependency)


def test_dependency_requires_child_consumer(dependency):
    with pytest.raises(ValueError, match="owner_result_consumer_unverified"):
        certify(dependency)


def test_scheduled_ancestor_needs_native_future_wake(dependency, consumer):
    import json
    with sqlite3.connect(dependency[0]) as c:
        c.execute("UPDATE tasks SET status='scheduled',current_run_id=NULL WHERE id=?", (PARENT,))
        c.execute("INSERT INTO task_events(task_id,kind,payload,created_at) VALUES (?,'scheduled',?,?)", (PARENT,json.dumps({"wake_at":time.time()+300}),int(time.time())))
    certify(dependency)
    with sqlite3.connect(dependency[0]) as c:
        c.execute("DELETE FROM task_events WHERE task_id=? AND kind='scheduled'", (PARENT,))
    with pytest.raises(ValueError):
        certify(dependency)


def test_transitive_shared_ancestor_checked(dependency, consumer):
    from hermes_cli import kanban_db as kb
    with sqlite3.connect(dependency[0]) as c:
        c.row_factory = sqlite3.Row
        for tid in ("t_abcdef13", "t_abcdef14"):
            c.execute("INSERT INTO tasks(id,title,status,assignee,created_at) VALUES (?,'Intermediate','ready','default',1)", (tid,))
        c.commit()
        for tid in ("t_abcdef13", "t_abcdef14"):
            kb.link_tasks(c, PARENT, tid)
            kb.link_tasks(c, tid, TID)
    certify(dependency)
    with sqlite3.connect(dependency[0]) as c:
        c.execute("UPDATE task_runs SET claim_expires=1")
    with pytest.raises(ValueError):
        certify(dependency)


def test_terminal_ancestor_does_not_hide_failed_transitive_owner(dependency, consumer):
    with sqlite3.connect(dependency[0]) as c:
        c.execute("INSERT INTO tasks(id,title,status,assignee,created_at,completed_at) VALUES ('t_abcdef13','Terminal','done','default',1,2)")
        c.execute("INSERT INTO task_links VALUES ('t_abcdef13','t_1234abcd')")
        c.execute("INSERT INTO tasks(id,title,status,assignee,created_at) VALUES ('t_abcdef14','Failed','blocked','default',1)")
        c.execute("INSERT INTO task_links VALUES ('t_abcdef14','t_abcdef13')")
    with pytest.raises(ValueError):
        certify(dependency)


@pytest.mark.parametrize("corruption", ["stale", "profile", "board", "lane"])
def test_dependency_dispatcher_scope_and_age(dependency, consumer, dispatcher, corruption):
    import json
    record = json.loads(dispatcher.path.read_text())
    if corruption == "stale":
        record["stamp"] -= 301
    elif corruption == "profile":
        record["boards"][0][1] = ["other"]
    elif corruption == "board":
        record["boards"][0][0] += ".foreign"
    else:
        record["boards"][0][2] = ["review"]
    dispatcher.path.write_text(json.dumps(record))
    with pytest.raises(ValueError, match="owner_dispatcher_unverified"):
        certify(dependency)


def test_dependency_graph_limit(dependency, consumer):
    with sqlite3.connect(dependency[0]) as c:
        for i in range(65):
            tid = f"t_{i:08x}"
            c.execute("INSERT INTO tasks(id,title,status,assignee,created_at,completed_at) VALUES (?,'Completed','done','default',1,2)", (tid,))
            c.execute("INSERT INTO task_links VALUES (?,?)", (tid,TID))
    with pytest.raises(ValueError, match="owner_dependency_unverified"):
        certify(dependency)


@pytest.mark.parametrize("status", ["archived", "failed", "review", "todo", "done"])
def test_ambiguous_terminal_or_unowned_ancestor(dependency, consumer, status):
    with sqlite3.connect(dependency[0]) as c:
        c.execute("UPDATE tasks SET status=? WHERE id=?", (status,PARENT))
    with pytest.raises(ValueError):
        certify(dependency)


def test_sql_work_budget_is_bounded(dependency):
    from contextlib import closing
    with closing(ownership._ro_connect(dependency[0])) as c:
        with pytest.raises(sqlite3.OperationalError, match="interrupted"):
            c.execute("WITH RECURSIVE n(x) AS (VALUES(1) UNION ALL SELECT x+1 FROM n WHERE x<1000000) SELECT sum(x) FROM n").fetchone()


def test_scheduled_ancestor_review_lane_not_certified_by_ready(dependency, consumer):
    import json
    with sqlite3.connect(dependency[0]) as c:
        c.execute("UPDATE tasks SET status='scheduled',current_run_id=NULL WHERE id=?", (PARENT,))
        c.execute("INSERT INTO task_events(task_id,kind,payload,created_at) VALUES (?,'continued',?,?)", (PARENT,json.dumps({"wake_at":time.time()+300,"resume_status":"review"}),int(time.time())))
    with pytest.raises(ValueError, match="owner_scheduled_wake_unverified"):
        certify(dependency)


@pytest.mark.parametrize("ancestor_status", ["blocked", "running"])
def test_scheduled_child_also_requires_live_dependencies(dependency, consumer, ancestor_status):
    # A timed wake lands in todo when its native parents remain unfinished.
    with sqlite3.connect(dependency[0]) as c:
        c.execute("UPDATE tasks SET status='scheduled' WHERE id=?", (TID,))
        c.execute("UPDATE tasks SET status=? WHERE id=?", (ancestor_status, PARENT))
        if ancestor_status == "running":
            c.execute("UPDATE task_runs SET claim_expires=1 WHERE task_id=?", (PARENT,))
    with pytest.raises(ValueError):
        certify(dependency)


def test_scheduled_child_with_live_dependency(dependency, consumer):
    with sqlite3.connect(dependency[0]) as c:
        c.execute("UPDATE tasks SET status='scheduled' WHERE id=?", (TID,))
    certify(dependency)
