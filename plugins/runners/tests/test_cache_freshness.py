"""Inventory cache age is evidence age, never collector-attempt age."""
import pytest
from plugins.runners.collector import Collector, iso
from plugins.runners.models import validate
from plugins.runners.providers.base import ProviderResult
from plugins.runners.service import Service
from plugins.runners.store import Store
from test_collector import Adapter, config, F


@pytest.mark.parametrize('shared', [False, True])
def test_cache_age_advances_on_memo_reads_and_failure_preserves_fetch(tmp_path, shared):
    path = tmp_path / 'state.sqlite'
    adapter = Adapter()
    now = [1001]
    with Store(path) as store:
        Collector(store, config(), adapter, clock=lambda: 1000).tick()
    service = Service(path, config(), clock=lambda: now[0])
    first = service.inventory(shared=shared)
    row = first['meta']['inventory_cache'][0]
    assert row == dict(scope_id=F['Scope']['id'], fetched_at=iso(1000), age_s=1, incomplete=False)
    now[0] = 1011
    assert service.inventory(shared=shared)['meta']['inventory_cache'][0]['age_s'] == 11
    assert row['age_s'] == 1  # shared memo and old caller view were not mutated
    adapter.result = ProviderResult((), False, 'budget_deferred', 1031, None, 3600)
    with Store(path) as store:
        Collector(store, config(), adapter, clock=lambda: 1031).tick()
    now[0] = 1032
    result = service.inventory(shared=shared)
    assert result['meta']['inventory_cache'][0] == dict(
        scope_id=F['Scope']['id'], fetched_at=iso(1000), age_s=32, incomplete=True)
    validate('InventoryResponse', result)
    assert adapter.calls == 2


@pytest.mark.parametrize('shared', [False, True])
def test_memo_age_does_not_reparse_timestamps(tmp_path, monkeypatch, shared):
    from plugins.runners import service as module
    path = tmp_path / 'state.sqlite'
    with Store(path) as store:
        Collector(store, config(), Adapter(), clock=lambda: 1000).tick()
    response = Service(path, config(), clock=lambda: 1001).inventory(shared=shared)
    memo = module._Result(('test',), 1001, 1010, response)
    first = memo.view(shared, 1001)

    class NoParsing:
        @staticmethod
        def fromisoformat(value):
            pytest.fail('memo hit reparsed a fetched timestamp')

    monkeypatch.setattr(module, 'datetime', NoParsing)
    for at in (1002, 1003, 1004):
        result = memo.view(shared, at)
        assert result['meta']['inventory_cache'][0]['age_s'] == at - 1000
        validate('InventoryResponse', result)
    assert first['meta']['inventory_cache'][0]['age_s'] == 1


def test_cache_never_fetched_is_explicitly_incomplete(tmp_path):
    with Store(tmp_path / 'missing.sqlite') as store:
        Collector(store, config(), Adapter(), clock=lambda: 1000)
    response = Service(tmp_path / 'missing.sqlite', config(), clock=lambda: 1000).inventory()
    assert response['meta']['inventory_cache'] == [dict(
        scope_id=F['Scope']['id'], fetched_at=None, age_s=None, incomplete=True)]
