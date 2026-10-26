import json
from pathlib import Path
import pytest
from plugins.runners.config import normalize
from plugins.runners.store import Store
from plugins.runners.providers.base import ProviderResult
from plugins.runners.collector import Collector
from plugins.runners.service import Service

F = json.loads((Path(__file__).parents[1]/'contracts/fixtures.json').read_text())


def config(revision=1, gateway=None):
    c = normalize(dict(schema_version=1, owner_gateway_id=gateway or F['Owner']['gateway_id'],
                       owner_profile='example', scopes=[F['Scope']]))
    c['config_revision'] = revision
    return c


class Adapter:
    def __init__(self):
        self.calls = 0
        self.result = ProviderResult((F['Runner'],), True, None, 1000, 1)

    def enumerate(self, scope):
        self.calls += 1
        return self.result


def test_config_revision_bump_discards_stale_snapshot_instead_of_crash_looping(tmp_path):
    # Any CAS config edit (e.g. enabling repair_sessions) bumps config_revision.
    # The collector must restart onto the new revision, not refuse forever on
    # its own previous-revision snapshot; the stale snapshot is never served.
    path = tmp_path / 'state.sqlite'
    with Store(path) as store:
        Collector(store, config(1), Adapter(), clock=lambda: 1000).tick()
    with pytest.raises(RuntimeError, match='owner_conflict'):
        Service(path, config(2), clock=lambda: 1001).inventory()
    with Store(path) as store:
        collector = Collector(store, config(2), Adapter(), clock=lambda: 1030)
        assert collector.host_results == {} and collector.scope_results == {}
        collector.tick()
    response = Service(path, config(2), clock=lambda: 1031).inventory()
    assert response['meta']['owner']['config_revision'] == 2
    assert response['data']['counts']['online'] == 1


def test_foreign_owner_snapshot_still_fails_closed(tmp_path):
    path = tmp_path / 'state.sqlite'
    with Store(path) as store:
        Collector(store, config(1), Adapter(), clock=lambda: 1000).tick()
    with Store(path) as store:
        with pytest.raises(RuntimeError, match='owner_conflict'):
            Collector(store, config(1, gateway='00000000-0000-4000-8000-000000000000'), Adapter(), clock=lambda: 1030)
        with pytest.raises(RuntimeError, match='owner_conflict'):
            stale = config(1)
            stale['owner_epoch'] = 2
            Collector(store, stale, Adapter(), clock=lambda: 1030)


def test_older_writer_refuses_newer_revision_snapshot(tmp_path):
    path = tmp_path / 'state.sqlite'
    with Store(path) as store:
        Collector(store, config(3), Adapter(), clock=lambda: 1000).tick()
    with Store(path) as store:
        with pytest.raises(RuntimeError, match='owner_conflict'):
            Collector(store, config(2), Adapter(), clock=lambda: 1030)
