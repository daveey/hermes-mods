"""Full bytes through two real exec boundaries and HTTP; real CoreReaders, no field filtering."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import time

import pytest

runtime = pytest.importorskip('hermes_cli.serve_producer_runtime')
from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_panel_parity_changes import home, legacy_feed, load, identity

ROOT = Path(__file__).resolve().parents[1]


def helper():
    path = ROOT / 'tests' / 'controlled_exec_provider.py'
    spec = importlib.util.spec_from_file_location('_parity_control', path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('scenario', ['active', 'mixed'])
@pytest.mark.parametrize('verb', ['changes', 'status', 'decision-refresh', 'events'])
def test_real_exec_http_complete_bytes_match_legacy(home, monkeypatch, verb, scenario):
    api = load('_full_wire_legacy_api', 'plugin_api.py')
    control = helper()
    legacy = legacy_feed(api)
    emitted = []
    legacy.emit = emitted.append
    frames = []
    import hermes_cli.plugin_events as plugin_events
    monkeypatch.setattr(plugin_events, 'broadcast_plugin_event',
                        lambda plugin, event, payload=None: frames.append(payload))
    # Non-ASCII survives the wire->IPC text->wire round trip without escaping drift.
    from hermes_cli.kanban_db_connect import connect
    from hermes_cli import kanban_db
    with connect(home / 'kanban.db') as db:
        db.execute("UPDATE tasks SET title='Parity work — 雪'")
        if scenario == 'mixed':
            parent = kanban_db.create_task(db, title='Nested parent', session_id='chat')
            kanban_db.create_task(db, title='Nested child', session_id='chat', parents=[parent])
            for state in ('done', 'scheduled', 'blocked'):
                task = kanban_db.create_task(db, title='Parity ' + state, session_id='chat')
                db.execute('UPDATE tasks SET status=?, completed_at=? WHERE id=?',
                           (state, int(control.WALL) - 1 if state == 'done' else None, task))

    sessions = ['chat', 'absent', 'chat'] if scenario == 'mixed' and verb == 'changes' else ['chat']
    with control.clocks(legacy.readers):
        legacy.changes_bytes(sessions)
        legacy._drain()
        legacy.tick()
        if verb == 'changes':
            want = api._WIRE_PREFIX + legacy.prepared_changes_bytes(sessions)[1:]
        elif verb == 'events':
            want = json.dumps(api.push_payload(emitted[-1]), ensure_ascii=False, separators=(',', ':')).encode()
        elif verb == 'status':
            want = json.dumps(api.json_safe({**identity(api), 'running': True, **legacy.status()}),
                              ensure_ascii=False, separators=(',', ':')).encode()
        else:
            result = legacy.refresh_decision('chat', readers=legacy.readers)
            result['bundle'] = legacy.decision_projection(result['bundle'])
            want = json.dumps(api.json_safe({**identity(api), **result}),
                              ensure_ascii=False, separators=(',', ':')).encode()
    fixture = ROOT / 'tests' / 'controlled_exec_provider.py'
    spec = {'path': str(fixture), 'sha256': hashlib.sha256(fixture.read_bytes()).hexdigest()}
    relay = runtime.ProjectionRelay(spec, home=home, runtime=home / 'runtime', generation='full-wire')
    try:
        relay.start_for_fixture(ready_timeout=60)
        api.attach_external(relay)
        if verb == 'events':
            deadline = time.monotonic() + 30
            while not frames:
                assert time.monotonic() < deadline
                time.sleep(.05)
            assert frames[0]['reset'] is True
        monkeypatch.setattr(api, '_request_profile', lambda: api.LAUNCH_PROFILE)
        app = FastAPI()
        app.include_router(api.router)
        with TestClient(app) as client:
            deadline = time.monotonic() + 60
            while True:
                if verb == 'status':
                    response = client.get('/status')
                elif verb == 'decision-refresh':
                    response = client.post('/decision-refresh', json={'session': 'chat'})
                else:
                    response = client.post('/changes', json={'sessions': sessions})
                if response.status_code == 200:
                    break
                assert response.status_code == 503 and response.headers['retry-after'] == '1'
                assert time.monotonic() < deadline
                time.sleep(.05)
            if verb == 'events':
                while not any(frame.get('prev') is not None for frame in frames):
                    assert time.monotonic() < deadline
                    time.sleep(.05)
                frame = next(frame for frame in frames if frame.get('prev') is not None)
                assert json.dumps(frame, ensure_ascii=False, separators=(',', ':')).encode() == want
            else:
                assert response.content == want
                if scenario == 'mixed' and verb == 'changes':
                    for title in ('Nested parent', 'Nested child', 'Parity done', 'Parity scheduled', 'Parity blocked'):
                        assert title.encode() in response.content, response.json()
                    revision = response.json()['rev']
                    with control.clocks(legacy.readers):
                        unchanged = api._WIRE_PREFIX + legacy.prepared_changes_bytes(
                            sessions, since=revision, epoch='parity-epoch')[1:]
                    again = client.post('/changes', json={'sessions': sessions, 'since': revision,
                                                         'epoch': 'parity-epoch'})
                    deadline = time.monotonic() + 30
                    while again.status_code == 503:
                        assert time.monotonic() < deadline
                        time.sleep(.05)
                        again = client.post('/changes', json={'sessions': sessions, 'since': revision,
                                                             'epoch': 'parity-epoch'})
                    assert again.status_code == 200
                    assert again.content == unchanged
    finally:
        api.detach_external(relay)
        assert relay.close(timeout=10)
        legacy.readers.release()
        legacy.stopped.set()
