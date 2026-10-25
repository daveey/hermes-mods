"""Failure recovery must use committed evidence, never speculative progress."""
from contextlib import contextmanager
from copy import deepcopy

import pytest

from plugins.runners.collector import Collector
from plugins.runners.providers.base import Credential, HTTPResponse
from plugins.runners.providers.github_actions import GitHubActions
from plugins.runners.store import Store
from test_collector import Adapter, F, config


def test_failed_snapshot_restores_memory_and_retries(tmp_path):
    adapter = Adapter()
    now = [1000]
    with Store(tmp_path / 'state.sqlite') as store:
        collector = Collector(store, config(), adapter, clock=lambda: now[0])
        collector.tick()
        committed = deepcopy(collector.scope_results)
        original = store.transaction
        count = [0]

        @contextmanager
        def fail_commit():
            count[0] += 1
            current = count[0]
            with original():
                yield
                if current == 2:
                    raise RuntimeError('injected_commit_failure')

        store.transaction = fail_commit
        now[0] = 1031
        with pytest.raises(RuntimeError, match='injected_commit_failure'):
            collector.tick()
        assert collector.scope_results == committed
        store.transaction = original
        collector.tick()
        assert adapter.calls == 3


def test_expired_credential_is_evicted_without_immediate_retry():
    class Resolver:
        def __init__(self):
            self.invalidated = []
        def __call__(self, ref):
            return Credential('shared-key', 'fictional')
        def invalidate(self, *, key):
            self.invalidated.append(key)
    resolver = Resolver()
    calls = []
    def transport(*args, **kwargs):
        calls.append(args)
        return HTTPResponse(401, {})
    adapter = GitHubActions(transport=transport, credential_resolver=resolver)
    assert adapter.enumerate(F['Scope']).error == 'unauthorized'
    assert resolver.invalidated == ['shared-key']
    assert len(calls) == 1


def test_inventory_calls_scale_by_scope_not_runner(tmp_path):
    class Many(Adapter):
        def enumerate(self, scope):
            result = super().enumerate(scope)
            rows = []
            for i in range(100):
                row = deepcopy(result.rows[0])
                row.update(id='runner_' + format(i + 1, '032x'),
                           provider_runner_id=str(i + 1), scope_id=scope['id'])
                rows.append(row)
            from plugins.runners.providers.base import ProviderResult
            return ProviderResult(tuple(rows), True, None, 1000, 100)
    cfg = config()
    cfg['scopes'] = [dict(F['Scope'], id='scope_' + format(i + 1, '032x')) for i in range(4)]
    adapter = Many()
    with Store(tmp_path / 'state.sqlite') as store:
        Collector(store, cfg, adapter, clock=lambda: 1000).tick()
    assert adapter.calls == 4
