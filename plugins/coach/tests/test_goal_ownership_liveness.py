"""Isolated ownership regressions; never create fixtures in a live home."""
import importlib.util
import sqlite3
import threading
import time
from pathlib import Path

import pytest
from test_goal_ownership import owner, SID, TID, gd
from goal_consumer_fixture import consumer

path = Path(__file__).parents[1] / 'goal_ownership.py'
if path.exists():
    spec = importlib.util.spec_from_file_location('ownership_liveness', path)
    ownership = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ownership)
else:
    ownership = gd


def certify(owner):
    ownership.authoritative_owner(owner[1], owner[2], SID)


def test_subscription_is_not_live_consumer(owner):
    with pytest.raises(ValueError, match='owner_result_consumer_unverified'):
        certify(owner)


@pytest.mark.parametrize('status', ['scheduled', 'todo'])
def test_waiting_without_dispatcher_is_unknown(owner, status):
    with sqlite3.connect(owner[0]) as conn:
        conn.execute('UPDATE tasks SET status=?', (status,))
    with pytest.raises(ValueError, match='owner_(scheduled_wake|dependency|dispatcher)_unverified'):
        certify(owner)


def test_unknown_schema_never_creates_evidence(owner):
    with sqlite3.connect(owner[0]) as conn:
        conn.execute('DROP TABLE task_runs')
    before = owner[0].read_bytes()
    with pytest.raises(ValueError):
        certify(owner)
    assert owner[0].read_bytes() == before


def test_live_native_poller_and_completion_to_exact_origin(owner, consumer):
    before = {p: p.read_bytes() for p in owner[0].parent.glob('*.db')}
    certify(owner)
    assert all(p.read_bytes() == data for p, data in before.items())
    with sqlite3.connect(owner[0]) as conn:
        conn.execute("UPDATE tasks SET status='done', completed_at=?", (int(time.time()),))
        conn.execute("INSERT INTO task_events(task_id,kind,payload,created_at) VALUES (?,'completed','{}',?)",
                     (TID, int(time.time())))
    deadline = time.monotonic() + 3
    while not consumer[2] and time.monotonic() < deadline:
        time.sleep(.02)
    assert consumer[2] == [f'{SID}:{TID}:completed']
    with pytest.raises(ValueError):
        certify(owner)


@pytest.mark.parametrize('change', ['stop', 'finalized', 'origin', 'profile', 'refused'])
def test_consumer_must_be_scoped_and_live(owner, consumer, change):
    session, stop, _ = consumer
    if change == 'stop':
        stop.set()
    elif change == 'finalized':
        session['_finalized'] = True
    elif change == 'origin':
        session['session_key'] = 'another-origin'
    elif change == 'profile':
        session['profile_home'] = str(owner[0].parent / 'another-profile')
    else:
        session['_kanban_refused_until'] = time.monotonic() + 60
    with pytest.raises(ValueError, match='owner_result_consumer_unverified'):
        certify(owner)


@pytest.mark.parametrize('wake', ['future', 'past', 'nan', 'bool'])
def test_native_scheduled_evidence_still_needs_dispatcher(owner, wake):
    import json
    value = {'future':time.time()+300, 'past':1, 'nan':float('nan'), 'bool':True}[wake]
    with sqlite3.connect(owner[0]) as conn:
        conn.execute("UPDATE tasks SET status='scheduled'")
        conn.execute("INSERT INTO task_events(task_id,kind,payload,created_at) VALUES (?,'scheduled',?,?)",
                     (TID, json.dumps({'wake_at':value}), int(time.time())))
    reason = 'dispatcher' if wake == 'future' else 'scheduled_wake'
    with pytest.raises(ValueError, match=f'owner_{reason}_unverified'):
        certify(owner)


def test_native_dependency_link_still_needs_dispatcher(owner):
    with sqlite3.connect(owner[0]) as conn:
        conn.execute("UPDATE tasks SET status='todo', block_kind='dependency'")
        conn.execute("INSERT INTO tasks(id,title,status,created_at) VALUES ('t_87654321','Prerequisite','running',?)",
                     (int(time.time()),))
        conn.execute("INSERT INTO task_links(parent_id,child_id) VALUES ('t_87654321',?)", (TID,))
    with pytest.raises(ValueError, match='owner_dispatcher_unverified'):
        certify(owner)


@pytest.mark.parametrize('sql', [
    "UPDATE tasks SET status='done'", "UPDATE tasks SET assignee='other'",
    "UPDATE tasks SET body='Unrelated work'", "UPDATE tasks SET session_id='other'",
    "UPDATE task_runs SET claim_expires=1", "UPDATE task_runs SET worker_started_at='wrong|123'",
    "UPDATE task_runs SET profile='other'", "UPDATE task_runs SET worker_pid=99999999",
    "DELETE FROM kanban_notify_subs", "UPDATE kanban_notify_subs SET notifier_profile='other'",
    "UPDATE kanban_notify_subs SET chat_id='other'",
])
def test_live_consumer_never_overrides_bad_owner(owner, consumer, sql):
    with sqlite3.connect(owner[0]) as conn:
        conn.execute(sql)
    with pytest.raises(ValueError):
        certify(owner)


def test_consumer_board_must_be_visible(owner, consumer, monkeypatch):
    from hermes_cli import kanban_db as kb
    monkeypatch.setattr(kb, 'list_boards', lambda **kw: [])
    with pytest.raises(ValueError, match='owner_result_consumer_unverified'):
        certify(owner)


def test_named_thread_alone_is_not_consumer(owner):
    stop = threading.Event()
    thread = threading.Thread(target=stop.wait, name=f'tui-notif-poller-{SID}')
    thread.start()
    try:
        with pytest.raises(ValueError, match='owner_result_consumer_unverified'):
            certify(owner)
    finally:
        stop.set()
        thread.join(3)
