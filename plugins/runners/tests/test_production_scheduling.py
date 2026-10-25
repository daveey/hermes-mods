from copy import deepcopy
import json
from math import ceil
import pytest

from plugins.runners.collector import Collector
from plugins.runners.store import Store
from plugins.runners.providers.github_actions import GitHubActions
from plugins.runners.providers.base import Credential, HTTPResponse, ProviderResult
from test_scheduler import fleet, Empty
from test_collector import F


def test_pacing_window_uses_header_budget_without_one_call_per_tick():
    calls = []
    def transport(*args, **kwargs):
        calls.append(args)
        return HTTPResponse(200, {'x-ratelimit-remaining': '100', 'x-ratelimit-reset': '2000'}, {'total_count': 0, 'runners': []})
    a = GitHubActions(transport=transport, credential_resolver=lambda _: Credential('shared', 'fake'),
        clock=lambda: 1000, pace_requests=True, pacing_window=30)
    a.begin_cycle()
    results = [a.enumerate(F['Scope']) for _ in range(10)]
    assert len(calls) == 4
    assert sum(r.complete for r in results) == 4
    assert results[-1].error == 'budget_deferred'


def test_populated_scopes_refresh_while_discovery_keeps_progressing(tmp_path):
    cfg = fleet(100)
    row = deepcopy(F['Runner']); row['scope_id'] = cfg['scopes'][-1]['id']
    now = [1000]
    class Provider(Empty):
        def enumerate(self, scope):
            self.calls.append(scope['id'])
            rows = (row,) if scope['id'] == row['scope_id'] else ()
            return ProviderResult(rows, True, None, now[0], len(rows))
    a = Provider(lambda: now[0])
    with Store(tmp_path / 'state.sqlite') as store:
        store.put('Runner', row, at=900)
        c = Collector(store, cfg, a, clock=lambda: now[0], max_scopes=4)
        for _ in range(3):
            snap = c.tick()
            assert row['scope_id'] in a.calls[-4:]
            now[0] += 31
        assert len(set(a.calls)) == 10
        assert len(snap['coverage']) == 100
        assert snap['collection']['state'] == 'partial'


@pytest.mark.parametrize('count,active_count,slots,max_scopes', [
    (555, 8, 4, 16), (17, 2, 1, 16), (17, 2, 1, 1),
])
def test_shared_credential_bounds_discovery_and_active_refresh(
        tmp_path, count, active_count, slots, max_scopes):
    cfg = fleet(count)
    now = [1000]
    calls = []
    active = {s['id'] for s in cfg['scopes'][-active_count:]}
    by_name = {}
    for i, scope in enumerate(cfg['scopes']):
        scope['name'] = f'fictional/repo-{i}'
        scope['kind'] = 'repository'
        by_name[scope['name']] = scope['id']

    def transport(method, url, **kwargs):
        sid = by_name[url.split('/repos/')[1].split('/actions/')[0]]
        calls.append((sid, now[0]))
        rows = [dict(id=1, name='fictional-runner', status='online', busy=False,
                     labels=[], os='linux')] if sid in active else []
        # Four (or one) actual reads per 31-second tick, regardless of the
        # collector's sixteen scope attempts. All scopes share this budget.
        return HTTPResponse(200, {
            'x-ratelimit-remaining': str(slots * 100),
            'x-ratelimit-reset': str(now[0] + 3100),
        }, dict(total_count=len(rows), runners=rows))

    def adapter():
        return GitHubActions(transport=transport,
            credential_resolver=lambda _: Credential('shared', 'fake'),
            clock=lambda: now[0], pace_requests=True, pacing_window=30)

    with Store(tmp_path / 'state.sqlite') as store:
        for scope in cfg['scopes'][-active_count:]:
            row = deepcopy(F['Runner'])
            row.update(id='runner_' + scope['id'].split('_')[1], scope_id=scope['id'])
            store.put('Runner', row, at=now[0])
        c = Collector(store, cfg, adapter(), clock=lambda: now[0], max_scopes=max_scopes)
        ticks = 2 * ceil((count - active_count) / slots) + 2
        for tick in range(ticks):
            # Fairness must survive process restarts, not reset to active first.
            if tick % 7 == 0:
                c = Collector(store, cfg, adapter(), clock=lambda: now[0], max_scopes=max_scopes)
            c.tick()
            now[0] += 31
        assert {sid for sid, _ in calls} == {s['id'] for s in cfg['scopes']}
        for sid in active:
            observed = [1000] + [at for key, at in calls if key == sid] + [now[0]]
            assert max(b - a for a, b in zip(observed, observed[1:])) <= 31 * 2 * ceil(active_count / slots)


@pytest.mark.parametrize('max_scopes', [1, 16])
@pytest.mark.parametrize('refresh_all', [False, True])
def test_continuous_refresh_cannot_monopolize_shared_credential(tmp_path, max_scopes, refresh_all):
    cfg = fleet(7)
    now = [1000]
    calls = []
    active = cfg['scopes'][-1]['id']
    refresh = cfg['scopes'][0]['id']
    by_name = {}
    for i, scope in enumerate(cfg['scopes']):
        scope.update(name=f'fictional/repo-{i}', kind='repository')
        by_name[scope['name']] = scope['id']

    def transport(method, url, **kwargs):
        sid = by_name[url.split('/repos/')[1].split('/actions/')[0]]
        calls.append((sid, now[0]))
        rows = [dict(id=1, name='fictional-runner', status='online', busy=False,
                     labels=[], os='linux')] if sid == active else []
        return HTTPResponse(200, {'x-ratelimit-remaining': '100',
            'x-ratelimit-reset': str(now[0] + 3100)},
            dict(total_count=len(rows), runners=rows))

    with Store(tmp_path / 'state.sqlite') as store:
        row = deepcopy(F['Runner'])
        row.update(scope_id=active)
        store.put('Runner', row, at=now[0])
        for tick in range(24):
            adapter = GitHubActions(transport=transport,
                credential_resolver=lambda _: Credential('shared', 'fake'),
                clock=lambda: now[0], pace_requests=True, pacing_window=30)
            c = Collector(store, cfg, adapter, clock=lambda: now[0], max_scopes=max_scopes)
            with store.transaction():
                store.connection.execute('INSERT INTO collector_refresh VALUES (?,?,?,?)',
                    (f'refresh-{tick}', json.dumps([] if refresh_all else [refresh]), 'pending', now[0]))
            before = len(calls)
            c.tick()
            assert len(calls) - before == 1
            now[0] += 31
        assert {sid for sid, _ in calls} == {s['id'] for s in cfg['scopes']}
        for sid in ((active,) if refresh_all else (active, refresh)):
            observed = [1000] + [at for key, at in calls if key == sid] + [now[0]]
            assert max(b - a for a, b in zip(observed, observed[1:])) <= 3 * 31


@pytest.mark.parametrize('same_lane', [False, True])
@pytest.mark.parametrize('max_scopes', [1, 16])
@pytest.mark.parametrize('budget_field', ['next_at', 'until'])
def test_deferred_credential_cannot_starve_independent_credential(
        tmp_path, same_lane, max_scopes, budget_field):
    cfg = fleet(40)
    now = [1000]
    healthy = cfg['scopes'][-1]['id']
    calls = []
    for i, scope in enumerate(cfg['scopes']):
        scope.update(name=f'fictional/repo-{i}', kind='repository',
                     credential_ref='cred_' + ('a' if scope['id'] == healthy else 'b') * 32)

    def transport(method, url, **kwargs):
        calls.append(now[0])
        assert '/repos/fictional/repo-39/' in url
        return HTTPResponse(200, {}, dict(total_count=0, runners=[]))

    with Store(tmp_path / 'state.sqlite') as store:
        if not same_lane:
            for scope in cfg['scopes'][:-1]:
                row = deepcopy(F['Runner'])
                row.update(id='runner_' + scope['id'].split('_')[1], scope_id=scope['id'])
                store.put('Runner', row, at=now[0])
        for tick in range(8):
            adapter = GitHubActions(transport=transport,
                credential_resolver=lambda ref: Credential(ref, 'fake'),
                clock=lambda: now[0], pace_requests=True, pacing_window=30)
            adapter.restore_state({'cred_' + 'b' * 32: {budget_field: 10000}})
            # Recreate on every tick: both fairness and backoff must persist.
            c = Collector(store, cfg, adapter, clock=lambda: now[0], max_scopes=max_scopes)
            before = len(calls)
            snap = c.tick()
            assert len(calls) - before <= 1
            assert sum(v['attempted_at'] == now[0] for v in snap['scope_results'].values()) <= max_scopes
            now[0] += 31
        assert calls
        observed = [1000] + calls + [now[0]]
        assert max(b - a for a, b in zip(observed, observed[1:])) <= 2 * 31
