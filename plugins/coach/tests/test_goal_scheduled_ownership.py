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
    """Receipt for this board only. Do not tick the live dispatcher.

    ``available()`` fails closed when ``/proc/locks`` omits this flock or a
    host pause sentinel exists. Both are shared with the gateway dispatcher
    and with sibling ``-j`` workers, so a miss becomes
    ``owner_dispatcher_unverified`` before the consumer check. Scope, ttl and
    invalidation stay on the real receipt; lock ownership and pause paths are
    bound to this fixture.
    """
    import os
    from gateway import kanban_dispatcher_health as health
    from gateway.kanban_watchers_common import _acquire_singleton_lock, _release_singleton_lock
    from hermes_cli import kanban_db as kb
    root = scheduled[0].parent / 'kanban'
    root.mkdir(parents=True, exist_ok=True)
    sentinel = root / 'estop-absent'
    monkeypatch.setattr('agent.estop._candidate_sentinel_paths', lambda: [sentinel])
    lock, state = _acquire_singleton_lock(root / '.dispatcher.lock')
    assert lock is not None and state == 'held'
    held = lock

    def owns_fixture_lock(path, pid, _lock=held):
        try:
            name = getattr(_lock, 'name', None)
            if pid != os.getpid() or getattr(_lock, 'closed', True) or not name:
                return False
            return Path(path).resolve() == Path(name).resolve()
        except OSError:
            return False

    monkeypatch.setattr(health, '_owns_lock', owns_fixture_lock)
    producer = health.DispatcherHealth(lock, interval=60)
    try:
        producer.publish([(str(scheduled[0].resolve()), ['default'], ['ready'], health.time.monotonic())])
        assert producer.path is not None
        record = json.loads(producer.path.read_text())
        assert record.get('boards'), 'fixture must publish a scope without a live tick'
        assert record.get('pause_paths') and all(
            Path(p).resolve().is_relative_to(root.resolve()) for p in record['pause_paths'])
        assert health.available(kb.kanban_home() / 'kanban', scheduled[0], 'default', 'ready')
        yield producer
    finally:
        producer.close()
        _release_singleton_lock(lock)


def test_scheduled_requires_both_native_receipts(scheduled, consumer, dispatcher):
    ownership.authoritative_owner(scheduled[1], scheduled[2], SID)
    dispatcher.close()
    with pytest.raises(ValueError, match='owner_dispatcher_unverified'):
        ownership.authoritative_owner(scheduled[1], scheduled[2], SID)


def test_scheduling_does_not_replace_return_receipt(scheduled, dispatcher, monkeypatch):
    # Parallel workers rewrite /proc/locks. A missing line must not become
    # owner_dispatcher_unverified; scheduling still needs the return consumer.
    real = Path.read_text
    def poisoned(self, *args, **kwargs):
        if self == Path('/proc/locks'):
            return ''
        return real(self, *args, **kwargs)
    monkeypatch.setattr(Path, 'read_text', poisoned)
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
