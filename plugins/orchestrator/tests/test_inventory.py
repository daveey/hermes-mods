"""Cross-gateway inventory exercises the actual hubview and public status validator."""
import asyncio
import importlib.util
import json
import sqlite3
from types import SimpleNamespace

import pytest
from test_snapshot_and_stream import api, fixture_adapter, run


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def outcome(gateway, profile, sid, kind='running'):
    return {'status': {'status_version': 1, 'revision': 4, 'generation': 1,
        'identity': {'gateway': gateway, 'profile': profile, 'session_id': sid, 'owner_epoch': 'e'},
        'kind': kind, 'coverage': {'state': 'complete', 'reasons': []},
        'presentation': {'stripe': 'none', 'reason_codes': []}}}


@pytest.fixture
def real_producers(tmp_path, monkeypatch):
    root = api.ROOT
    support = load(root / 'hub-sync/tests/conftest.py', 'inventory_support')
    fake = load(root / 'hub-sync/tests/fake_hermes_hub.py', 'inventory_client')
    state = tmp_path / 'state'
    state.mkdir()
    support.build_db(state / 'hub.db', fake.SCHEMA)
    monkeypatch.setenv('HERMES_HUB_STATE', str(state))
    hv = load(root / 'hub-sync/hubview.py', 'inventory_hubview')
    monkeypatch.setattr(hv, 'load_client', lambda: fake)
    monkeypatch.setattr(hv, '_now', lambda: support.NOW)
    model = load(root / 'session-panel/model.py', 'inventory_model')
    sync = load(root / 'hub-sync/dashboard/plugin_api.py', 'inventory_sync_api')
    monkeypatch.setattr(sync, '_read', lambda kind, *args: hv.sessions(*args))
    monkeypatch.setattr(api, 'producer', lambda name: sync if name == 'hub-sync' else
                        SimpleNamespace(_load_feed=lambda: SimpleNamespace(_model=lambda: model)))
    return hv, state


def test_actual_hub_sync_inventory_and_owner_routes(real_producers):
    hv, state = real_producers
    db = sqlite3.connect(state / 'hub.db')
    for gateway, profile in [('mini', 'default'), ('cloud', 'alpha')]:
        raw = {'id': 'chat', 'profile': profile, 'title': gateway + ' title', 'activity': 1700000000,
               'outcome': outcome(gateway, profile, 'chat'), 'status': 'done'}
        db.execute('INSERT INTO replica VALUES (?,?,?,?,?,?,?)',
                   (gateway, 'sessions', profile + '/chat', 1, 1, json.dumps(raw), 1700000000000))
    db.commit()
    db.close()
    adapter = fixture_adapter()
    adapter.inventory = api.read_inventory
    panel = adapter.sessions()
    panel['bundles']['chat']['bundle'].update(gateway='mini', profile='default')
    adapter.requests = lambda _: {'chat': [{'requestId': 'ask', 'revision': 1, 'state': 'open',
        'owner': {'gatewayId': 'cloud', 'profile': 'alpha', 'storedSessionId': 'chat'}}]}
    result = run(adapter)
    rows = [r for r in result['sessions'] if r['session_id'] == 'chat']
    assert {r['key'] for r in rows} == {'mini/default/chat', 'cloud/alpha/chat'}
    assert {r['title'] for r in rows} == {'mini title', 'cloud title'}
    assert all(r['state'] == 'running' and 'mode' not in r['route'] for r in rows)
    request = next(r for r in result['human'] if r.get('request'))
    assert request['route']['gatewayId'] == 'cloud'
    assert request['route']['targetProfile'] == 'alpha'
    assert sum(r['key'] == 'cloud/alpha/chat' for r in result['sessions']) == 1
    # Historical booleans never manufacture a current running state.
    assert next(r for r in result['sessions'] if r['session_id'] == 's_mini')['state'] == 'unknown'
    assert result['totals']['running'] is None


@pytest.mark.parametrize('failure', [None, {'available': False}, {'warming': True},
    {'available': True, 'sessions': [], 'stale': True},
    {'available': True, 'sessions': [], 'partial': True}])
def test_global_unknown_local_fallback_and_recovery(real_producers, failure):
    adapter = fixture_adapter()
    adapter.inventory = lambda: {'available': True, 'sessions': []}
    healthy = run(adapter)
    adapter.inventory = lambda: failure
    missing = run(adapter)
    assert missing['sessions']  # local panel remains useful
    source = next(s for s in missing['sources'] if s['name'] == 'session-inventory')
    assert source['state'] == 'unknown' and 'global' in source['reason']
    assert missing['totals']['sessions'] is None
    assert missing['totals']['filtered']['running'] is None
    adapter.inventory = lambda: {'available': True, 'sessions': []}
    assert run(adapter)['cursor'] == healthy['cursor']


def test_inventory_read_is_bounded_and_uses_full_projection(monkeypatch):
    calls = []
    async def get_sessions(**kwargs):
        calls.append(kwargs)
        return {'available': True, 'sessions': []}
    monkeypatch.setattr(api, 'producer', lambda _: SimpleNamespace(get_sessions=get_sessions))
    assert asyncio.run(api.read_inventory())['available']
    assert calls == [dict(gateway=None, exclude=None, archived=0, limit=5000, projection=None)]
    async def slow(**kwargs):
        await asyncio.sleep(10)
    monkeypatch.setattr(api, 'INVENTORY_TIMEOUT', .001)
    monkeypatch.setattr(api, 'producer', lambda _: SimpleNamespace(get_sessions=slow))
    with pytest.raises(TimeoutError):
        asyncio.run(api.read_inventory())


def test_canonical_owner_coverage_and_staleness_fail_closed(real_producers):
    raw = {'id': 'same', 'gateway': 'mini', 'profile': 'default', 'running': True,
           'status': 'running', 'outcome': outcome('mini', 'default', 'same')}
    value = {'available': True, 'sessions': [raw]}
    assert api.canonical_inventory_rows(value)[0]['state'] == 'running'
    raw['outcome']['status']['identity']['gateway'] = 'foreign'
    assert api.canonical_inventory_rows(value)[0]['state'] == 'unknown'
    raw['outcome'] = outcome('mini', 'default', 'same')
    raw['outcome']['status']['coverage']['state'] = 'unknown'
    assert api.canonical_inventory_rows(value)[0]['state'] == 'unknown'
    raw['outcome'] = outcome('mini', 'default', 'same')
    raw['stale'] = True
    assert api.canonical_inventory_rows(value)[0]['state'] == 'unknown'
    assert not api.inventory_complete({'available': True, 'sessions': [{'id': 'ownerless'}]})
    assert not api.inventory_complete({'available': True, 'sessions': [raw] * api.INVENTORY_LIMIT})
