"""GET /api/plugins/session-repair/findings through FastAPI: the cron tick's last scan, a chat filter, a fresh scan."""
import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from sr_helpers import PLUGIN, load


def client(monkeypatch, tmp_path):
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    api = load('_sr_routes_api', PLUGIN / 'dashboard' / 'plugin_api.py')
    monkeypatch.setattr(api, '_home', lambda: tmp_path)
    app = FastAPI()
    app.include_router(api.router, prefix='/api/plugins/session-repair')
    return api, TestClient(app)


def test_findings_serves_the_last_scan_and_filters_by_chat(monkeypatch, tmp_path):
    _, c = client(monkeypatch, tmp_path)
    empty = c.get('/api/plugins/session-repair/findings').json()
    assert empty['findings'] == [] and empty['source'] == 'none'
    (tmp_path / 'session-repair').mkdir()
    (tmp_path / 'session-repair' / 'last.json').write_text(json.dumps(
        {'scanned_at': 1.0, 'findings': [{'id': 'BS-stuck-run', 'chat': 'S1'}, {'id': 'BS-overdue-wake', 'chat': 'S2'}]}))
    got = c.get('/api/plugins/session-repair/findings').json()
    assert got['source'] == 'cron' and len(got['findings']) == 2 and got['age_s'] > 0
    only = c.get('/api/plugins/session-repair/findings', params={'chat': 'S2'}).json()
    assert [f['id'] for f in only['findings']] == ['BS-overdue-wake']


def test_fresh_runs_the_scanner_and_stores_nothing(monkeypatch, tmp_path):
    api, c = client(monkeypatch, tmp_path)
    calls = []

    class FakeScan:
        @staticmethod
        def scan():
            calls.append(1)
            return {'scanned_at': 2.0, 'findings': [{'id': 'BS-parent-done', 'chat': 'S1'}]}
    monkeypatch.setattr(api, '_load', lambda name, path: FakeScan)
    got = c.get('/api/plugins/session-repair/findings', params={'fresh': 1}).json()
    assert calls == [1] and got['source'] == 'fresh' and got['findings'][0]['id'] == 'BS-parent-done'
    assert not (tmp_path / 'session-repair' / 'last.json').exists(), 'a fresh read never stores or dedupes'


def test_routes_are_read_only_get():
    api = load('_sr_routes_api2', PLUGIN / 'dashboard' / 'plugin_api.py')
    assert {m for r in api.router.routes for m in r.methods} == {'GET'}
