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
        assert view['status'] == {'you': 1, 'ext': 2, 'gate': 2, 'worker': 2, 'run': 1, 'dep': 2, 'triage': 1,
                                  'other': 1}
        assert sum(view['status'].values()) == len(cards)
        strips = {n: view['lanes'][n]['strip'] for n in ('a', 'b')}
        assert sum(s['blocked'] for s in strips.values()) == view['status']['you']
        assert sum(s['running'] for s in strips.values()) == view['status']['run']
        assert sum(s[k] for s in strips.values() for k in ('blocked', 'running', 'other')) == len(cards)
        for key in view['status']:  # per-lane status adds up to the all-lanes totals
            assert sum(view['lanes'][n]['status'][key] for n in ('a', 'b')) == view['status'][key]
    collapsed = api.lane_view(6, '6h', False)
    assert 'cards' not in collapsed and 'floor' not in collapsed
    # Unavailable flow: no status rather than zeros that look real (a fresh module: views are cached).
    api = load()
    monkeypatch.setattr(api, 'core', lambda: None)
    monkeypatch.setattr(api, 'read', lambda hours: {'configured': True, 'lanes': {'a': {'open': 6}}})
    monkeypatch.setattr(api, 'flow', lambda window: {'configured': False, 'reason': 'x'})
    assert 'status' not in api.lane_view(6, '6h', False)


def test_collapsed_view_maps_open_cards_to_their_sessions(monkeypatch):
    api = load()
    monkeypatch.setattr(api, 'core', lambda: None)
    cards = [dict(id='t_1', lane='a', reason='you', source={'kind': 'session', 'id': 's1'}),
             dict(id='t_2', lane='a', reason='run', source={'kind': 'session', 'id': 's1'}),
             dict(id='t_3', lane='b', reason='dep', source={'kind': 'session', 'id': 's1'}),
             dict(id='t_4', lane='b', reason='gate', source={'kind': 'session', 'id': 's2'}),
             dict(id='t_5', lane='a', reason='run', source={'kind': 'automation', 'id': 't_0'}),
             dict(id='t_6', lane='a', reason='run')]
    monkeypatch.setattr(api, 'read', lambda hours: {'configured': True, 'lanes': {'a': {'open': 4}, 'b': {'open': 2}}})
    monkeypatch.setattr(api, 'flow', lambda window: {'configured': True, 'cards': cards, 'lanes': {}})
    assert api.lane_view(6, '6h', False)['sessions'] == {
        's1': {'a': {'you': 1, 'run': 1}, 'b': {'dep': 1}}, 's2': {'b': {'gate': 1}}}


def test_view_names_each_gate_and_who_filed_its_unlinked_cards(monkeypatch):
    api = load()
    monkeypatch.setattr(api, 'core', lambda: None)
    cards = [dict(id='t_1', lane='hermes', reason='gate', source={'kind': 'automation', 'id': 'ci-sweeper'}),
             dict(id='t_2', lane='hermes', reason='gate', source={'kind': 'automation', 'id': 'ci-sweeper'}),
             dict(id='t_3', lane='hermes', reason='gate', source={'kind': 'session', 'id': 's1'}),
             dict(id='t_4', lane='hermes', reason='gate', source={'kind': 'user', 'id': ''}),
             dict(id='t_5', lane='bb', reason='limit:usd_per_day', source={'kind': 'session', 'id': 's2'}),
             dict(id='t_6', lane='bb', reason='slot')]
    monkeypatch.setattr(api, 'read', lambda hours: {'configured': True, 'lanes': {
        'hermes': {'open': 4, 'gated': True, 'admitted_today': 0, 'admit_per_day': 2}, 'bb': {'open': 2}}})
    monkeypatch.setattr(api, 'flow', lambda window: {'configured': True, 'cards': cards, 'lanes': {}})
    view = api.lane_view(6, '6h', False)
    assert view['status']['gate'] == 5 and view['status']['worker'] == 1
    assert view['gates'] == [
        {'lane': 'hermes', 'kind': 'lane_gate', 'count': 4, 'linked': 1, 'unlinked': {'ci-sweeper': 2, 'user': 1},
         'admitted_today': 0, 'admit_per_day': 2},
        {'lane': 'bb', 'kind': 'limit:usd_per_day', 'count': 1, 'linked': 1, 'unlinked': {}}]
    assert [g['lane'] for g in view['lanes']['bb']['gates']] == ['bb']
    assert sum(g['count'] for g in view['gates']) == view['status']['gate']


@pytest.mark.parametrize('maximum,target,running,cap,inside,over', [
    (2, .5, 7, 2, 2, 5), (None, .25, 7, 2, 2, 5),
    (2, .5, 0, 2, 0, 0), (0, .5, 7, 0, 0, 7), (None, .5, 0, 0, 0, 0)])
def test_strip_capacity_wait_and_tokens(maximum, target, running, cap, inside, over):
    project = load()._view_module().project
    cards = [dict(lane='work', reason=r) for r in ['run'] * running + ['you', 'dep', 'dep', 'slot', 'mystery']]
    snap = {'lanes': {'work': {'target': target}}}
    raw = {'cards': cards, 'lanes': {'work': {'limits': {'max_workers': maximum}}},
           'strip_tokens': {'work': [10] * 36}}
    for expanded in (False, True):
        # Expanded fixtures need the normal card identity/step metadata.
        for i, card in enumerate(cards):
            card.update(id=f't_{i}', step='waiting')
        strip = project(snap, raw, expanded)['lanes']['work']['strip']
        assert (strip['cap'], strip['in_share'], strip['over']) == (cap, inside, over)
        assert strip['wait'] == {'dep': 2, 'slot': 1, 'mystery': 1}
        assert sum(strip['wait'].values()) == strip['other'] == 4
        assert strip['tokens'] == [10] * 36
    old = project(snap, {'lanes': {}}, False)['lanes']['work']
    assert 'strip' not in old and 'status' not in old


def test_strip_token_buckets_deduplicate_and_match_usage_window():
    import json
    import sqlite3
    from contextlib import closing
    from types import SimpleNamespace
    api = load()
    pol = SimpleNamespace(lanes={'work': {}, 'idle': {}}, lane_of=lambda t: 'work')
    with closing(sqlite3.connect(':memory:')) as db:
        db.executescript('CREATE TABLE tasks(id TEXT,tenant TEXT); '
                         'CREATE TABLE task_events(task_id TEXT,run_id INTEGER,kind TEXT,created_at INTEGER,payload TEXT);')
        db.execute("INSERT INTO tasks VALUES ('t_1','mapped-tenant')")
        now = 100000
        def seed(run, stamp, tokens, kind='worker_result'):
            db.execute('INSERT INTO task_events VALUES (?,?,?,?,?)',
                       ('t_1', run, kind, stamp, json.dumps({'usage': {'input_tokens': tokens, 'output_tokens': 3}})))
        seed(1, now-21600, 900)  # cutoff excluded, as in core usage
        seed(2, now-21599, 10)
        seed(2, now-21598, 20)  # one task/run, maximum once
        seed(3, now-600, 30)
        seed(4, now, 40)
        seed(5, now-10, 900, 'heartbeat')
        series = api.token_series(db, pol, now=now)
        assert len(series['work']) == 36
        assert series['work'][0] == 23 and series['work'][35] == 76
        assert series['idle'] == [0] * 36
        # Exactly core usage()'s aggregate; a bucket query must not sum duplicate reports.
        total = db.execute("SELECT SUM(tokens) FROM (SELECT MAX("
            "COALESCE(json_extract(payload,'$.usage.input_tokens'),0) + "
            "COALESCE(json_extract(payload,'$.usage.output_tokens'),0)) AS tokens "
            "FROM task_events WHERE kind='worker_result' AND created_at > ? "
            "AND json_extract(payload,'$.usage') IS NOT NULL GROUP BY task_id,run_id)", (now-21600,)).fetchone()[0]
        assert sum(series['work']) == total == 99


def test_get_flow_returns_the_same_expanded_view(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    api = load()
    view = {'configured': True, 'totals': {'open': 9}, 'floor': {'cells': []}, 'flow': {'graphs': {}}}
    monkeypatch.setattr(api, '_lane_view', lambda *args: view)
    app = FastAPI()
    app.include_router(api.router)
    with TestClient(app) as client:
        first = client.get('/v1/lanes?hours=6&window=6h&expanded=true').json()
        second = client.get('/v1/lanes/flow?window=6h').json()
        assert first.pop('stale_seconds') >= 0.
        assert first == view
        assert second.pop('stale_seconds') >= 0.
        assert second == view
