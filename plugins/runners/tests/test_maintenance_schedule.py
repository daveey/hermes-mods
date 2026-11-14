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
