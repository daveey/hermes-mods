"""The hub owns every displayed count, including the expanded projections."""
import importlib.util
from pathlib import Path

import pytest


def load():
    spec = importlib.util.spec_from_file_location('lanes_view_api', Path(__file__).parents[1] / 'dashboard/plugin_api.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    # These unit harnesses inject projection/cursor functions in this process.
    # Actual child execution is exercised separately in test_projector.py.
    mod._projector_enabled = False
    return mod


def test_lane_view_counts_and_conservation(monkeypatch):
    api = load()
    monkeypatch.setattr(api, 'core', lambda: None)  # exercise the older-core projection seam
    cards = [dict(id=f't_{i}', lane='work', step=step, reason=reason, age_h=2,
                  source={'kind': 'user'}) for i, (step, reason) in enumerate([
                      ('running', 'run'), ('waiting', 'you'), ('ready', 'slot'), ('waiting', 'dep')])]
    monkeypatch.setattr(api, 'read', lambda hours: {'configured': True, 'lanes': {'work': {
        'running': 1, 'blocked_on_you': 1, 'open': 4, 'ready': 1, 'held': 0,
        'share': 10, 'target': 1, 'actual': .5}}})
    monkeypatch.setattr(api, 'flow', lambda window: {'configured': True, 'cards': cards, 'lanes': {'work': {'done': 3}}})
    view = api.lane_view(6, '6h', True)
    assert view['lanes']['work']['not_ready'] == 2
    assert view['totals']['open'] == 4
    assert view['totals']['running'] == 1
    cells = view['floor']['cells']
    assert sum(c['count'] for c in cells if c['step'] != 'done') == 4
    wait = next(c for c in cells if c['step'] == 'waiting')
    assert wait['count'] == 2 and wait['piling'] is True
    assert next(c for c in cells if c['step'] == 'done')['count'] == 3
    for graph in view['flow']['graphs'].values():
        assert graph['total'] == 4
        for layer in ('sources', 'lanes', 'sinks'):
            assert sum(n['count'] for n in graph[layer]) == 4
        for edges in graph['edges'].values():
            assert sum(e['count'] for e in edges) == 4
        assert next(n for n in graph['sinks'] if n['key'] == 'dam:you')['hint'] == 'answer 1 ask · oldest 2h'
    assert 'floor' not in api.lane_view(6, '6h', False)


def test_collapsed_view_carries_all_lanes_status_counts(monkeypatch):
    api = load()
    monkeypatch.setattr(api, 'core', lambda: None)
    reasons = ['run', 'you', 'time', 'review', 'gate', 'slot', 'quota', 'limit:usd_per_day', 'dep', 'dep',
               'triage', 'mystery']
    cards = [dict(id=f't_{i}', lane='a' if i % 2 else 'b', step='waiting', reason=r, age_h=1)
             for i, r in enumerate(reasons)]
    monkeypatch.setattr(api, 'read', lambda hours: {'configured': True, 'lanes': {
        'a': {'running': 1, 'blocked_on_you': 1, 'open': 6}, 'b': {'open': 6}}})
    monkeypatch.setattr(api, 'flow', lambda window: {'configured': True, 'cards': cards, 'lanes': {}})
    for expanded in (False, True):
        view = api.lane_view(6, '6h', expanded)
        assert view['status'] == {'you': 1, 'ext': 2, 'worker': 4, 'run': 1, 'dep': 2, 'triage': 1, 'other': 1}
        assert sum(view['status'].values()) == len(cards)
        strips = {n: view['lanes'][n]['strip'] for n in ('a', 'b')}
        assert sum(s['blocked'] for s in strips.values()) == view['status']['you']
        assert sum(s['running'] for s in strips.values()) == view['status']['run']
        assert sum(sum(s.values()) for s in strips.values()) == len(cards)
    collapsed = api.lane_view(6, '6h', False)
    assert 'cards' not in collapsed and 'floor' not in collapsed
    # Unavailable flow: no status rather than zeros that look real (a fresh module: views are cached).
    api = load()
    monkeypatch.setattr(api, 'core', lambda: None)
    monkeypatch.setattr(api, 'read', lambda hours: {'configured': True, 'lanes': {'a': {'open': 6}}})
    monkeypatch.setattr(api, 'flow', lambda window: {'configured': False, 'reason': 'x'})
    assert 'status' not in api.lane_view(6, '6h', False)


def test_get_flow_returns_the_same_expanded_view(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    api = load()
    view = {'configured': True, 'totals': {'open': 9}, 'floor': {'cells': []}, 'flow': {'graphs': {}}}
    monkeypatch.setattr(api, 'lane_view', lambda *args: view)
    app = FastAPI()
    app.include_router(api.router)
    with TestClient(app) as client:
        assert client.get('/v1/lanes?hours=6&window=6h&expanded=true').json() == view
        assert client.get('/v1/lanes/flow?window=6h').json() == view
