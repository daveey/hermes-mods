import json
from pathlib import Path
import pytest
from plugins.runners.config import normalize
from plugins.runners.store import Store
from plugins.runners.models import validate
from plugins.runners.providers.base import ProviderResult
from plugins.runners.collector import Collector, OwnerLock
from plugins.runners.service import Service

F = json.loads((Path(__file__).parents[1]/'contracts/fixtures.json').read_text())

def config():
    return normalize(dict(schema_version=1, owner_gateway_id=F['Owner']['gateway_id'], owner_profile='example', scopes=[F['Scope']]))

class Adapter:
    def __init__(self):
        self.calls = 0
        self.result = ProviderResult((F['Runner'],), True, None, 1000, 1)
    def enumerate(self, scope):
        self.calls += 1
        return self.result

def test_lock_and_owner(tmp_path):
    with pytest.raises(RuntimeError, match='owner_conflict'):
        with OwnerLock(tmp_path/'lock', config(), ('wrong','example',1)): pass
    with OwnerLock(tmp_path/'lock', config(), (F['Owner']['gateway_id'],'example',1)):
        with pytest.raises(RuntimeError, match='collector_running'):
            with OwnerLock(tmp_path/'lock', config(), (F['Owner']['gateway_id'],'example',1)): pass

def test_atomic_collection_restart_and_cached_read(tmp_path):
    path=tmp_path/'state.sqlite'; adapter=Adapter()
    with Store(path) as store:
        c=Collector(store, config(), adapter, clock=lambda:1000)
        c.tick()
        assert store.load_health(F['Runner']['id']).registration == 'online'
    service=Service(path, config(), clock=lambda:1001)
    response=service.inventory()
    validate('InventoryResponse',response)
    assert response['data']['counts']['online']==1
    assert adapter.calls==1
    assert Service(path,config(),clock=lambda:1201).inventory()['data']['runners'][0]['freshness']=='stale'
    with Store(path) as store:
        adapter.result=ProviderResult((),False,'auth',1031,None)
        Collector(store,config(),adapter,clock=lambda:1031).tick()
        assert store.load_health(F['Runner']['id']).registration=='online'
        assert store.load_health(F['Runner']['id']).source_error=='auth'
    result = Service(path,config(),clock=lambda:1032).inventory()
    assert result['data']['state']=='partial'
    provenance = next(p for p in result['meta']['provenance'] if p['source_id'] == F['Runner']['id'])
    from plugins.runners.collector import iso
    assert provenance['observed_at'] == iso(1000)
    assert provenance['observed_at'] != result['meta']['observed_at']
    assert provenance['state'] != 'fresh'
    validate('InventoryResponse', result)

def test_scope_error_persists_retry_eta_in_cached_api(tmp_path):
    path = tmp_path / 'state.sqlite'
    adapter = Adapter()
    adapter.result = ProviderResult((), False, 'forbidden', 1000, None, 60)
    with Store(path) as store:
        Collector(store, config(), adapter, clock=lambda: 1000).tick()
    response = Service(path, config(), clock=lambda: 1001).inventory()
    error = response['meta']['source_errors'][0]
    assert error['code'] == 'forbidden'
    assert error['retry_after_seconds'] == 60
    validate('InventoryResponse', response)


def test_hosts_once_concurrency_and_cache(tmp_path):
    from copy import deepcopy
    from threading import Lock
    import time
    cfg=config(); host=deepcopy(F['Host']); host['management']='unknown'
    cfg['hosts']=[host]
    active=[0,0,0]; lock=Lock()
    def probe(h):
        with lock: active[0]+=1; active[1]=max(active[1],active[0]); active[2]+=1
        time.sleep(.01)
        with lock: active[0]-=1
        return dict(services={},disk_free_bytes=10**11,tools={})
    with Store(tmp_path/'state.sqlite') as store:
        c=Collector(store,cfg,Adapter(),clock=lambda:1000,host_probe=probe)
        c.tick(); c.tick()
    assert active==[0,1,1]
    response=Service(tmp_path/'state.sqlite',cfg,clock=lambda:1001).inventory()
    assert response['data']['hosts'][0]['management']=='reachable'
    validate('InventoryResponse',response)


def test_refresh_singleflight_and_validation(tmp_path):
    path=tmp_path/'state.sqlite'
    with Store(path) as store: Collector(store, config(), Adapter(), clock=lambda:1000).tick()
    s=Service(path,config(),clock=lambda:1001)
    r=dict(target_ids=[F['Scope']['id']],idempotency_key='request_'+'a'*16)
    a=s.refresh(r); b=s.refresh(dict(r,idempotency_key='request_'+'b'*16))
    assert a['data']['id']==b['data']['id']
    validate('RefreshResponse',a)
    with pytest.raises(ValueError): s.refresh(dict(r,target_ids=['scope_'+'f'*16]))
    with Store(path) as store: Collector(store,config(),Adapter(),clock=lambda:1031).tick()
    assert s.refresh(r)['data']['state']=='complete'
