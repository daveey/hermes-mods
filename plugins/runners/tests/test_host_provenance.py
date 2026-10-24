from plugins.runners.collector import Collector
from plugins.runners.store import Store
from plugins.runners.providers.base import ProviderResult
from test_collector import F, Adapter, config
from test_host_health import setup


def test_error_path_obeys_configured_inventory_ttl(tmp_path):
    cfg = config(); cfg['thresholds']['inventory_stale_seconds'] = 300
    a = Adapter()
    with Store(tmp_path / 'state.sqlite') as store:
        Collector(store, cfg, a, clock=lambda: 1000).tick()
        a.result = ProviderResult((), False, 'forbidden', 1300, None)
        Collector(store, cfg, a, clock=lambda: 1500).tick()
        assert store.load_health(F['Runner']['id']).source_error == 'forbidden'


def test_host_management_evidence_durable_once_per_probe(tmp_path):
    def probe(host):
        return dict(disk_free_bytes=10**11, services={host['service_refs'][0]: 'running'}, tools={})
    cfg, path, a = setup(tmp_path, probe)
    with Store(path) as store:
        c = Collector(store, cfg, a, clock=lambda: 1000, host_probe=probe)
        c.tick(); c.tick()
        rows = [o for o in store.list('Observation') if o['dimension'] == 'management']
        assert len(rows) == 1
        assert rows[0]['entity_id'] == cfg['hosts'][0]['resource_id']
        assert rows[0]['value'] == 'reachable'
        assert rows[0]['id'] in c.host_results[cfg['hosts'][0]['id']]['observation_ids']


def test_unavailable_host_persists_valid_error_evidence(tmp_path):
    def probe(host):
        raise OSError('unavailable')
    cfg, path, a = setup(tmp_path, probe)
    with Store(path) as store:
        c = Collector(store, cfg, a, clock=lambda: 1000, host_probe=probe)
        c.tick()
        rows = [o for o in store.list('Observation') if o['dimension'] == 'management']
        assert len(rows) == 1
        assert rows[0]['value'] == 'unreachable'
        assert rows[0]['error_class'] == 'network'
