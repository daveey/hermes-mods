"""Interleaved fingerprints and C15 attribution regressions."""
import threading
import sqlite3
import pytest
from test_waiting_inputs import world, plugin, poll, _notice


def test_interleaved_fingerprints_keep_invalidation(world, monkeypatch):
    monkeypatch.setattr(plugin.time, 'monotonic', lambda: 100.0)
    assert poll() == {'c1': ['t_1'], 'c2': ['t_2']}
    _notice(world['state'], 'c1', 101)
    plugin.waiting_inputs()
    errors = []
    def other():
        try:
            plugin.waiting_inputs()
        except Exception as exc:
            errors.append(exc)
    thread = threading.Thread(target=other)
    thread.start()
    thread.join()
    assert not errors
    assert plugin.waiting_source() == {'c2': ['t_2']}


def test_moved_same_answer_has_separate_cpu(world):
    la = pytest.importorskip('hermes_cli.loop_accounting')
    poll()
    before = la.counters().get('chat-closeout.waiting_moved', {}).get('runs', 0)
    changes_before = la.counters()['chat-closeout.waiting']['changes']
    with sqlite3.connect(world['state']) as conn:
        conn.execute("INSERT INTO messages (session_id, role, content, timestamp, active) VALUES ('c3','user','hello',200,1)")
    assert poll() == {'c1': ['t_1'], 'c2': ['t_2']}
    assert la.counters()['chat-closeout.waiting']['changes'] == changes_before + 1
    moved = la.counters()['chat-closeout.waiting_moved']
    assert moved['runs'] == before + 1
    assert moved['cpu_s'] > 0
    changes = la.counters()['chat-closeout.waiting']['changes']
    poll()
    assert la.counters()['chat-closeout.waiting_moved']['runs'] == before + 1
    assert la.counters()['chat-closeout.waiting']['changes'] == changes


def test_fallback_is_not_input_moved(world, monkeypatch):
    la = pytest.importorskip('hermes_cli.loop_accounting')
    poll()
    before = la.counters().get('chat-closeout.waiting_moved', {}).get('runs', 0)
    def unavailable(*a, **kw):
        raise LookupError('unreadable')
    monkeypatch.setattr(plugin, '_input_parts', unavailable)
    poll()
    assert la.counters().get('chat-closeout.waiting_moved', {}).get('runs', 0) == before
    assert la.counters()['chat-closeout.waiting_fallback']['runs'] >= 1
