"""Scheduled certification composes native dispatch and return evidence."""
import importlib.util
import json
import sqlite3
import time
import sys
from pathlib import Path
import pytest
from test_goal_ownership import owner, SID, TID
from goal_consumer_fixture import consumer

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="native flock ownership proof requires Linux")

spec = importlib.util.spec_from_file_location('scheduled_owner', Path(__file__).parents[1] / 'goal_ownership.py')
ownership = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ownership)

@pytest.fixture
def scheduled(owner):
    with sqlite3.connect(owner[0]) as conn:
        conn.execute("UPDATE tasks SET status='scheduled', current_run_id=NULL")
        conn.execute("DELETE FROM task_runs")
        conn.execute("INSERT INTO task_events(task_id,kind,payload,created_at) VALUES (?,'scheduled',?,?)", (TID,json.dumps({'wake_at':time.time()+300}),int(time.time())))
    return owner

@pytest.fixture
def dispatcher(scheduled, monkeypatch):
    from gateway.kanban_dispatcher_health import DispatcherHealth
    from gateway.kanban_watchers_common import _acquire_singleton_lock, _release_singleton_lock
    from gateway.kanban_watchers_dispatcher import _KanbanDispatcher, _resolve_dispatcher_settings
    from hermes_cli import kanban_db as kb, kanban_db_dispatch as kbd
    root = scheduled[0].parent / 'kanban'
    monkeypatch.setattr(Path, 'home', lambda: scheduled[0].parent)
    monkeypatch.setenv('HERMES_KANBAN_HOME', str(scheduled[0].parent))
    monkeypatch.setattr(kbd, '_memory_pressure_level', lambda: 'unknown')
    loop = _KanbanDispatcher(kb, _resolve_dispatcher_settings({},kb))
    monkeypatch.setattr(loop, '_board_slugs', lambda: ['default'])
    monkeypatch.setattr(loop, '_receipt_scope', lambda: ({'default'}, {'ready'}))
    lock, state = _acquire_singleton_lock(root / '.dispatcher.lock')
    assert state == 'held'
    producer = DispatcherHealth(lock, interval=1)
    try:
        loop.tick_once()
        producer.publish(loop.successful_scopes)
        yield producer
    finally:
        producer.close()
        _release_singleton_lock(lock)


def test_scheduled_requires_both_native_receipts(scheduled, consumer, dispatcher):
    ownership.authoritative_owner(scheduled[1], scheduled[2], SID)
    dispatcher.close()
    with pytest.raises(ValueError, match='owner_dispatcher_unverified'):
        ownership.authoritative_owner(scheduled[1], scheduled[2], SID)


def test_scheduling_does_not_replace_return_receipt(scheduled, dispatcher):
    with pytest.raises(ValueError, match='owner_result_consumer_unverified'):
        ownership.authoritative_owner(scheduled[1], scheduled[2], SID)


def test_scheduling_cannot_override_scope(scheduled, consumer, dispatcher):
    with sqlite3.connect(scheduled[0]) as conn:
        conn.execute("UPDATE tasks SET body='Unrelated task'")
    with pytest.raises(ValueError, match='owner_task_scope_or_state_mismatch'):
        ownership.authoritative_owner(scheduled[1], scheduled[2], SID)


def test_public_gate_certifies_scheduled_owner_without_writes(scheduled, consumer, dispatcher, monkeypatch):
    from types import SimpleNamespace
    from test_goal_ownership import gd, REQUEST
    events = []
    monkeypatch.setattr(gd, 'settings', lambda: {'mode': 'enforce'})
    monkeypatch.setattr(gd, 'aux_judge', lambda *_: scheduled[1])
    before = {p: p.read_bytes() for p in scheduled[0].parent.glob('*.db')}
    gate = gd.build_gate(SimpleNamespace(log=events.append))
    assert gate(session_id=SID, final_response='Scheduled work is owned.',
                turn_messages=[dict(role='user', content=REQUEST)]) is None
    assert events[-1]['disposition'] == 'owned_continuing'
    assert all(p.read_bytes() == data for p, data in before.items())
    dispatcher.close()
    assert gate(session_id=SID, final_response='Scheduled work is owned.',
                turn_messages=[dict(role='user', content=REQUEST)]) is None
    assert events[-1]['event'] == 'goal_disposition_unknown'
    assert events[-1]['reason'] == 'owner_dispatcher_unverified'
