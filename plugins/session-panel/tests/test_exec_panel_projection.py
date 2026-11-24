"""Walking skeleton: real CoreReaders/Feed in exec child, memory-only HTTP."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import sys
import time

import pytest

core_runtime = pytest.importorskip('hermes_cli.serve_producer_runtime')
from fastapi import FastAPI
from fastapi.testclient import TestClient


def wait_until(predicate, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    raise AssertionError('condition was not met')

ROOT = Path(__file__).resolve().parents[1]


def load_api():
    name = '_exec_panel_endpoint_fixture'
    spec = importlib.util.spec_from_file_location(name, ROOT / 'dashboard' / 'plugin_api.py')
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize('slot,profile', [('desktop-a', 'default'), ('desktop-b', 'researcher')])
def test_real_panel_projection_served_without_readers_in_request_process(tmp_path, monkeypatch, slot, profile):
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
        tid = kanban_db.create_task(db, title='Real exec panel work', session_id='chat')
    provider = ROOT / 'dashboard' / 'producer_provider.py'
    manifest = {'path': str(provider), 'sha256': hashlib.sha256(provider.read_bytes()).hexdigest()}
    relay = core_runtime.ProjectionRelay(manifest, home=tmp_path, runtime=tmp_path / 'runtime', generation='panel-fixture')
    monkeypatch.setattr(sys, 'argv', ['hermes', 'serve', '--ssh-session-token-file', f'/slots/{slot}/token'])
    api = load_api()
    monkeypatch.setattr(api, 'LAUNCH_PROFILE', profile)
    monkeypatch.setattr(api, '_request_profile', lambda: profile)
    try:
        # READY includes two execs and cold core/provider imports. Shared CI
        # runners can exceed the runtime's 10-second fixture default here;
        # retain a finite startup bound without relaxing projection assertions.
        relay.start_for_fixture(ready_timeout=60)
        monkeypatch.setattr(api._LOADER, 'external_projection', relay, raising=False)
        monkeypatch.setattr(api, 'CoreReaders', lambda *a, **k: pytest.fail('request-side CoreReaders'))
        monkeypatch.setattr(api, 'start_publication', lambda: pytest.fail('request-side producer start'))
        monkeypatch.setattr(api, '_load_feed', lambda: pytest.fail('request-side producer import'))
        app = FastAPI()
        app.include_router(api.router)
        with TestClient(app) as client:
            body = {'sessions': ['chat'],
                    '_wire_identity': {'feed': 'untrusted:other-slot', 'profile': 'other'}}
            def ready():
                response = client.post('/changes', json=body)
                assert response.status_code in (200, 503)
                return response.status_code == 200
            wait_until(ready, timeout=30)
            response = client.post('/changes', json=body)
            assert response.status_code == 200
            wire = response.json()
            assert wire['feed'] == api.FEED_ID
            assert wire['feed'].endswith(f':serve-{slot}')
            assert wire['profile'] == profile
            assert {'epoch', 'rev', 'reset', 'bundles'} <= wire.keys()
            task = wire['bundles']['chat']['tasks'][0]
            assert task['id'] == tid
            assert task['title'] == 'Real exec panel work'
            assert wire['bundles']['chat']['model']
            with sqlite3.connect(tmp_path / 'runtime' / 'projection.sqlite3') as db:
                stored = db.execute('SELECT generation, wire FROM projection WHERE id=1').fetchone()
            assert stored[0] == 'panel-fixture'
            assert stored[1] == response.content
            assert json.loads(stored[1])['bundles']['chat']['tasks'][0]['id'] == tid
            assert relay.ping()['child_pid'] != os.getpid()
            assert relay.ping()['child_pid'] != relay.broker_pid
            assert 'gateway.run' not in sys.modules
    finally:
        api._LOADER.external_projection = None
        assert relay.close(timeout=10)


def test_provider_drains_legacy_visibility_queue_and_bounds_subscribers(tmp_path, monkeypatch):
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    monkeypatch.setenv('HOME', str(tmp_path))
    path = ROOT / 'dashboard' / 'producer_provider.py'
    spec = importlib.util.spec_from_file_location('_panel_visibility_provider', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    direct = module.open_provider({})
    wall = [1000.0]
    direct.feed.clock = lambda: wall[0]
    monkeypatch.setattr(direct.feed, 'tick', lambda: None)
    def call(subscriber):
        return direct({'body': {'sessions': [], 'subscriber': subscriber,
                               '_wire_identity': {'feed': 'fixture:serve', 'profile': 'default'}},
                       'accepted': time.monotonic()})
    try:
        for _ in range(300):
            call('one')
            assert direct.feed._panel_updates.empty()
        for i in range(255):
            call(str(i))
        with pytest.raises(RuntimeError, match='subscriber budget'):
            call('overflow')
        assert len(direct.feed._panel_subscribers) == 256
        wall[0] += 100
        call('new')
        assert set(direct.feed._panel_subscribers) == {'new'}
    finally:
        direct.close()
