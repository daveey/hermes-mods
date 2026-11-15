"""Count real maintenance transport and bounded Collector sweeps, offline."""
from collections import Counter
from types import SimpleNamespace
from urllib.parse import urlsplit, parse_qs
import json
from plugins.runners.collector import Collector
from plugins.runners.config import ConfigFile
from plugins.runners.providers.base import Credential, HTTPResponse, ProviderResult
from plugins.runners.scripts import reconcile_scopes as cli
from plugins.runners.scripts.maintenance_schedule import MaintenanceSchedule
from plugins.runners.store import Store
from plugins.runners.tests.test_maintenance_schedule import fixture


def test_full_day_discovery_debt_deletion_and_revision_churn(tmp_path, monkeypatch):
    now = [100000]
    monkeypatch.setattr(cli.time, 'time', lambda: now[0])
    source = ConfigFile(tmp_path / 'config.yaml')
    config = source.update(fixture(), expected_revision=0, expected_owner_epoch=1)
    ref = config['scopes'][0]['credential_ref']
    runtime = {'credentials': {ref: {'key': 'shared'}}}
    deleted = config['scopes'][0]['id']
    calls, reports, applies = [], [], []
    class Broker:
        def __call__(self, ref): return Credential('shared', 'fictional-token')
        def transport(self, method, url, **kwargs):
            hour = int((now[0] - 100000) // 3600)
            calls.append((hour, url))
            if '/orgs/' in url:
                page = int(parse_qs(urlsplit(url).query)['page'][0])
                rows = [{'id': i + 1, 'full_name': f'example/r{i}'} for i in range(1, 552)]
                rows[0]['full_name'] = 'example/renamed'
                if hour >= 6: rows.append({'id': 1000, 'full_name': 'example/added'})
                return HTTPResponse(200, {}, rows[(page-1)*100:page*100])
            name = url.split('/repos/')[1]
            if name == 'example/r0': return HTTPResponse(404, {}, {})
            if name == 'example/r1': name = 'example/renamed'
            return HTTPResponse(200, {}, {'id': 1, 'full_name': name})
    class Inventory:
        def enumerate(self, scope):
            hour = int((now[0] - 100000) // 3600)
            error = 'not_found' if scope['id'] == deleted else None
            # One transient failed endpoint must not count as serviced proof.
            if scope['name'] == 'example/r20' and hour < 3: error = 'unauthorized'
            return ProviderResult((), error is None, error, now[0], None)
    # Ensure both a deletion first-proof and an unrelated rename in the first
    # bounded pass. Hints control priority only, never provider answers.
    visits = {}
    schedule = MaintenanceSchedule(config, runtime, visits, now[0])
    for scope in config['scopes'][:2]:
        visits[schedule.key('repository', scope)] = {'at': 0, 'pending': True}
    (tmp_path / 'scope-maintenance.json').write_text(json.dumps({'visits': visits}))
    args = SimpleNamespace(state_dir=str(tmp_path), apply=True, confirmation_interval=3600)
    broker = Broker()
    for hour in range(24):
        now[0] = 100000 + hour * 3600
        # Shared durable collector debt denies three hourly maintenance cycles.
        if hour in (4, 11, 18):
            with Store(tmp_path / 'state.sqlite') as store:
                Collector(store, source.load(), Inventory())
                store.connection.execute("INSERT OR REPLACE INTO collector_cache VALUES ('provider_budget',?)",
                    (json.dumps({'shared': {'next_at': now[0] + 60}}),))
        before = len(calls)
        report = cli._run(args, source, source.load(), runtime, broker, [('example', ref)])
        reports.append(report)
        assert len(calls) - before <= 32
        if hour in (4, 11, 18): assert len(calls) == before
        if report['applied']: applies.append(hour)
        if hour == 0:
            assert report['apply_hold']['deferred']
            assert source.load()['config_revision'] == config['config_revision']
        # Real Collector generates persisted current-revision scope_results.
        with Store(tmp_path / 'state.sqlite') as store:
            collector = Collector(store, source.load(), Inventory(), clock=lambda: now[0], max_scopes=16)
            for tick in range(6):
                now[0] = 100000 + hour * 3600 + (tick + 1) * 500
                collector.tick()
    counts = Counter(h for h, _ in calls)
    repos = {url.split('/repos/')[1] for _, url in calls if '/repos/' in url}
    assert deleted not in {s['id'] for s in source.load()['scopes']}
    assert all(b - a >= 6 for a, b in zip(applies, applies[1:]))
    assert len(calls) < 2208
    # Every original live repository was directly checked or freshly discovered
    # under its new canonical name; discovery itself always requires six pages.
    missing = {f'example/r{i}' for i in range(2, 552)} - repos
    assert not missing, sorted(missing)
    assert any(url.endswith('page=6') for _, url in calls)
    print({'wire_requests': len(calls), 'baseline_metadata_only': 2208,
           'per_hour': dict(sorted(counts.items())), 'apply_hours': applies,
           'direct_repository_names': len(repos), 'deletion_removed': True})
