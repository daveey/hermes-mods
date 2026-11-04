"""Collector reads every Runner (no 1000-row cap) and retires only per the Store's
absence-retirement policy. Fictional ids only; temporary databases.
"""
import json
from pathlib import Path

import pytest

F = json.loads((Path(__file__).parents[1] / 'contracts/fixtures.json').read_text())
SCOPE = F['Scope']['id']
HOST = '00000000-0000-4000-8000-00000000000b'


def _hex(n, width=16):
    return format(n, 'x').rjust(width, '0')


def config():
    from plugins.runners.config import normalize
    return normalize(dict(schema_version=1, owner_gateway_id=F['Owner']['gateway_id'],
                          owner_profile='example', scopes=[F['Scope']]))


def runner(n, **extra):
    row = dict(F['Runner'], id='runner_' + _hex(n), provider_runner_id=str(n + 1))
    row.update(extra)
    return row


class Adapter:
    """Lists `rows`; observed_at follows the injected clock so health advances."""
    def __init__(self, clock, rows=(), complete=True):
        self.clock, self.rows, self.complete = clock, list(rows), complete

    def enumerate(self, scope):
        from plugins.runners.providers.base import ProviderResult
        return ProviderResult(tuple(self.rows), self.complete, None if self.complete else 'partial',
                              self.clock[0], len(self.rows))


def _seed(store, rows, at):
    with store.transaction():
        for row in rows:
            store.put('Runner', row, at=at)


def _tick(store, clock, adapter, **kw):
    from plugins.runners.collector import Collector
    return Collector(store, config(), adapter, clock=lambda: clock[0], **kw).tick()


def test_runner_after_first_thousand_keys_is_in_inventory_and_keeps_host_id(tmp_path):
    from plugins.runners.service import Service
    from plugins.runners.store import Store
    path = tmp_path / 'state.sqlite'
    clock = [10_000.0]
    # 1001 registrations whose keys sort before the live one.
    others = [runner(n) for n in range(1001)]
    live = runner(0xffff, host_id=HOST)
    with Store(path) as store:
        _seed(store, others + [live], at=clock[0] - 10)
        listed = dict(live, host_id=None)  # the provider never supplies host_id
        snapshot = _tick(store, clock, Adapter(clock, [listed]))
        assert live['id'] in {r['id'] for r in snapshot['runners']}
        assert len(snapshot['runners']) == 1002
        assert store.get('Runner', live['id'])['host_id'] == HOST
        # Health advances for absent runners beyond the first 1000 keys too.
        assert store.load_health(others[-1]['id']).absent_cycles == 1
    inventory = Service(path, config(), clock=lambda: clock[0]).inventory()['data']
    row = next(r for r in inventory['runners'] if r['id'] == live['id'])
    assert row['host_id'] == HOST
    assert inventory['counts']['registrations'] == 1002


def _absent_ticks(store, clock, adapter, n=3, step=30):
    for _ in range(n):
        clock[0] += step
        _tick(store, clock, adapter)


def test_absent_unbound_runner_is_retired_after_grace_with_context_resolved(tmp_path):
    from plugins.runners.collector import RETIRE_AFTER
    from plugins.runners.store import Store
    clock = [100_000.0]
    gone, fresh, keep = runner(1), runner(2), runner(3)
    with Store(tmp_path / 'state.sqlite') as store:
        _seed(store, [gone], at=clock[0] - RETIRE_AFTER - 1)
        _seed(store, [fresh], at=clock[0] - 60)
        adapter = Adapter(clock, [keep])
        _tick(store, clock, adapter)  # mints investigation contexts for all three
        context = next(i for i in store.active('Incident') if i['entity_id'] == gone['id'])
        _absent_ticks(store, clock, adapter)
        assert store.get('Runner', gone['id']) is None
        assert store.tombstone(gone['id'])['provider_runner_id'] == gone['provider_runner_id']
        resolved = store.get('Incident', context['id'])
        assert resolved['resolved_at'] is not None
        # Health stays until the tombstone retention removes it.
        assert store.load_health(gone['id']).registration == 'missing'
        # Within the grace window and still-listed runners are kept.
        assert store.get('Runner', fresh['id']) is not None
        assert store.get('Runner', keep['id']) is not None
        snapshot = _tick(store, clock, adapter)
        assert gone['id'] not in {r['id'] for r in snapshot['runners']}
        assert gone['id'] not in snapshot['states']


def test_retirement_never_touches_bound_partial_or_busy_runners(tmp_path):
    from plugins.runners.collector import RETIRE_AFTER
    from plugins.runners.store import Store
    clock = [100_000.0]
    old = clock[0] - RETIRE_AFTER - 1
    bound, busy, partial = runner(1, host_id=HOST), runner(2), runner(3)
    with Store(tmp_path / 'state.sqlite') as store:
        _seed(store, [bound, busy], at=old)
        store.put('Job', dict(F['Job'], id='obj_' + _hex(9), assigned_runner_id=busy['id'], status='running'), at=old)
        _absent_ticks(store, clock, Adapter(clock, []), n=4)
        assert store.get('Runner', bound['id']) is not None
        assert store.get('Runner', busy['id']) is not None
        assert store.load_health(busy['id']).registration == 'missing'
    with Store(tmp_path / 'partial.sqlite') as store:
        _seed(store, [partial], at=old)
        _absent_ticks(store, clock, Adapter(clock, [], complete=False), n=4)
        assert store.get('Runner', partial['id']) is not None


def test_relisted_retired_identity_is_skipped_not_fatal(tmp_path):
    from plugins.runners.store import Store
    clock = [100_000.0]
    ghost, live = runner(1), runner(2)
    with Store(tmp_path / 'state.sqlite') as store:
        _seed(store, [ghost], at=1)
        store.retire_runner(ghost['id'], at=2)
        snapshot = _tick(store, clock, Adapter(clock, [ghost, live]))
        assert {r['id'] for r in snapshot['runners']} == {live['id']}
        assert store.get('Runner', ghost['id']) is None


def test_retire_after_is_validated():
    from plugins.runners.collector import Collector
    for bad in (0, -1, True, '3600'):
        with pytest.raises(ValueError):
            Collector(None, config(), None, retire_after=bad)
