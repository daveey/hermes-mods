"""CLI maintenance uses durable admission; scheduling hints are never authority."""
from copy import deepcopy
from types import SimpleNamespace
import json

from plugins.runners.config import ConfigFile
from plugins.runners.providers.base import Credential, HTTPResponse
from plugins.runners.scripts import reconcile_scopes as cli
from plugins.runners.tests.test_maintenance_schedule import fixture


class Broker:
    def __init__(self):
        self.calls = []
        self.rename = True
    def __call__(self, ref):
        return Credential('shared', 'fictional-token')
    def transport(self, method, url, **kwargs):
        self.calls.append(url)
        name = url.split('/repos/')[1]
        if self.rename and name == 'example/r0':
            name = 'example/renamed'
        return HTTPResponse(200, {}, {'id': 1, 'full_name': name})


def setup(tmp_path):
    source = ConfigFile(tmp_path / 'config.yaml')
    config = source.update(fixture(40), expected_revision=0, expected_owner_epoch=1)
    args = SimpleNamespace(state_dir=str(tmp_path), apply=True, confirmation_interval=3600)
    return source, config, args, Broker()


def test_cli_bounds_requests_persists_schedule_and_shared_debt(tmp_path, monkeypatch):
    source, config, args, broker = setup(tmp_path)
    monkeypatch.setattr(cli.time, 'time', lambda: 100000)
    report = cli._run(args, source, config, {}, broker, [])
    assert len(broker.calls) <= 32
    assert report['maintenance']['deferred'] >= 8
    assert (tmp_path / 'scope-maintenance.json').exists()
    before = len(broker.calls)
    cli._run(args, source, source.load(), {}, broker, [])
    assert len(broker.calls) == before  # restart cannot reset shared pacing debt


def test_pending_rename_revalidated_not_replayed_before_apply(tmp_path, monkeypatch):
    source, config, args, broker = setup(tmp_path)
    now = [100000]
    monkeypatch.setattr(cli.time, 'time', lambda: now[0])
    # Suppress applies for another five hours, but permit collection now.
    (tmp_path / 'scope-maintenance.json').write_text(json.dumps({'visits': {}, 'last_apply': 96400}))
    report = cli._run(args, source, config, {}, broker, [])
    assert not report['applied']
    assert source.load() == config
    assert report['changes']  # advisory only
    broker.rename = False  # provider no longer authorizes the earlier rename
    now[0] = 118000
    before = len(broker.calls)
    report = cli._run(args, source, source.load(), {}, broker, [])
    assert 'https://api.github.com/repos/example/r0' in broker.calls[before:]
    assert not report['applied']
    assert source.load() == config
