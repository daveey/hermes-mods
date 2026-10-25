from copy import deepcopy
import pytest
from plugins.runners.collector import Collector
from plugins.runners.service import Service
from plugins.runners.store import Store
from plugins.runners.providers.base import ProviderResult
from plugins.runners.models import validate
from test_collector import config,F,Adapter


def setup(tmp_path,probe):
    cfg=config(); host=deepcopy(F['Host']); host['management']='unknown'
    cfg['hosts']=[host]; cfg['bindings']=[deepcopy(F['RunnerBinding'])]
    path=tmp_path/'state.sqlite'; a=Adapter()
    # This seed represents an independently verified association, not provider data.
    row=deepcopy(F['Runner']); row['host_id']=host['id']
    with Store(path) as store: store.put('Runner',row,at=900)
    return cfg,path,a


@pytest.mark.parametrize('disk,service,expected',[(100,'running','pass'),(1,'running','fail'),(10,'running','warn'),(100,'failed','fail'),(100,'unknown','not_checked')])
def test_required_checks_derive_readiness(tmp_path,disk,service,expected):
    def probe(h): return dict(disk_free_bytes=disk*1024**3,services={h['service_refs'][0]:service},tools={})
    cfg,path,a=setup(tmp_path,probe)
    with Store(path) as store:
        Collector(store,cfg,a,clock=lambda:1000,host_probe=probe).tick()
        assert any(o['dimension']=='readiness' for o in store.list('Observation'))
    response=Service(path,cfg,clock=lambda:1001).inventory()
    validate('InventoryResponse',response)
    assert response['data']['runners'][0]['readiness']==expected
    assert response['data']['counts']['verified_ready']==int(expected=='pass')
    stale=Service(path,cfg,clock=lambda:1200).inventory()
    assert stale['data']['runners'][0]['readiness']=='not_checked'


def test_host_failures_prevent_incident_recovery(tmp_path):
    free=[1]; clock=[1000]
    def probe(h): return dict(disk_free_bytes=free[0]*1024**3,services={h['service_refs'][0]:'running'},tools={})
    cfg,path,a=setup(tmp_path,probe)
    with Store(path) as store:
        from dataclasses import replace
        store.save_health(F['Runner']['id'],replace(store.load_health(F['Runner']['id']),incident_open=True))
        c=Collector(store,cfg,a,clock=lambda:clock[0],host_probe=probe)
        for _ in range(2):
            a.result=ProviderResult((F['Runner'],),True,None,clock[0],1)
            c.tick(); clock[0]+=61
        assert store.load_health(F['Runner']['id']).incident_open
        free[0]=100
        for _ in range(2):
            a.result=ProviderResult((F['Runner'],),True,None,clock[0],1)
            c.tick(); clock[0]+=61
        assert not store.load_health(F['Runner']['id']).incident_open


def test_provider_cannot_assert_host_binding(tmp_path):
    cfg=config(); path=tmp_path/'state.sqlite'; a=Adapter()
    row=dict(F['Runner'],host_id=F['Host']['id'])
    a.result=ProviderResult((row,),True,None,1000,1)
    with Store(path) as store: Collector(store,cfg,a,clock=lambda:1000).tick()
    assert Service(path,cfg,clock=lambda:1001).inventory()['data']['runners'][0]['host_id'] is None
