"""Maintenance uses the same lifetime lock and keeps execution-bound scopes intact."""
from types import SimpleNamespace
import pytest
from plugins.runners.collector import OwnerLock
from plugins.runners.config import ConfigFile, normalize
from plugins.runners.tests.test_config import BASE
from plugins.runners.tests.test_github import scope
from plugins.runners.providers.base import Credential, HTTPResponse
from plugins.runners.scripts import reconcile_scopes as cli


def test_cli_refuses_live_collector_before_any_http(tmp_path, monkeypatch):
    config = normalize(BASE)
    source = ConfigFile(tmp_path / 'runners.yaml')
    config = source.update(config, expected_revision=0, expected_owner_epoch=config['owner_epoch'])
    monkeypatch.setattr(cli, 'load_runtime', lambda _: {'credentials': {}})
    monkeypatch.setattr(cli, 'BrokerVendGitHub', lambda *a, **k: SimpleNamespace(transport=None, close=lambda: None))
    monkeypatch.setattr(cli, 'reconcile', lambda *a, **k: pytest.fail('network entered while collector owns lock'))
    identity = tuple(config[k] for k in ('owner_gateway_id', 'owner_profile', 'owner_epoch'))
    with OwnerLock(tmp_path / 'collector.lock', config, identity):
        with pytest.raises(RuntimeError, match='collector_running'):
            cli.main(['--config', str(source.path), '--runtime', 'unused', '--state-dir', str(tmp_path), '--apply'])
    assert source.load() == config


def test_workflow_scope_rename_is_reported_not_applied():
    config = normalize(BASE); config['scopes'] = [scope()]
    # The planner only needs the reference field; the unchanged output is then
    # validated with a real contract-valid workflow fixture.
    from plugins.runners.tests.test_config import FIXTURES
    from copy import deepcopy
    workflow = deepcopy(FIXTURES['Workflow']); workflow['scope_id'] = scope()['id']
    config['allowed_workflows'] = [workflow]
    config = normalize(config)
    changed, report = cli.reconcile(config, {},
        lambda *a, **k: HTTPResponse(200, {}, {'id': 42, 'full_name': 'example/new'}),
        lambda ref: Credential(ref, 'fictional-token'), pause=lambda _: None)
    assert changed == config and not report['changes']
    assert report['errors'][0]['error'] == 'referenced_scope_requires_coordinated_rename'
