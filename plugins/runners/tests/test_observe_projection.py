from copy import deepcopy
import pytest
from plugins.runners.collector import Collector, iso
from plugins.runners.service import Service
from plugins.runners.store import Store
from plugins.runners.models import validate
from test_collector import F, Adapter, config
from test_jobs_collection import Jobs


def test_host_provenance_uses_probe_not_heartbeat_and_includes_unbound(tmp_path):
    cfg = config(); cfg['hosts'] = [deepcopy(F['Host'])]
    path = tmp_path/'state.sqlite'
    with Store(path) as store:
        collector = Collector(store, cfg, Adapter(), clock=lambda: 1000,
                              host_probe=lambda host: {})
        collector.tick()
        collector.clock = lambda: 1030
        collector.tick()
    inv = Service(path, cfg, clock=lambda: 1031).inventory()
    p = next(p for p in inv['meta']['provenance'] if p['source_id'] == cfg['hosts'][0]['resource_id'])
    assert p['observed_at'] == iso(1000) != inv['meta']['observed_at']
    assert p['expires_at'] == iso(1180)
    assert p['state'] == 'fresh'
    assert p['observation_ids'] == inv['data']['hosts'][0]['observation_ids']
    validate('InventoryResponse', inv)
    stale = Service(path, cfg, clock=lambda: 1200).inventory()
    assert next(p for p in stale['meta']['provenance'] if p['source_id'] == cfg['hosts'][0]['resource_id'])['state'] == 'stale'


@pytest.mark.parametrize('configured,state,reason', [(False, 'not_configured', 'not_configured'), (True,'configured','bounded_query_complete')])
def test_job_monitoring_and_scope_runner_filtered_evidence(tmp_path, configured, state, reason):
    cfg=config(); path=tmp_path/'state.sqlite'; adapter=Jobs()
    adapter.result=adapter.result.__class__((dict(F['Runner'], provider_runner_id='3'),),True,None,1000,1)
    with Store(path) as store:
        Collector(store,cfg,adapter,clock=lambda:1000,
                  job_repositories={F['Scope']['id']:['example/project']} if configured else {}).tick()
    detail=Service(path,cfg,clock=lambda:1001).inspect(F['Runner']['id'])
    monitor=detail['data']['job_monitoring']
    assert (monitor['state'],monitor['reason_code']) == (state,reason)
    rows=detail['data']['observed_jobs']
    assert len(rows) == int(configured)
    if configured:
        assert rows[0]['runner_id']=='3' and rows[0]['observed_at']==iso(1000)
        assert rows[0]['url']=='https://github.com/example/project/actions/runs/1/job/2'
    assert detail['data']['jobs']==[]
    validate('InspectResponse',detail)


@pytest.mark.parametrize('error,complete,state', [('forbidden',False,'forbidden'),('network',False,'unavailable'),('enrichment_truncated',False,'configured'),(None,True,'configured')])
def test_monitoring_errors_and_staleness(tmp_path,error,complete,state):
    from plugins.runners.providers.base import ProviderResult
    cfg=config();path=tmp_path/'state.sqlite';adapter=Jobs()
    adapter.enrich_jobs=lambda *a,**kw:ProviderResult((),complete,error,1000,None)
    with Store(path) as store:
        Collector(store,cfg,adapter,clock=lambda:1000,job_repositories={F['Scope']['id']:['example/project']}).tick()
    svc=Service(path,cfg,clock=lambda:1001)
    assert svc.inspect(F['Runner']['id'])['data']['job_monitoring']['state']==state
    svc.clock=lambda:1201
    assert svc.inspect(F['Runner']['id'])['data']['job_monitoring']['state']=='unknown'


def test_job_cooldown_preserves_age_and_revocation_removes_evidence(tmp_path):
    from plugins.runners.providers.base import ProviderResult
    cfg=config(); path=tmp_path/'state.sqlite'; a=Jobs()
    allow={F['Scope']['id']:['example/project']}
    with Store(path) as store:
        c=Collector(store,cfg,a,clock=lambda:1000,job_repositories=allow)
        first=c.tick()['job_results']
        a.enrich_jobs=lambda *args,**kw:ProviderResult((),False,'enrichment_cooldown',1031,None)
        c.clock=lambda:1031
        assert c.tick()['job_results']==first
        # Explicit allowlist removal must not expose saved rows after restart.
        c=Collector(store,cfg,a,clock=lambda:1062,job_repositories={})
        assert c.tick()['job_results']=={}
    assert Service(path,cfg,clock=lambda:1063).inspect(F['Runner']['id'])['data']['job_monitoring']['state']=='not_configured'


def test_job_filter_limits_legacy_unknown_and_no_live_io(tmp_path):
    import json
    cfg=config();path=tmp_path/'state.sqlite';a=Jobs()
    a.result=a.result.__class__((dict(F['Runner'],provider_runner_id='3'),),True,None,1000,1)
    with Store(path) as store:
        c=Collector(store,cfg,a,clock=lambda:1000,job_repositories={F['Scope']['id']:['example/project']})
        snap=c.tick()
        base=snap['job_results'][F['Scope']['id']]['rows'][0]
        snap['job_results'][F['Scope']['id']]['rows']=[dict(base,runner_id='4'),base]
        with store.transaction():
            store.connection.execute("UPDATE collector_cache SET payload=? WHERE key='snapshot'",(json.dumps(snap),))
        svc=Service(path,cfg,clock=lambda:1001)
        assert len(svc.inspect(F['Runner']['id'],1)['data']['observed_jobs'])==1
        assert svc.inspect(F['Runner']['id'],0)['data']['observed_jobs']==[]
        snap.pop('job_monitoring_scopes')
        with store.transaction():
            store.connection.execute("UPDATE collector_cache SET payload=? WHERE key='snapshot'",(json.dumps(snap),))
        assert svc.inspect(F['Runner']['id'])['data']['job_monitoring']['state']=='unknown'
    assert len(a.job_calls)==1


def test_partial_allowlist_revocation_filters_cached_rows_during_cooldown(tmp_path):
    from plugins.runners.providers.base import ProviderResult
    cfg=config();path=tmp_path/'state.sqlite';a=Jobs();sid=F['Scope']['id']
    with Store(path) as store:
        Collector(store,cfg,a,clock=lambda:1000,job_repositories={sid:['example/project','example/other']}).tick()
        a.enrich_jobs=lambda *args,**kw:ProviderResult((),False,'enrichment_cooldown',1031,None)
        snap=Collector(store,cfg,a,clock=lambda:1031,job_repositories={sid:['example/other']}).tick()
        assert snap['job_results'][sid]['rows']==[]
