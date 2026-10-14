"""Synthetic schema fixtures, NOT populated live observations."""
import importlib
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def test_runpod_stopped_quote_and_worker():
    from providers.runpod import RunPodCollector
    calls = []
    def request(method, url, **kw):
        calls.append((method, url, kw))
        if method == 'POST':
            return {'data': {'myself': {'id': 'user'}}}
        return [dict(id='stopped', desiredStatus='EXITED', costPerHr='9', env={'SECRET':'dont-leak'},
                     lastStartedAt='2026-09-25 07:08:31.628 +0000 UTC'),
                dict(id='worker', desiredStatus='RUNNING', endpointId='ep', gpuCount=4,
                     costPerHr='3', adjustedCostPerHr='2', machine={'gpuTypeId':'H100'})]
    out = RunPodCollector(request=request).collect()
    assert out['status'] == 'ok'
    a, b = out['machines']
    assert a['hourly_rate'] is None and '9' in a['rate_note']
    assert a['started_at'] == '2026-09-25T07:08:31.628000+00:00'
    assert b['hourly_rate'] == '2' and b['currency'] == 'credits'
    assert b['resource_kind'] == 'serverless_worker'
    assert b['hardware'].startswith('4 × H100')
    assert 'dont-leak' not in json.dumps(out)
    assert calls[1][2]['params']['includeWorkers'] == 'true'


def test_prime_paging_aliases_zero_prices_and_secret_allowlist():
    from providers.primeintellect import PrimeIntellectCollector
    calls = []
    def request(method, url, **kw):
        p = kw.get('params', {}); calls.append((url, p))
        if url.endswith('/whoami'): return {'data': {'id':'u'}}
        if url.endswith('/teams'): return {'data':[{'id':'team'}], 'total_count':1}
        if url.endswith('/pods/'):
            offset = p['offset']
            return {'data':[{'id':str(offset), 'status':'STOPPED' if offset == 0 else 'PROVISIONING',
                'priceHr':'4', 'provisioningPriceHr':0, 'gpuName':'CPU_NODE', 'gpuCount':1,
                'environmentVars':{'TOKEN':'secret-test'}}], 'total_count':2}
        page = p['page']
        return {'sandboxes':[{'id':'sb'+str(page), 'cpuCores':2, 'memoryGB':4, 'secrets':['secret-test']}],
                'total':2, 'hasNext':page == 1}
    out = PrimeIntellectCollector(request=request).collect()
    assert out['status'] == 'ok'
    assert len(out['machines']) == 4
    assert [r['hourly_rate'] for r in out['machines'][:2]] == ['4','0']
    assert out['machines'][0]['hardware'].startswith('0 ×')
    assert all(r['started_at'] is None for r in out['machines'])
    assert 'secret-test' not in json.dumps(out)
    assert any(p.get('team_id') == 'team' for _,p in calls)


def test_modal_all_envs_both_versions_and_cross_kind_dedup():
    from providers.modal import ModalCollector
    calls = []
    async def rpc(name, **args):
        calls.append((name,args))
        if name == 'TokenInfoGet': return {'workspace_id':'ws','workspace_name':'demo','token_id':'secret-test'}
        if name == 'EnvironmentList': return {'items':[{'name':'main'},{'name':'other'}]}
        if name == 'TaskList':
            return {'tasks':[{'task_id':'container','started_at':10},{'task_id':'underlying','started_at':20}]} if args['environment_name']=='main' else {'tasks':[]}
        if name in ('SandboxList','SandboxListV2'):
            if args['before_timestamp'] or args['environment_name']=='other': return {'sandboxes':[]}
            return {'sandboxes':[{'id':'sb','created_at':20,'task_info':{'id':'underlying','started_at':20}, 'secrets':['secret-test']}]}
        raise AssertionError(name)
    out = ModalCollector(rpc=rpc).collect()
    assert out['status'] == 'ok'
    assert len(out['machines']) == 2
    assert {m['resource_kind'] for m in out['machines']} == {'sandbox','serverless_worker'}
    assert all(m['hourly_rate'] is None for m in out['machines'])
    assert 'secret-test' not in json.dumps(out)
    assert {a['environment_name'] for n,a in calls if n == 'TaskList'} == {'main','other'}
    assert {n for n,a in calls if n.startswith('Sandbox')} == {'SandboxList','SandboxListV2'}


def test_modal_requested_hardware_presence_and_zero_unknown():
    from providers.modal import sandbox_hardware
    assert sandbox_hardware({}) == 'unknown'
    assert sandbox_hardware({'resource_info': {'milli_cpu':{'value':0}}}) == 'unknown'
    text = sandbox_hardware({'resource_info': {'milli_cpu':{'value':2000}, 'memory_mb':{'value':4096}, 'gpu_type':'H100'}, 'task_info':{'gpu_config':{'count':2}}})
    assert '2 physical CPU cores' in text and '4 GiB' in text and '2 × H100' in text
    assert 'requested' in text


def test_runpod_graphql_and_http_failure_are_sanitized():
    from providers.runpod import RunPodCollector
    def request(method, url, **kw):
        if method == 'POST': return {'errors':[{'message':'secret-test'}]}
        raise RuntimeError('secret-test')
    out = RunPodCollector(request=request).collect()
    assert out['status'] == 'error' and out['error']
    assert 'secret-test' not in json.dumps(out)


def test_runpod_running_fallback_and_zero_quote():
    from providers.runpod import RunPodCollector
    def request(method, url, **kw):
        if method == 'POST': return {'data':{'myself':{'id':'u'}}}
        return [{'id':'p','desiredStatus':'RUNNING','costPerHr':'4','adjustedCostPerHr':None},
                {'id':'z','desiredStatus':'RUNNING','costPerHr':'4','adjustedCostPerHr':0}]
    assert [m['hourly_rate'] for m in RunPodCollector(request).collect()['machines']] == ['4','0']


def test_prime_repeated_page_partial_retains_rows():
    from providers.primeintellect import PrimeIntellectCollector
    def request(method, url, **kw):
        if url.endswith('/whoami'): return {'data':{'id':'u'}}
        if url.endswith('/teams'): return {'data':[], 'total_count':0}
        if url.endswith('/pods/'): return {'data':[{'id':'p','status':'ACTIVE','priceHr':1}], 'total_count':2}
        return {'sandboxes':[], 'total':0, 'has_next':False}
    out = PrimeIntellectCollector(request).collect()
    assert out['status'] == 'partial' and len(out['machines']) == 1
    assert 'progress' in out['error']


def test_prime_cap_and_contradictory_sandbox_total():
    from providers.primeintellect import PrimeIntellectCollector
    def request(method, url, **kw):
        if url.endswith('/whoami'): return {'data':{'id':'u'}}
        if url.endswith('/teams'): return {'data':[], 'total_count':0}
        if url.endswith('/pods/'): return {'data':[{'id':'p'}], 'total_count':2}
        return {'sandboxes':[{'id':'s'}], 'total':2, 'has_next':False}
    out = PrimeIntellectCollector(request, max_pages=1).collect()
    assert out['status'] == 'partial'
    assert 'cap' in out['error'] and 'count mismatch' in out['error']


def test_modal_v2_failure_is_partial_not_empty_success():
    from providers.modal import ModalCollector
    async def rpc(name, **args):
        if name == 'TokenInfoGet': return {'workspace_id':'ws'}
        if name == 'EnvironmentList': return {'items':[{'name':'main'}]}
        if name == 'TaskList': return {'tasks':[{'task_id':'t'}]}
        if name == 'SandboxListV2': raise ValueError('secret-test')
        return {'sandboxes':[]}
    out = ModalCollector(rpc).collect()
    assert out['status'] == 'partial' and len(out['machines']) == 1
    assert 'secret-test' not in json.dumps(out)


def test_factories_are_lazy_and_broker_cache_is_process_only(monkeypatch):
    from providers.runpod import RunPodCollector
    import providers.runpod as shared
    from types import SimpleNamespace
    calls = []
    def run(argv, **kw):
        calls.append((argv,kw))
        return SimpleNamespace(returncode=0, stdout=json.dumps({'provider':'runpod','machines':[], 'status':'ok'}))
    monkeypatch.setattr(shared.subprocess, 'run', run)
    import tempfile, pathlib
    cfg = pathlib.Path(tempfile.mkdtemp()) / 'cr.json'
    cfg.write_text(json.dumps({'broker': {'command': ['token-broker', 'exec', '--scope', 'user/{provider}', '--']}}))
    monkeypatch.setenv('COMPUTE_RENTALS_CONFIG', str(cfg))
    for name in ('runpod','modal','primeintellect'):
        assert callable(importlib.import_module('providers.'+name).build_collector())
    assert calls == []
    collector = RunPodCollector()
    assert collector.collect() == collector.collect()
    assert len(calls) == 1
    argv, kw = calls[0]
    assert argv[:4] == ['token-broker','exec','--scope','user/runpod']
    assert '--worker' in argv and kw['timeout'] == 70


def test_http_allowlist_rejects_mutation_before_network(monkeypatch):
    from providers.runpod import ReadHTTP
    import pytest
    import httpx
    def forbidden(*a, **kw):
        raise AssertionError('Network client must not be created')
    monkeypatch.setattr(httpx, 'Client', forbidden)
    request = ReadHTTP('secret-test', {('GET','https://rest.runpod.io/v1/pods')})
    with pytest.raises(ValueError): request('DELETE','https://rest.runpod.io/v1/pods')
    graphql = ReadHTTP('secret-test', {('POST','https://api.runpod.io/graphql')})
    with pytest.raises(ValueError): graphql('POST','https://api.runpod.io/graphql', json={'query':'mutation { createPod }'})


def test_modal_refresh_deadline_retains_known_tasks():
    from providers.modal import ModalCollector
    import asyncio
    async def rpc(name, **kw):
        if name == 'TokenInfoGet': return {'workspace_id':'ws'}
        if name == 'EnvironmentList': return {'items':[{'name':'main'}]}
        if name == 'TaskList': return {'tasks':[{'task_id':'t'}]}
        await asyncio.sleep(1)
        return {'sandboxes':[]}
    out = ModalCollector(rpc, refresh_timeout=.02).collect()
    assert out['status'] == 'partial' and len(out['machines']) == 1


