"""External /status must not infer running:false from missing local PROCESS_FEED."""
import hashlib
import importlib.util
import os
from pathlib import Path
import sqlite3
import sys
import time

import pytest

core_runtime = pytest.importorskip('hermes_cli.serve_producer_runtime')
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]


def wait_until(predicate, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    raise AssertionError('condition was not met')


def load_api():
    name = '_exec_panel_status_fixture'
    spec = importlib.util.spec_from_file_location(name, ROOT / 'dashboard' / 'plugin_api.py')
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_external_status_is_not_false_when_child_feed_runs(tmp_path, monkeypatch):
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.delenv('HERMES_KANBAN_DB', raising=False)
    with sqlite3.connect(tmp_path / 'state.db') as db:
        db.execute('CREATE TABLE sessions(id TEXT PRIMARY KEY,parent_session_id TEXT,outcome_json TEXT)')
    provider = ROOT / 'dashboard' / 'producer_provider.py'
    manifest = {'path': str(provider), 'sha256': hashlib.sha256(provider.read_bytes()).hexdigest()}
    relay = core_runtime.ProjectionRelay(manifest, home=tmp_path, runtime=tmp_path / 'runtime', generation='status')
    monkeypatch.setattr(sys, 'argv', ['hermes', 'serve', '--ssh-session-token-file', '/slots/desktop-a/token'])
    api = load_api()
    try:
        relay.start_for_fixture(ready_timeout=60)
        monkeypatch.setattr(api._LOADER, 'external_projection', relay, raising=False)
        assert getattr(sys.modules.get('_session_panel_feed_core'), 'PROCESS_FEED', None) in (None,)
        app = FastAPI()
        app.include_router(api.router)
        with TestClient(app) as client:
            def ready():
                response = client.get('/status')
                return response.status_code == 200 and response.json().get('running') is True
            wait_until(ready, timeout=30)
            body = client.get('/status').json()
            assert body['running'] is True
            assert body['feed'] == api.FEED_ID
            assert 'epoch' in body
    finally:
        api._LOADER.external_projection = None
        assert relay.close(timeout=10)
