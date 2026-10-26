"""Independent collector evidence only; no runtime/network credentials."""
import copy
import json

import pytest

from plugins.runners.collector import initialize, iso
from plugins.runners.repairs import RepairService
from plugins.runners.store import Store
from plugins.runners.models import validate
from test_repairs import env, core_types, reserve, PRINCIPAL


@pytest.fixture
def verified_env(env):
    e = env
    # Initialize the real collector storage before the repair domain schema.
    # Store v1 may coexist with the additive repair tables.
    e.store = Store(e.service.path)
    initialize(e.store)
    e.now = 1900000000
    e.service.clock = lambda: e.now
    e.service.config.update(bindings=[dict(runner_id=e.runner['id'], host_id=e.host['id'],
        service_ref=e.host['service_refs'][0], verification_observation_id='obs_0000000000000001')],
        thresholds=dict(inventory_stale_seconds=120, host_stale_seconds=180,
                        disk_warn_gib=20, disk_fail_gib=5), execution_targets=[], allowed_workflows=[])
    e.refresh_calls = []
    def refresh(request):
        e.refresh_calls.append(request)
        return {'data': {'id': 'collection_0000000000000099', 'state': 'pending',
                         'target_ids': request['target_ids']}}
    e.service.refresh = refresh
    e.repair = reserve(e)
    e.request_verify = dict(repair_id=e.repair['id'], expected_owner=e.request['expected_owner'],
                            idempotency_key='request_0000000000000010')
    yield e
    e.store.close()


def cycle(e, n, *, registration='online', management='reachable', service='running',
          tools=None, complete=True, host_at=None):
    at = e.now
    host_at = at if host_at is None else host_at
    row = copy.deepcopy(e.runner)
    row['registration'] = registration
    ids = []
    for index, entity, dimension, value, stamp, source in [
        (1, row['id'], 'registration', registration, at, row['scope_id']),
        (2, row['id'], 'readiness', 'pass', host_at, row['scope_id']),
        (3, e.host['resource_id'], 'management', management, host_at, e.host['resource_id'])]:
        oid = f'obs_{n:012x}{index:04x}'
        ids.append(oid)
        e.store.put('Observation', dict(id=oid, entity_id=entity, dimension=dimension,
            value=value, collected_at=iso(stamp), expires_at=iso(stamp+120),
            source_id=source, reason_code='provider_read' if index == 1 else 'fixed_host_probe',
            evidence_summary='Independent fixture collection', error_class=None), at=at)
    row['observation_ids'] = ids[:2]
    snap = dict(owner={k:e.service.config[k] for k in ('owner_gateway_id','owner_profile','owner_epoch','config_revision')},
        runners=[row], states={row['id']: dict(cycle=f'collection_{n:016x}', at=at, source_error=None)},
        host_results={e.host['id']: dict(at=host_at, management=management, observation_ids=ids[2:],
            facts=dict(disk_free_bytes=30*1024**3, services={e.host['service_refs'][0]:service},
                       tools={'compiler':'1.0'} if tools is None else tools))},
        scope_results={row['scope_id']: dict(complete=complete, error=None, observed_at=at,
            provenance=dict(collection_id=f'collection_{n:016x}'))})
    with e.store.transaction():
        e.store.connection.execute('INSERT OR REPLACE INTO collector_cache VALUES (?,?)', ('snapshot', json.dumps(snap)))
    return ids


def reconcile(e):
    return e.domain.reconcile_verification(e.repair['id'], principal=PRINCIPAL)


def test_two_distinct_fresh_cycles_persist_and_never_claim_build(verified_env):
    e = verified_env
    e.domain.verify(e.request_verify, principal=PRINCIPAL)
    e.now += 1
    first = cycle(e, 1)
    assert reconcile(e)['state'] == 'verifying'
    assert reconcile(e)['verification_observation_ids'] == first
    e.domain.close()
    e.domain = RepairService(e.service, core_factory=lambda profile:e.core)
    assert reconcile(e)['state'] == 'verifying'
    e.now += 30
    second = cycle(e, 2)
    result = reconcile(e)
    assert result['state'] == 'restored_unverified_build'
    assert result['verification_observation_ids'] == first + second
    validate('Repair', result)
    assert e.core.calls == 0


@pytest.mark.parametrize('change', [dict(registration='offline'), dict(management='unreachable'),
    dict(service='stopped'), dict(tools={'compiler':None}), dict(complete=False)])
def test_unhealthy_cycle_breaks_streak(verified_env, change):
    e = verified_env
    e.domain.verify(e.request_verify, principal=PRINCIPAL)
    e.now += 1
    cycle(e, 1); reconcile(e)
    e.now += 30
    cycle(e, 2, **change)
    assert reconcile(e)['verification_observation_ids'] == []
    e.now += 30
    cycle(e, 3)
    assert reconcile(e)['state'] == 'verifying'


def test_pre_request_and_reused_host_evidence_are_not_two_cycles(verified_env):
    e = verified_env
    cycle(e, 1)
    e.domain.verify(e.request_verify, principal=PRINCIPAL)
    assert reconcile(e)['verification_observation_ids'] == []
    e.now += 1
    cycle(e, 2); reconcile(e)
    host_at = e.now
    e.now += 30
    cycle(e, 3, host_at=host_at)
    assert reconcile(e)['state'] == 'verifying'


def test_expired_first_cycle_cannot_complete(verified_env):
    e = verified_env
    e.domain.verify(e.request_verify, principal=PRINCIPAL)
    e.now += 1
    cycle(e, 1); reconcile(e)
    e.now += 181
    ids = cycle(e, 2)
    result = reconcile(e)
    assert result['state'] == 'verifying'
    assert result['verification_observation_ids'] == ids


def test_verify_linkage_idempotency_and_authorization(verified_env):
    e = verified_env
    first = e.domain.verify(e.request_verify, principal=PRINCIPAL)
    e.domain.close()
    e.domain = RepairService(e.service, core_factory=lambda profile:e.core)
    assert e.domain.verify(e.request_verify, principal=PRINCIPAL) == first
    assert len(e.refresh_calls) == 1
    with e.domain._db() as db:
        row = db.execute('SELECT * FROM repair_domain_verification_requests').fetchone()
        assert row['repair_id'] == e.repair['id']
        assert row['collection_id'] == first['data']['id']
    with pytest.raises(PermissionError):
        e.domain.reconcile_verification(e.repair['id'], principal='other')
    with pytest.raises(ValueError):
        e.domain.verify({**e.request_verify, 'healthy': True}, principal=PRINCIPAL)


def test_missing_required_capability_blocks_even_with_readiness_pass(verified_env):
    e = verified_env
    e.service.config['execution_targets'] = [dict(runner_id=e.runner['id'], capability_ids=['cap_0000000000000001'])]
    e.domain.verify(e.request_verify, principal=PRINCIPAL)
    for n in (1, 2):
        e.now += 30
        cycle(e, n)
        assert reconcile(e)['state'] == 'verifying'
        assert reconcile(e)['verification_observation_ids'] == []


def capability(e, n, *, stamp=None):
    stamp = e.now if stamp is None else stamp
    cid, oid = 'cap_0000000000000001', f'obs_{n:012x}0004'
    e.service.config['execution_targets'] = [dict(runner_id=e.runner['id'], capability_ids=[cid])]
    e.store.put('Observation', dict(id=oid, entity_id=cid, dimension='capability', value='available',
        source_id=e.host['resource_id'], reason_code='fixed_host_probe', collected_at=iso(stamp),
        expires_at=iso(stamp+120), evidence_summary='Independent compiler check', error_class=None), at=e.now)
    e.store.put('Capability', dict(id=cid, requirement='compiler', declared_version=None,
        observed_version='1.0', state='available', observed_at=iso(stamp), expires_at=iso(stamp+120),
        verifier=e.host['resource_id'], observation_ids=[oid]), at=e.now)
    return oid


def test_capabilities_need_new_independent_evidence_in_both_cycles(verified_env):
    e = verified_env
    e.domain.verify(e.request_verify, principal=PRINCIPAL)
    e.now += 1
    cycle(e, 1); first = capability(e, 1)
    assert first in reconcile(e)['verification_observation_ids']
    e.now += 30
    cycle(e, 2)
    assert reconcile(e)['state'] == 'verifying'  # cached capability is not a second check
    second = capability(e, 2)
    result = reconcile(e)
    assert result['state'] == 'restored_unverified_build'
    assert first in result['verification_observation_ids']
    assert second in result['verification_observation_ids']


@pytest.mark.parametrize('mutation', ['missing', 'wrong_entity', 'wrong_source', 'error', 'future', 'expired'])
def test_observations_must_exist_match_and_be_fresh(verified_env, mutation):
    e = verified_env
    e.domain.verify(e.request_verify, principal=PRINCIPAL)
    e.now += 1
    ids = cycle(e, 1)
    o = e.store.get('Observation', ids[0])
    if mutation == 'missing':
        e.store.connection.execute("DELETE FROM runner_records WHERE kind='Observation' AND record_key=?", (o['id'],))
    else:
        if mutation == 'wrong_entity': o['entity_id'] = 'runner_0000000000000002'
        if mutation == 'wrong_source': o['source_id'] = 'scope_0000000000000002'
        if mutation == 'error': o['error_class'] = 'partial'
        if mutation == 'future': o['collected_at'] = iso(e.now+1)
        if mutation == 'expired': o['expires_at'] = iso(e.now)
        e.store.put('Observation', o, at=e.now)
    assert reconcile(e)['verification_observation_ids'] == []


def test_refresh_failure_retains_intent_and_retry_key(verified_env):
    e = verified_env
    refresh = e.service.refresh
    def fail(request):
        raise ConnectionError('lost acknowledgement')
    e.service.refresh = fail
    with pytest.raises(ConnectionError):
        e.domain.verify(e.request_verify, principal=PRINCIPAL)
    e.service.refresh = refresh
    e.now += 1
    cycle(e, 1)
    assert reconcile(e)['state'] == 'verifying'
    result = e.domain.verify(e.request_verify, principal=PRINCIPAL)
    assert result['data']['state'] == 'pending'
    assert len(e.refresh_calls) == 1
    assert e.refresh_calls[0]['idempotency_key'] == e.request_verify['idempotency_key']


def test_terminal_repair_and_owner_conflict_are_not_promoted(verified_env):
    e = verified_env
    e.domain.verify(e.request_verify, principal=PRINCIPAL)
    e.now += 1
    cycle(e, 1); reconcile(e)
    e.now += 30
    cycle(e, 2)
    e.service.config['owner_epoch'] += 1
    with pytest.raises(ValueError, match='owner_conflict'):
        reconcile(e)
    e.service.config['owner_epoch'] -= 1
    with e.domain._db(transaction=True) as db:
        repair = e.domain.get(e.repair['id'], principal=PRINCIPAL)
        repair['state'] = 'closed'
        db.execute('UPDATE repair_domain_records SET active=0,payload=? WHERE id=?',
                   (json.dumps(repair), repair['id']))
    assert reconcile(e)['state'] == 'closed'


def test_relabelled_observations_in_same_cycle_do_not_count_twice(verified_env):
    e = verified_env
    e.domain.verify(e.request_verify, principal=PRINCIPAL)
    e.now += 1
    first = cycle(e, 1); reconcile(e)
    e.now += 30
    cycle(e, 1)
    assert reconcile(e)['state'] == 'verifying'
    assert reconcile(e)['verification_observation_ids'] == first


def test_host_target_and_concurrent_reads_consume_cycle_once(verified_env):
    from concurrent.futures import ThreadPoolExecutor
    e = verified_env
    # Use the host target on a new physical reservation fixture.
    with e.domain._db(transaction=True) as db:
        db.execute('DELETE FROM repair_domain_aliases')
        db.execute('DELETE FROM repair_domain_records')
    from test_repairs import F
    e.store.put('Incident', {**F['Incident'], 'id': e.request['incident_id'],
        'entity_id': e.host['resource_id'], 'resolved_at': None}, at=e.now)
    e.repair = reserve(e, entity_id=e.host['resource_id'])
    e.request_verify['repair_id'] = e.repair['id']
    e.domain.verify(e.request_verify, principal=PRINCIPAL)
    e.now += 1
    first = cycle(e, 1)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: reconcile(e), range(8)))
    assert all(r['state'] == 'verifying' and r['verification_observation_ids'] == first for r in results)
    e.now += 30
    cycle(e, 2)
    assert reconcile(e)['state'] == 'restored_unverified_build'


def test_real_collector_refresh_and_reconcile(tmp_path):
    from test_host_health import setup
    from test_repairs import F, FakeCore
    from plugins.runners.collector import Collector
    from plugins.runners.providers.base import ProviderResult
    from plugins.runners.service import Service

    def probe(h):
        return dict(disk_free_bytes=30*1024**3, services={h['service_refs'][0]:'running'}, tools={})
    cfg, path, adapter = setup(tmp_path, probe)
    cfg['features']['repair_sessions'] = True
    clock = [1000.1234567]
    service = Service(path, cfg, clock=lambda:clock[0])
    with Store(path) as store:
        collector = Collector(store, cfg, adapter, clock=lambda:clock[0], host_probe=probe)
        adapter.result = ProviderResult((F['Runner'],), True, None, clock[0], 1)
        collector.tick()
        core = FakeCore()
        domain = RepairService(service, core_factory=lambda profile:core)
        collector.repair_service = domain
        owner = service.inventory()['meta']['owner']
        row = domain.reserve(dict(entity_id=F['Runner']['id'], incident_id=service.inspect(F['Runner']['id'])['data']['incidents'][0]['id'],
            idempotency_key='request_0000000000000001', mode='diagnose', expected_owner=owner),
            F['Requester'], principal=PRINCIPAL)
        response = domain.verify(dict(repair_id=row['id'], expected_owner=owner,
            idempotency_key='request_0000000000000002'), principal=PRINCIPAL)
        validate('RefreshResponse', response)
        for expected in ['verifying', 'restored_unverified_build']:
            clock[0] += 31
            adapter.result = ProviderResult((F['Runner'],), True, None, clock[0], 1)
            collector.tick()
            result = domain.get(row['id'], principal=PRINCIPAL)
            assert result['state'] == expected
        assert len(result['verification_observation_ids']) == 6
        assert all(store.get('Observation', oid) for oid in result['verification_observation_ids'])
        assert domain.verification_targets() == []
        assert core.calls == 0
        domain.close()


def test_verification_force_window_is_bounded_and_explicit_retry_reopens(verified_env):
    e = verified_env
    e.domain.verify(e.request_verify, principal=PRINCIPAL)
    assert e.domain.verification_targets() == [e.runner['id']]
    e.now += 301
    assert e.domain.verification_targets() == []
    e.domain.verify({**e.request_verify, 'idempotency_key':'request_0000000000000011'}, principal=PRINCIPAL)
    assert e.domain.verification_targets() == [e.runner['id']]
