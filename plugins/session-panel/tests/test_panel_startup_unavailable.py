"""Selected external custody must never fall through to legacy HTTP paths before READY."""
import importlib.util
import sys
import types
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.delenv('HERMES_SERVE_PANEL_EXTERNAL', raising=False)
    monkeypatch.delenv('HERMES_SERVE_PRODUCERS_EXTERNAL', raising=False)
    for key in ('_session_panel_feed_core', '_session_panel_feed_loader'):
        monkeypatch.delitem(sys.modules, key, raising=False)
    name = '_panel_startup_unavailable_api'
    spec = importlib.util.spec_from_file_location(name, ROOT / 'dashboard' / 'plugin_api.py')
    mod = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, mod)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, '_request_profile', lambda: mod.LAUNCH_PROFILE)
    monkeypatch.setattr(mod._LOADER, 'external_projection', None, raising=False)
    return mod


@pytest.mark.parametrize('state', ['starting', 'draining', 'unavailable'])
def test_no_legacy_http_fallback_during_transition(api, monkeypatch, state):
    monkeypatch.setattr(api._LOADER, 'panel_lifecycle', types.SimpleNamespace(
        state=state, revision_applied=1, _legacy_running=False), raising=False)
    starts = []
    monkeypatch.setattr(api, 'start_publication', lambda: starts.append('legacy'))
    app = FastAPI()
    app.include_router(api.router)
    with TestClient(app) as client:
        responses = [client.post('/changes', json={'sessions': ['chat']}),
                     client.get('/status'),
                     client.post('/decision-refresh', json={'session': 'chat'})]
    assert [(r.status_code, r.headers.get('Retry-After')) for r in responses] == [(503, '1')] * 3
    assert starts == [], 'requests must not revive a legacy producer during external admission/drain'


def test_external_selection_before_first_watch_is_unavailable(api, monkeypatch):
    monkeypatch.setenv('HERMES_SERVE_PANEL_EXTERNAL', 'true')
    monkeypatch.setattr(api._LOADER, 'panel_lifecycle', types.SimpleNamespace(
        state='off', revision_applied=-1, _legacy_running=False), raising=False)
    starts = []
    monkeypatch.setattr(api, 'start_publication', lambda: starts.append('legacy'))
    app = FastAPI()
    app.include_router(api.router)
    with TestClient(app) as client:
        response = client.post('/changes', json={'sessions': ['chat']})
        status = client.get('/status')
    assert response.status_code == status.status_code == 503
    assert response.headers['Retry-After'] == status.headers['Retry-After'] == '1'
    assert starts == []


def test_completed_rollback_uses_legacy_even_with_original_on_environment(api, monkeypatch):
    monkeypatch.setenv('HERMES_SERVE_PANEL_EXTERNAL', 'true')
    monkeypatch.setattr(api._LOADER, 'panel_lifecycle', types.SimpleNamespace(
        state='off', revision_applied=2, _legacy_running=True), raising=False)
    app = FastAPI()
    app.include_router(api.router)
    with TestClient(app) as client:
        response = client.get('/status')
    assert response.status_code == 200
    assert response.json()['running'] is False  # truthful legacy schema, not a fabricated child status


@pytest.mark.parametrize(('stage', 'master', 'unavailable'), [
    ('true', None, True), ('true', '0', False), ('false', None, False), (None, None, False),
])
def test_before_lifecycle_install_uses_selection_only(api, monkeypatch, stage, master, unavailable):
    monkeypatch.setattr(api._LOADER, 'panel_lifecycle', None, raising=False)
    if stage is not None:
        monkeypatch.setenv('HERMES_SERVE_PANEL_EXTERNAL', stage)
    if master is not None:
        monkeypatch.setenv('HERMES_SERVE_PRODUCERS_EXTERNAL', master)
    assert api._legacy_http_unavailable() is unavailable


def test_off_before_external_boot_has_no_legacy_authority(api, monkeypatch):
    monkeypatch.setattr(api._LOADER, 'panel_lifecycle', types.SimpleNamespace(
        state='off', revision_applied=-1, _legacy_running=False), raising=False)
    assert api._legacy_http_unavailable() is True


def test_lifecycle_state_overrides_unchanged_environment(api, monkeypatch):
    monkeypatch.setattr(api._LOADER, 'panel_lifecycle', types.SimpleNamespace(
        state='unavailable', revision_applied=2, _legacy_running=True), raising=False)
    monkeypatch.setenv('HERMES_SERVE_PRODUCERS_EXTERNAL', '0')
    assert api._legacy_http_unavailable() is True
