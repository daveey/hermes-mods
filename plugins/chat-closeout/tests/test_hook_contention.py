"""Foreground closeout hooks never borrow the projection's unbounded lock wait."""
import importlib.util
import threading
import time
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location('closeout_contention', Path(__file__).parents[1] / '__init__.py')
plugin = importlib.util.module_from_spec(spec)
plugin.__package__ = ''
spec.loader.exec_module(plugin)


@pytest.fixture
def hooks(tmp_path, monkeypatch):
    monkeypatch.delenv('HERMES_KANBAN_TASK', raising=False)
    monkeypatch.delenv('CHAT_CLOSEOUT_OFF', raising=False)
    led = plugin.core.Ledger(tmp_path / 'ledger.db')
    led.write('chat', batch=['default/t_work'], open=['default/t_work'])
    monkeypatch.setattr(plugin, 'ledger', lambda: led)
    monkeypatch.setattr(plugin, '_upgraded', lambda *a: None)
    monkeypatch.setattr(plugin, '_reconcile_asks', lambda *a: {})
    monkeypatch.setattr(plugin, 'session_proof', lambda *a: False)
    monkeypatch.setattr(plugin, 'read_cards', lambda ids: {'default/t_work': {'status': 'running'}})
    return led


def test_post_returns_and_next_closeout_read_waits_for_stamp(hooks, monkeypatch):
    lock = threading.Lock()
    release = threading.Event()
    held = threading.Event()
    entered = threading.Event()

    def holder():
        with lock:
            held.set()
            release.wait(60)

    def ids(sid):
        entered.set()
        with lock:
            return ['default/t_work']

    thread = threading.Thread(target=holder)
    thread.start()
    assert held.wait(1)
    monkeypatch.setattr(plugin, 'open_card_ids', ids)
    try:
        before = time.monotonic()
        plugin.post_llm_call(session_id='chat', platform='desktop')
        assert time.monotonic() - before < 1
        assert entered.wait(1)
        assert hooks.get('chat').get('stamped_at') is None
        # Busy work must not be overtaken by the next turn's ledger read.
        reads = []
        original = hooks.get
        monkeypatch.setattr(hooks, 'get', lambda sid: (reads.append(sid), original(sid))[1])
        before = time.monotonic()
        assert plugin.pre_llm_call(session_id='chat', platform='desktop') is None
        assert time.monotonic() - before < 1
        assert not reads
        release.set()
        # No sleeps/poll loop: synchronize with the same hook barrier.
        assert plugin._flush_turn_end('chat', timeout=5)
        assert original('chat')['stamped_at'] is not None
        plugin.pre_llm_call(session_id='chat', platform='desktop')
        assert reads
    finally:
        release.set()
        thread.join(2)
        plugin._flush_turn_end('chat', timeout=5)


def test_prefinal_contended_probe_is_bounded(hooks, monkeypatch):
    release = threading.Event()
    entered = threading.Event()

    def backed(sid):
        entered.set()
        release.wait(60)
        return False

    monkeypatch.setattr(plugin, '_backed', backed)
    try:
        before = time.monotonic()
        out = plugin.pre_final_gate(session_id='chat', platform='desktop', final_response="I'll report back when it finishes.")
        assert time.monotonic() - before < 1
        assert entered.wait(1)
        assert out is None
    finally:
        release.set()
        plugin._flush_gate('chat', timeout=5)


def test_deferred_stamp_keeps_observed_inputs(hooks, monkeypatch):
    release = threading.Event()
    monkeypatch.setattr(plugin, 'open_card_ids', lambda sid: (release.wait(60), ['default/t_work'])[1])
    seen = []
    original = plugin.core.record_turn_end

    def record(*args, **kwargs):
        seen.append(kwargs['observed_sig'])
        return original(*args, **kwargs)

    monkeypatch.setattr(plugin.core, 'record_turn_end', record)
    plugin._state.setdefault('observed', {})['chat'] = 'original-generation'
    try:
        plugin.post_llm_call(session_id='chat', platform='desktop')
        plugin._state['observed']['chat'] = 'new-generation'
        release.set()
        assert plugin._flush_turn_end('chat', timeout=5)
        assert seen == ['original-generation']
        assert plugin._state['observed']['chat'] == 'new-generation'
    finally:
        release.set()
        plugin._flush_turn_end('chat', timeout=5)


def test_real_links_lock_held_sixty_seconds(hooks, monkeypatch):
    ksa = pytest.importorskip('hermes_cli.kanban_session_activity')
    held = threading.Event()
    scope = ('contention-test',)
    entry = (0, scope, (), [{'id': 't_work', 'key': 'default/t_work'}], [])
    monkeypatch.setattr(ksa, '_links_fingerprint', lambda: ())
    monkeypatch.setattr(ksa, '_links_cache', {})
    monkeypatch.setattr(ksa, '_publish_links', lambda *a: entry)
    monkeypatch.setattr(ksa._revalidate, 'forced', lambda *a: False)
    def project(sid):
        return [r['key'] for r in ksa._links_lookup(scope, False)[3]]
    monkeypatch.setattr(plugin, 'open_card_ids', project)
    monkeypatch.setattr(plugin, '_shown_ids', lambda sid: set(project(sid)))
    def holder():
        with ksa._links_lock:
            held.set()
            time.sleep(60)
    thread = threading.Thread(target=holder)
    thread.start()
    assert held.wait(2)
    try:
        start = time.monotonic()
        plugin.post_llm_call(session_id='chat', platform='desktop')
        assert time.monotonic() - start < 1
        turn = [{'role': 'tool', 'name': 'kanban_create', 'content': '{"ok": true, "task_id": "t_work"}'}]
        start = time.monotonic()
        assert plugin.pre_final_gate(session_id='chat', platform='desktop', final_response='Created.',
                                     turn_messages=turn) is None
        assert time.monotonic() - start < 1
        assert plugin._flush_turn_end('chat', timeout=65)
        assert hooks.get('chat')['stamped_at'] is not None
    finally:
        thread.join(65)
        plugin._flush_turn_end('chat', timeout=5)
        plugin._flush_gate('chat', timeout=5)


def test_lane_fifo_and_submission_context():
    import contextvars
    lane = plugin.hook_work.Lanes()
    var = contextvars.ContextVar('fixture_profile', default='unknown')
    release = threading.Event()
    seen = []
    var.set('first')
    first = lane.submit('s', lambda: (release.wait(2), seen.append(var.get())))
    var.set('second')
    second = lane.submit('s', lambda: seen.append(var.get()))
    release.set()
    second.result(timeout=3)
    assert seen == ['first', 'second']
    assert first.done()


def test_completed_probe_is_not_reused():
    lane = plugin.hook_work.Lanes()
    in_callback = threading.Event()
    release = threading.Event()
    running = threading.Event()
    proceed = threading.Event()
    future = lane.submit('s', lambda: (running.set(), proceed.wait(2), 'old')[-1])
    assert running.wait(1)
    def callback(f):
        in_callback.set()
        release.wait(2)
    future.add_done_callback(callback)
    proceed.set()
    assert in_callback.wait(1)
    new = lane.submit('s', lambda: 'new', reuse=True)
    try:
        assert new is not future
    finally:
        release.set()
    assert new.result(timeout=3) == 'new'


def test_reload_keeps_pending_stamp_barrier(hooks, monkeypatch):
    release = threading.Event()
    monkeypatch.setattr(plugin, 'open_card_ids', lambda sid: (release.wait(5), ['default/t_work'])[1])
    plugin.post_llm_call(session_id='chat', platform='desktop')
    reloaded = importlib.util.module_from_spec(spec)
    reloaded.__package__ = ''
    spec.loader.exec_module(reloaded)
    try:
        assert reloaded._turn_ends is plugin._turn_ends
        assert not reloaded._flush_turn_end('chat', timeout=0)
    finally:
        release.set()
        assert reloaded._flush_turn_end('chat', timeout=5)

