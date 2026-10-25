"""Bounded scheduling must not fabricate freshness or refresh completion."""
from copy import deepcopy
from plugins.runners.collector import Collector
from plugins.runners.service import Service
from plugins.runners.store import Store
from plugins.runners.providers.base import ProviderResult
from test_collector import config, F, Adapter


def fleet(n):
    cfg=config()
    cfg['scopes']=[dict(deepcopy(F['Scope']),id='scope_'+format(i,'032x')) for i in range(n)]
    return cfg


class Empty:
    def __init__(self,clock): self.clock,self.calls=clock,[]
    def enumerate(self,scope):
        self.calls.append(scope['id'])
        return ProviderResult((),True,None,self.clock(),0)


def test_bounded_fair_schedule_preserves_coverage_after_restart(tmp_path):
    cfg=fleet(37); clock=[1000]; a=Empty(lambda:clock[0]); path=tmp_path/'state.sqlite'
    with Store(path) as store:
        c=Collector(store,cfg,a,clock=lambda:clock[0],max_scopes=8)
        first=c.tick()
        assert len(a.calls)==8
        assert len(first['coverage'])==37
        assert first['collection']['state']=='partial'
        for _ in range(4):
            clock[0]+=1
            c=Collector(store,cfg,a,clock=lambda:clock[0],max_scopes=8)
            snap=c.tick()
        assert len(a.calls)==37 and len(set(a.calls))==37
        assert snap['collection']['state']=='complete'
        c.tick()
        assert len(a.calls)==37


def test_refresh_deferred_target_not_reported_complete(tmp_path):
    cfg=fleet(3); clock=[1000]; a=Empty(lambda:clock[0]); path=tmp_path/'state.sqlite'
    with Store(path) as store:
        c=Collector(store,cfg,a,clock=lambda:clock[0],max_scopes=1)
        c.tick()
        request=dict(target_ids=[cfg['scopes'][2]['id']],idempotency_key='request_'+'a'*16)
        s=Service(path,cfg,clock=lambda:clock[0])
        ticket=s.refresh(request)['data']['id']
        clock[0]+=1
        c.tick()
        assert a.calls[-1]==cfg['scopes'][2]['id']
        assert s.refresh(request)['data']['state']=='complete'
        assert s.inventory()['data']['state']=='partial'


def test_refresh_at_same_timestamp_forces_new_observation(tmp_path):
    cfg=config(); clock=[1000]; a=Empty(lambda:clock[0]); path=tmp_path/'state.sqlite'
    with Store(path) as store:
        c=Collector(store,cfg,a,clock=lambda:clock[0]); c.tick()
        s=Service(path,cfg,clock=lambda:clock[0])
        request=dict(target_ids=[cfg['scopes'][0]['id']],idempotency_key='request_'+'c'*16)
        s.refresh(request)
        c.tick()
        assert len(a.calls)==2
        assert s.refresh(request)['data']['state']=='complete'


def test_old_provenance_stales_while_owner_keeps_ticking(tmp_path):
    cfg=fleet(2); clock=[1000]; a=Empty(lambda:clock[0]); path=tmp_path/'state.sqlite'
    with Store(path) as store:
        c=Collector(store,cfg,a,clock=lambda:clock[0],max_scopes=1)
        c.tick()
        clock[0]=1201
        c.tick()
    result=Service(path,cfg,clock=lambda:1202).inventory()
    assert result['meta']['provenance'][0]['state']=='stale'
    assert result['data']['state']=='partial'


def test_config_revision_fences_cached_reads(tmp_path):
    cfg=config(); path=tmp_path/'state.sqlite'
    with Store(path) as store: Collector(store,cfg,Adapter(),clock=lambda:1000).tick()
    changed=deepcopy(cfg); changed['owner_epoch']+=1
    import pytest
    with pytest.raises(RuntimeError,match='owner_conflict'):
        Service(path,changed,clock=lambda:1001).inventory()
