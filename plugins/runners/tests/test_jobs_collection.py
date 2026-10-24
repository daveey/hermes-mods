from copy import deepcopy

from plugins.runners.collector import Collector
from plugins.runners.providers.base import ProviderResult
from plugins.runners.store import Store
from test_collector import Adapter, config, F


class Jobs(Adapter):
    def __init__(self):
        super().__init__()
        self.job_calls = []
    def enrich_jobs(self, scope, runners, *, allowed_repositories, recent_runner_ids):
        self.job_calls.append((scope['id'], allowed_repositories, recent_runner_ids))
        return ProviderResult((dict(repository='example/project', run_id='1', job_id='2', runner_id='3', status='completed', conclusion='failure'),), True, None, 1000, None)


def test_enrichment_is_opt_in_and_separate_from_health(tmp_path):
    adapter = Jobs()
    with Store(tmp_path/'state.sqlite') as store:
        c = Collector(store, config(), adapter, clock=lambda: 1000)
        assert c.tick().get('job_results', {}) == {}
        assert not adapter.job_calls
        c = Collector(store, config(), adapter, clock=lambda: 1031,
                      job_repositories={F['Scope']['id']: ['example/project']})
        snapshot = c.tick()
        assert len(adapter.job_calls) == 1
        assert snapshot['job_results'][F['Scope']['id']]['rows'][0]['conclusion'] == 'failure'
        assert store.load_health(F['Runner']['id']).registration == 'online'


def test_global_enrichment_bound_and_explicit_allowlist(tmp_path):
    cfg = config()
    cfg['scopes'] = [dict(F['Scope'], id='scope_'+format(i+1,'016x')) for i in range(8)]
    adapter = Jobs()
    allow = {s['id']: ['example/project'] for s in cfg['scopes']}
    with Store(tmp_path/'state.sqlite') as store:
        snapshot = Collector(store, cfg, adapter, clock=lambda: 1000,
                             job_repositories=allow, max_job_scopes=2).tick()
        assert len(adapter.job_calls) == 2
        assert len(snapshot['job_results']) == 2
        assert all(call[1] == ['example/project'] for call in adapter.job_calls)
