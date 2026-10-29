"""Tool results are bounded, entity-scoped projections (<=32 KB) of a >=300-runner fleet."""
import json
from copy import deepcopy
from types import SimpleNamespace
import pytest
from plugins.runners import register, tool_view
from plugins.runners.collector import Collector
from plugins.runners.providers.base import ProviderResult
from plugins.runners.service import Service
from plugins.runners.store import Store
from test_collector import F, config

N = 300


def rid(i): return 'runner_%016x' % i


@pytest.fixture
def fleet(tmp_path):
    cfg = config(); cfg['features']['repair_sessions'] = True
    host = deepcopy(F['Host']); host['management'] = 'unknown'
    cfg['hosts'] = [host]; cfg['bindings'] = [deepcopy(F['RunnerBinding'])]
    rows = [dict(F['Runner'], id=rid(i), provider_runner_id=str(i), display_name='Fictional registration %d' % i,
                 host_id=host['id'] if i == 1 else None) for i in range(1, N + 1)]
    cfg['bindings'][0]['runner_id'] = rid(1)
    adapter = SimpleNamespace(enumerate=lambda scope: ProviderResult(tuple(rows), True, None, 1000, N))

    def probe(h):
        return dict(disk_free_bytes=1024**3, services={h['service_refs'][0]: 'failed'}, tools={})  # disk+service fail
    path = tmp_path / 'state.sqlite'
    with Store(path) as store:
        store.put('Runner', rows[0], at=900)
        Collector(store, cfg, adapter, clock=lambda: 1000, host_probe=probe).tick()
        for i in range(1, N + 1):  # noisy history for every runner
            for n in range(60 if i <= 3 else 1):
                store.put('Observation', dict(F['Observation'], id='obj_%016x' % (i * 1000 + n), entity_id=rid(i),
                          evidence_summary='x' * 200 + ' subject-%d' % i), at=1000 + n)
    svc = Service(path, cfg, clock=lambda: 1001)
    return cfg, svc


def tools(svc, monkeypatch, domain=None):
    from plugins.runners.dashboard import plugin_api as api
    monkeypatch.setattr(api, 'service', lambda: svc)
    monkeypatch.setattr(api, 'active_profile', lambda: svc.config['owner_profile'])
    if domain:
        monkeypatch.setattr(api, 'repair_domain', lambda _: domain)
        monkeypatch.setattr('plugins.runners.tool_principal', lambda *a, **k: 'oidc:example')
    out = {}
    register(SimpleNamespace(register_tool=lambda **kw: out.update({kw['name']: kw['handler']})))
    return out


def test_full_envelope_is_large(fleet):
    _, svc = fleet
    assert tool_view.size(svc.inspect(rid(1), 100)) > 32 * 1024  # the problem being fixed


def test_runners_inspect_bounded_and_scoped(fleet, monkeypatch):
    cfg, svc = fleet
    raw = tools(svc, monkeypatch)['runners_inspect'](dict(runner_id=rid(1), history_limit=100))
    assert len(raw.encode()) <= 32 * 1024
    r = json.loads(raw); d = r['data']
    assert d['runner']['id'] == rid(1)
    assert (d['runner']['registration'], d['runner']['activity'], d['runner']['readiness']) == ('online', 'idle', 'fail')
    assert 'service' in d['runner']['readiness_evidence']['failed'] and 'disk' in d['runner']['readiness_evidence']['failed']
    assert d['host']['id'] == cfg['hosts'][0]['id'] and d['service_refs'] == [cfg['bindings'][0]['service_ref']]
    assert d['observations'] and all(o['entity_id'] == rid(1) for o in d['observations'])
    assert r['truncated'] is True  # 60 x ~400 B x 100 exceeds the cap -> oldest dropped
    assert {p['source_id'] for p in r['meta']['provenance']} <= {rid(1), F['Scope']['id'], cfg['hosts'][0]['resource_id']}
    assert r['meta']['owner'] and r['meta']['freshness'] == 'fresh' and r['meta']['observed_at']
    assert rid(2) not in raw and 'subject-2 ' not in raw


def test_runners_inspect_small_history_not_truncated(fleet, monkeypatch):
    _, svc = fleet
    r = json.loads(tools(svc, monkeypatch)['runners_inspect'](dict(runner_id=rid(1), history_limit=3)))
    assert r['truncated'] is False and len(r['data']['observations']) == 3


def test_runners_inspect_drops_oldest_first(fleet, monkeypatch):
    _, svc = fleet
    r = json.loads(tools(svc, monkeypatch)['runners_inspect'](dict(runner_id=rid(1), history_limit=100)))
    kept = [o['id'] for o in r['data']['observations']]
    newest = [o['id'] for o in svc.inspect(rid(1), 100)['data']['observations']]
    assert kept == newest[:len(kept)] and len(kept) < len(newest)


def test_runners_list_bounded_with_failures_first(fleet, monkeypatch):
    _, svc = fleet
    raw = tools(svc, monkeypatch)['runners_list']({})
    assert len(raw.encode()) <= 32 * 1024
    r = json.loads(raw); rows = r['data']['runners']
    assert r['data']['total_runners'] == N and r['data']['counts']['registrations'] == N
    assert rows[0]['id'] == rid(1) and rows[0]['readiness'] == 'fail'
    assert 'service' in rows[0]['readiness_evidence']['failed']
    assert rows[0]['registration'] == 'online' and rows[0]['activity'] == 'idle'
    assert r['truncated'] is True and r['data']['returned_runners'] < N
    assert 'provenance' not in r['meta'] and sum(r['meta']['provenance_states'].values()) >= N
    assert not any('observation' in k for k in rows[0])


def test_runners_verify_bounded_and_scoped(fleet, monkeypatch):
    cfg, svc = fleet
    full_meta = svc.inventory()['meta']
    ack = dict(meta=full_meta, data=dict(id='collection_' + '0' * 16, state='pending', target_ids=[rid(1)]))
    domain = SimpleNamespace(verify=lambda args, **kw: ack)
    raw = tools(svc, monkeypatch, domain)['runners_verify'](dict(repair_id='x'), session_id='s')
    assert tool_view.size(ack) > 32 * 1024
    assert len(raw.encode()) <= 32 * 1024
    r = json.loads(raw)
    assert r['data']['target_ids'] == [rid(1)] and r['data']['state'] == 'pending'
    assert {p['source_id'] for p in r['meta']['provenance']} <= {rid(1), F['Scope']['id'], cfg['hosts'][0]['resource_id']}
    assert r['meta']['owner'] and r['meta']['freshness'] == 'fresh' and r['truncated'] is False
    assert rid(2) not in raw
