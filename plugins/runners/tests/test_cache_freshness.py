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


@pytest.mark.parametrize('shared', [False, True])
def test_unchanged_scope_templates_carry_across_builds(tmp_path, shared):
    from plugins.runners import service as module
    path = tmp_path / 'state.sqlite'
    with Store(path) as store:
        Collector(store, config(), Adapter(), clock=lambda: 1000).tick()
    now = [1001]
    cfg = config()
    svc = Service(path, cfg, clock=lambda: now[0], fingerprint=Service.config_fingerprint(cfg))
    first = svc.inventory(shared=shared)
    original = module._RESULTS[str(path)].cache_epochs[0]
    # Force the ordinary memo ceiling, not a memo hit.
    now[0] = 1032
    second = svc.inventory(shared=shared)
    assert module._RESULTS[str(path)].cache_epochs[0] is original
    assert first['meta']['inventory_cache'][0]['age_s'] == 1
    assert second['meta']['inventory_cache'][0]['age_s'] == 32
    # A complete -> failed fetch preserves fetched_at but must replace the template.
    adapter = Adapter()
    adapter.result = ProviderResult((), False, 'budget_deferred', 1033, None, 3600)
    with Store(path) as store:
        Collector(store, config(), adapter, clock=lambda: 1033).tick()
    now[0] = 1034
    failed = svc.inventory(shared=shared)
    assert module._RESULTS[str(path)].cache_epochs[0] is not original
    assert failed['meta']['inventory_cache'][0]['incomplete'] is True
    assert failed['meta']['inventory_cache'][0]['fetched_at'] == iso(1000)
    assert first['meta']['inventory_cache'][0]['incomplete'] is False


def test_scope_template_carry_tracks_evidence_membership_and_clock():
    from plugins.runners.service import _scope_cache
    scopes = [{'id': 'a'}, {'id': 'b'}]
    snapshot = {'scope_results': {
        'a': {'complete': True, 'last_success_at': 1000},
        'b': {'complete': True, 'observed_at': 900}}}
    original = _scope_cache(scopes, snapshot, 1001)
    reordered = _scope_cache(list(reversed(scopes)), snapshot, 1002, original)
    assert reordered[0] is original[1]
    assert reordered[1] is original[0]
    snapshot['scope_results']['a']['last_success_at'] = 1003
    changed = _scope_cache(scopes, snapshot, 1004, original)
    assert changed[0] is not original[0]
    assert changed[1] is original[1]
    assert changed[0][0]['fetched_at'] == iso(1003)
    rolled_back = _scope_cache(scopes, snapshot, 1002, changed)
    assert rolled_back[0][0]['fetched_at'] is None
    assert rolled_back[0][0]['incomplete'] is True
    recovered = _scope_cache(scopes, snapshot, 1004, rolled_back)
    assert recovered[0][0]['fetched_at'] == iso(1003)
    removed = _scope_cache(scopes[:1], snapshot, 1004, recovered)
    assert len(removed) == 1 and removed[0] is recovered[0]
    assert original[0][0]['fetched_at'] == iso(1000)


def test_cache_never_fetched_is_explicitly_incomplete(tmp_path):
    with Store(tmp_path / 'missing.sqlite') as store:
        Collector(store, config(), Adapter(), clock=lambda: 1000)
    response = Service(tmp_path / 'missing.sqlite', config(), clock=lambda: 1000).inventory()
    assert response['meta']['inventory_cache'] == [dict(
        scope_id=F['Scope']['id'], fetched_at=None, age_s=None, incomplete=True)]
