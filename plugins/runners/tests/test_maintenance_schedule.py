from copy import deepcopy
from collections import Counter
from plugins.runners.providers.base import Credential, HTTPResponse
from plugins.runners.providers.maintenance import MaintenanceGitHub
from plugins.runners.scripts.reconcile_scopes import reconcile
from plugins.runners.scripts.maintenance_schedule import MaintenanceSchedule
from plugins.runners.tests.test_config import BASE
from plugins.runners.tests.test_github import scope
from plugins.runners.config import normalize


def fixture(count=552):
    config = normalize(deepcopy(BASE))
    config['scopes'] = [dict(scope(), id=f'scope_{i:016x}', name=f'example/r{i}') for i in range(count)]
    return config


def test_counted_24h_shared_budget_rotation_with_discovery_and_restart():
    config = fixture()
    calls, counts, state, schedule = [], [], {}, {}
    for hour in range(24):
        now = 100000 + hour * 3600
        def wire(method, url, **kwargs):
            calls.append((hour, url))
            if '/orgs/' in url:
                return HTTPResponse(200, {}, [])
            return HTTPResponse(200, {}, {'id': 1, 'full_name': url.split('/repos/')[1]})
        api = MaintenanceGitHub(transport=wire, credential_resolver=lambda _: Credential('shared', 'token'), clock=lambda: now)
        api.restore_state(state)
        before = len(calls)
        _, report = reconcile(config, {}, wire, api.credential_resolver, adapter=api,
                              schedule=schedule, now=now, discover_org=[('example', 'test')])
        counts.append(len(calls) - before)
        state = api.export_state()
        assert not report['errors']
    repos = Counter(url for _, url in calls if '/repos/' in url)
    assert len(repos) == 552
    assert max(counts) <= 32
    assert len(calls) <= 768  # old four six-hour full passes: 2208
    assert max(repos.values()) <= 2
    first = {}
    for hour, url in calls:
        first.setdefault(url, hour)
    assert max(first.values()) <= 18
    print({'requests': len(calls), 'repositories': len(repos), 'per_hour': counts,
           'max_first_visit_hour': max(first.values())})


def test_schedule_identity_and_revision():
    config = fixture(1)
    runtime = {'credentials': {'test': {'key': 'a'}}}
    item = dict(config['scopes'][0], credential_ref='test')
    state = {}
    s = MaintenanceSchedule(config, runtime, state, 100000)
    key = s.key('repository', item); s.attempted(key)
    config['config_revision'] += 1
    assert MaintenanceSchedule(config, runtime, state, 100001).key('repository', item) == key
    assert not s.due(key)
    runtime['credentials']['test']['key'] = 'b'
    assert MaintenanceSchedule(config, runtime, state, 100001).key('repository', item) != key
    assert s.key('repository', dict(item, name='example/renamed')) != key


def test_pacing_skip_preserves_evidence_and_does_not_sleep():
    config = fixture(1)
    evidence = {'scope_0000000000000000': {'at': 99999, 'sentinel': True}}
    api = MaintenanceGitHub(transport=lambda *a, **kw: None, credential_resolver=lambda _: Credential('shared', 'token'), clock=lambda: 100000)
    api.restore_state({'shared': {'next_at': 100031}})
    before = deepcopy(evidence)
    _, report = reconcile(config, {}, api.transport, api.credential_resolver, adapter=api,
                          evidence=evidence, schedule={}, now=100000,
                          pause=lambda _: (_ for _ in ()).throw(AssertionError('sleep')))
    assert evidence == before
    assert report['errors'] == [{'id': 'scope_0000000000000000', 'error': 'pacing'}]


def test_cached_404_preserves_proof_without_counting_as_new_confirmation():
    config = fixture(1)
    now = 100000
    calls = []
    def wire(method, url, **kwargs):
        calls.append(url)
        return HTTPResponse(404, {}, {}) if '/repos/' in url else HTTPResponse(200, {}, [])
    api = MaintenanceGitHub(transport=wire, credential_resolver=lambda _: Credential('shared', 'token'), clock=lambda: now)
    evidence = {}
    reconcile(config, {}, wire, api.credential_resolver, adapter=api, evidence=evidence, schedule={}, now=now)
    before = deepcopy(evidence)
    _, report = reconcile(config, {}, wire, api.credential_resolver, adapter=api, evidence=evidence, schedule={}, now=now)
    assert evidence == before
    assert len(calls) == 2
    assert report['removals'] == []
    assert report['errors'][0]['error'] == 'not_found'


def test_many_pacing_refusals_are_grouped_and_other_credential_progresses():
    config = fixture(40)
    config['scopes'][-1]['credential_ref'] = 'cred_' + '2' * 16
    calls = []
    def wire(method, url, **kwargs):
        calls.append(url)
        return HTTPResponse(200, {}, {'id': 1, 'full_name': url.split('/repos/')[1]})
    api = MaintenanceGitHub(transport=wire, credential_resolver=lambda ref: Credential(ref, 'token'), clock=lambda: 100000)
    api.restore_state({config['scopes'][0]['credential_ref']: {'next_at': 100031}})
    _, report = reconcile(config, {}, wire, api.credential_resolver, adapter=api, schedule={}, now=100000)
    assert len(calls) == 1
    assert len(report['errors']) == 1
    assert report['maintenance']['deferred'] == 39


def test_oversized_listing_yields_to_other_work_next_hour():
    config = fixture(1)
    now = [100000]
    calls, schedule = [], {}
    s = MaintenanceSchedule(config, {}, schedule, now[0])
    # Make the discovery first and pending, exercising priority as well as age.
    key = s.key('discovery', {'name': 'example', 'credential_ref': 'test'})
    schedule[key] = {'at': 0, 'pending': True}
    def wire(method, url, **kwargs):
        calls.append(url)
        if '/orgs/' in url:
            page = int(url.split('page=')[-1])
            return HTTPResponse(200, {}, [{'id': i + 1, 'full_name': f'example/r{i}'} for i in range((page-1)*100, page*100)])
        return HTTPResponse(200, {}, {'id': 1, 'full_name': 'example/r0'})
    api = MaintenanceGitHub(transport=wire, credential_resolver=lambda _: Credential('shared', 'token'), clock=lambda: now[0])
    _, report = reconcile(config, {}, wire, api.credential_resolver, adapter=api, schedule=schedule, now=now[0], discover_org=[('example', 'test')])
    assert len(calls) == 32
    assert not report['additions']
    assert schedule[key]['interrupted']
    now[0] += 3600
    _, report = reconcile(config, {}, wire, api.credential_resolver, adapter=api, schedule=schedule, now=now[0], discover_org=[('example', 'test')])
    assert len(calls) == 33
    assert calls[-1].endswith('/repos/example/r0')


def test_schedule_permissions_do_not_reset_but_owner_change_does():
    config = fixture(1)
    s = MaintenanceSchedule(config, {}, {}, 100000)
    item = config['scopes'][0]
    key = s.key('repository', item)
    item['permissions'] = []
    assert s.key('repository', item) == key
    config['owner_epoch'] += 1
    assert MaintenanceSchedule(config, {}, {}, 100000).key('repository', item) != key
