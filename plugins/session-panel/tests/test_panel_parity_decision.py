"""/decision-refresh: enqueue is never an ACK; only a correlated owner publication succeeds, once."""
import hashlib
import importlib.util
import sqlite3
import sys
import time
from pathlib import Path

import pytest

core_runtime = pytest.importorskip('hermes_cli.serve_producer_runtime')
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]


def wait_until(predicate, timeout=60):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    raise AssertionError('condition was not met')


def load_api():
    name = '_exec_panel_decision_fixture'
    spec = importlib.util.spec_from_file_location(name, ROOT / 'dashboard' / 'plugin_api.py')
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_decision_refresh_pending_ack_once_and_canonical_conflict(tmp_path, monkeypatch):
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
        kanban_db.create_task(db, title='Decision work', session_id='chat')
    provider = ROOT / 'dashboard' / 'producer_provider.py'
    manifest = {'path': str(provider), 'sha256': hashlib.sha256(provider.read_bytes()).hexdigest()}
    relay = core_runtime.ProjectionRelay(manifest, home=tmp_path, runtime=tmp_path / 'runtime', generation='dec')
    monkeypatch.setattr(sys, 'argv', ['hermes', 'serve', '--ssh-session-token-file', '/slots/desktop-a/token'])
    api = load_api()
    monkeypatch.setattr(api, '_request_profile', lambda: api.LAUNCH_PROFILE)
    try:
        relay.start_for_fixture(ready_timeout=60)
        api.attach_external(relay)
        app = FastAPI()
        app.include_router(api.router)
        with TestClient(app) as client:
            wait_until(lambda: 'chat' in client.post('/changes', json={'sessions': ['chat']}).json().get('bundles', {}))
            first = client.post('/decision-refresh', json={'session': 'chat'})
            assert first.status_code == 503, 'an enqueue is not an acknowledgement'
            assert first.headers['retry-after'] == '1'
            seen = []

            def delivered():
                response = client.post('/decision-refresh', json={'session': 'chat'})
                seen.append(response.status_code)
                return response.status_code == 200
            wait_until(delivered)
            body = client.post('/decision-refresh', json={'session': 'chat'})
            # delivered once: the next request cannot reuse the earlier owner publication
            assert body.status_code == 503
            wait_until(lambda: client.post('/decision-refresh', json={'session': 'chat'}).status_code == 200)
            # canonical conflict semantics: an unwatched chat is the legacy 409, not a perpetual 503
            ghost = []

            def conflicted():
                response = client.post('/decision-refresh', json={'session': 'ghost'})
                if response.status_code == 409:
                    ghost.append(response.json())
                return bool(ghost)
            wait_until(conflicted)
            assert ghost[0] == {'detail': 'Session panel is not hydrated'}
    finally:
        api.detach_external(relay)
        assert relay.close(timeout=10)


def test_failed_refresh_revokes_the_old_output_and_ack(tmp_path):
    provider = tmp_path / 'flaky.py'
    provider.write_text('calls = []\n'
                        'def produce(payload):\n'
                        '    calls.append(1)\n'
                        '    if len(calls) > 1:\n'
                        '        raise RuntimeError("source failed")\n'
                        '    return {"n": len(calls)}\n')
    spec = {'path': str(provider), 'sha256': hashlib.sha256(provider.read_bytes()).hexdigest()}
    relay = core_runtime.ProjectionRelay(spec, home=tmp_path, runtime=tmp_path / 'runtime', generation='flaky')
    try:
        relay.start_for_fixture(ready_timeout=60)
        key = relay.submit({'value': 'k'})
        wait_until(lambda: relay.read(key) is not None)
        assert relay.acknowledged(key)
        relay.submit({'value': 'k'})  # a new refresh of the same key now fails
        wait_until(lambda: relay.pending_count == 0)
        assert relay.read(key) is None, 'a failed refresh cannot leave the old bytes acknowledged'
        assert not relay.acknowledged(key)
    finally:
        assert relay.close(timeout=10)
