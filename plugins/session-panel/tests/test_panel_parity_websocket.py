"""Ordered child events reach this serve's websocket stream; gaps/overflow become reset+snapshot."""
import hashlib
import importlib.util
import json
import sqlite3
import sys
import time
from pathlib import Path

import pytest

core_runtime = pytest.importorskip('hermes_cli.serve_producer_runtime')
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]


def wait_until(predicate, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    raise AssertionError('condition was not met')


def load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'dashboard' / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_event_ring_is_ordered_bounded_and_resets_on_overflow():
    ring = load('_ring_fixture', 'producer_provider.py').EventRing()
    assert ring.since(None) == {'seq': 0, 'events': [], 'reset': False}
    for i in range(300):
        ring.append({'rev': i})
    head = ring.since(None)['seq']
    assert head == 300
    # a cursor that fell off the ring (overflow) must reset, never skip silently
    assert ring.since(10)['reset'] is True
    assert ring.since(-1)['reset'] is True
    assert ring.since(head + 5)['reset'] is True  # cursor from a different child lifetime
    tail = ring.since(head - 3)
    assert tail['reset'] is False
    assert [seq for seq, _ in tail['events']] == [298, 299, 300]
    assert ring.since(head) == {'seq': head, 'events': [], 'reset': False}
    assert len(ring._items) <= ring.MAX_EVENTS


def test_event_ring_batch_is_byte_bounded():
    mod = load('_ring_batch_fixture', 'producer_provider.py')
    ring = mod.EventRing()
    big = {'blob': 'x' * 200_000}
    for _ in range(6):
        ring.append(big)
    batch = ring.since(0, 64)
    assert 1 <= len(batch['events']) <= 3  # MAX_BATCH_BYTES bounds one IPC frame


class StubChannel:
    def __init__(self, replies):
        self.replies = list(replies)
        self.sent = []

    def send(self, value, request_id='x'):
        self.sent.append(value)

    def receive(self):
        return self.replies.pop(0)


def relay_with(replies):
    relay = core_runtime.ProjectionRelay({}, home='/tmp', runtime='/tmp/r', generation='g')
    relay._channel = StubChannel(replies)
    got = []
    relay._event_sink = got.append
    return relay, got


def reply(relay, **fields):
    return {'verb': 'EVENTS', 'key': 'events', 'generation': relay.config['generation'], **fields}


def test_relay_delivers_contiguous_events_in_order_and_resets_on_gap():
    relay, got = relay_with([])
    relay._channel.replies = [
        reply(relay, seq=5, events=[], reset=True, snapshot={'bundles': {'a': 0}, 'rev': 5}),  # first poll
        reply(relay, seq=7, events=[[6, {'rev': 6}], [7, {'rev': 7}]], reset=False),
        reply(relay, seq=10, events=[[9, {'rev': 9}]], reset=False),       # 8 missing: gap
        reply(relay, seq=10, events=[], reset=True, snapshot={'bundles': {'a': 1}, 'rev': 10}),
        reply(relay, seq=11, events=[[11, {'rev': 11}]], reset=False),
    ]
    assert relay._event_cursor is None
    relay._poll_events()
    assert relay._channel.sent[0]['after'] == -1, 'the first poll asks for reset+snapshot, never a silent head adoption'
    assert got == [{'bundles': {'a': 0}, 'rev': 5, 'reset': True}] and relay._event_cursor == 5
    relay._poll_events()
    assert got[1:] == [{'rev': 6}, {'rev': 7}] and relay._event_cursor == 7
    relay._poll_events()
    assert got[1:] == [{'rev': 6}, {'rev': 7}], 'an out-of-order batch is never delivered'
    assert relay._event_cursor == -1
    assert relay._channel.sent[-1]['after'] == 7
    relay._poll_events()
    assert got[-1] == {'bundles': {'a': 1}, 'rev': 10, 'reset': True}
    assert relay._channel.sent[-1]['after'] == -1
    relay._poll_events()
    assert got[-1] == {'rev': 11}
    assert relay.event_resets == 2


def test_first_poll_after_attach_never_loses_events_emitted_before_the_sink():
    relay, got = relay_with([])
    relay._channel.replies = [
        reply(relay, seq=3, events=[], reset=True, snapshot={'bundles': {'a': 3}, 'rev': 3}),
        reply(relay, seq=4, events=[[4, {'rev': 4}]], reset=False),
    ]
    relay._poll_events()
    relay._poll_events()
    assert [item.get('rev') for item in got] == [3, 4]
    assert got[0]['reset'] is True and 'reset' not in got[1]


def test_relay_revocation_drops_in_flight_events_and_rejects_foreign_generation():
    relay, got = relay_with([])

    class Revoking(StubChannel):
        def receive(self):
            relay.revoke_generation()
            return super().receive()

    relay._channel = Revoking([reply(relay, seq=1, events=[[1, {'rev': 1}]], reset=False)])
    relay._event_cursor = 0
    relay._poll_events()
    assert got == [] and relay._event_cursor is None
    relay._channel = StubChannel([{**reply(relay, seq=0, events=[], reset=False), 'generation': 'other'}])
    with pytest.raises(Exception, match='generation'):
        relay._poll_events()


def test_real_exec_child_events_reach_serve_stream_with_serving_identity(tmp_path, monkeypatch):
    from hermes_cli.kanban_db_connect import connect
    from hermes_cli import kanban_db

    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.delenv('HERMES_KANBAN_DB', raising=False)
    monkeypatch.delenv('HERMES_KANBAN_HOME', raising=False)
    with sqlite3.connect(tmp_path / 'state.db') as db:
        db.execute('CREATE TABLE sessions(id TEXT PRIMARY KEY,parent_session_id TEXT,outcome_json TEXT)')
        db.execute("INSERT INTO sessions VALUES ('chat',NULL,'{}')")
    with connect(tmp_path / 'kanban.db') as db:
        kanban_db.create_task(db, title='Event work', session_id='chat')
    provider = ROOT / 'dashboard' / 'producer_provider.py'
    manifest = {'path': str(provider), 'sha256': hashlib.sha256(provider.read_bytes()).hexdigest()}
    relay = core_runtime.ProjectionRelay(manifest, home=tmp_path, runtime=tmp_path / 'runtime', generation='ws')
    monkeypatch.setattr(sys, 'argv', ['hermes', 'serve', '--ssh-session-token-file', '/slots/desktop-a/token'])
    api = load('_exec_panel_ws_fixture', 'plugin_api.py')
    frames = []
    import hermes_cli.plugin_events as plugin_events
    monkeypatch.setattr(plugin_events, 'broadcast_plugin_event',
                        lambda plugin, event, payload=None: frames.append((plugin, event, dict(payload))))
    try:
        relay.start_for_fixture(ready_timeout=60)
        api.attach_external(relay)
        app = FastAPI()
        app.include_router(api.router)
        with TestClient(app) as client:
            body = {'sessions': ['chat']}
            wait_until(lambda: 'chat' in client.post('/changes', json=body).json().get('bundles', {}), timeout=60)
            wait_until(lambda: any(f[2].get('bundles') or f[2].get('sessions') for f in frames), timeout=30)
        assert all(f[0] == api.PLUGIN_ID and f[1] == 'changed' for f in frames)
        # serving identity is the serve slot's, never the child's argv-derived identity
        assert {f[2]['feed'] for f in frames} == {api.FEED_ID}
        assert api.FEED_ID.endswith(':serve-desktop-a')
        revs = [f[2]['rev'] for f in frames if 'rev' in f[2] and not f[2].get('reset')]
        assert revs == sorted(revs)
        prevs = [f[2] for f in frames if 'prev' in f[2]]
        for earlier, later in zip(prevs, prevs[1:]):
            assert later['prev'] == earlier['rev'] or later['epoch'] != earlier['epoch']
    finally:
        api.detach_external(relay)
        assert relay.close(timeout=10)
    assert getattr(api._LOADER, 'external_projection', None) is None
