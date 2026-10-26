"""Authority fencing and status-only recovery; no live runtime or mutation."""

from types import SimpleNamespace

import pytest

from plugins.runners.repairs import RepairService, domain_for
from plugins.runners.tests.test_repairs import env, core_types, PRINCIPAL, reserve


@pytest.mark.parametrize('change', ['disabled', 'revision', 'epoch', 'gateway', 'profile', 'missing', 'invalid'])
def test_registered_resolver_reloads_disk_authority(env, monkeypatch, change):
    e = env
    import yaml
    from plugins.runners.config import normalize
    current = normalize(dict(schema_version=1,
        **{k: v for k, v in e.service.config.items() if k.startswith('owner_') or k in ('features', 'config_revision')}))
    e.service.path = e.service.path.parent / 'runners' / 'state.sqlite'
    path = e.service.path.parent.parent / 'runners.yaml'
    path.write_text(yaml.safe_dump(current))
    import sys
    monkeypatch.setattr(sys.modules['tui_gateway.session_start_requests'], 'get_service', lambda profile: e.core)
    e.domain.close()
    e.domain = domain_for(e.service)
    try:
        row = reserve(e)
        owner = e.domain.register(PRINCIPAL)
        assert e.core.resolvers[owner, 'runners'](owner, row['session']['start_request_id'])
        if change == 'disabled':
            current['features']['repair_sessions'] = False
        elif change in ('missing', 'invalid'):
            current = None
        else:
            key = {'revision': 'config_revision', 'epoch': 'owner_epoch',
                   'gateway': 'owner_gateway_id', 'profile': 'owner_profile'}[change]
            current[key] = current[key] + 1 if isinstance(current[key], int) else (
                '00000000-0000-4000-8000-000000000099' if change == 'gateway' else 'changed')
        if change == 'missing':
            path.unlink()
        elif change == 'invalid':
            path.write_text('invalid: [')
        else:
            path.write_text(yaml.safe_dump(current))
        # No domain_for refresh or runners endpoint between change and Desktop RPC.
        with pytest.raises((RuntimeError, ValueError)):
            e.core.start(owner, 'runners', row['session']['start_request_id'])
        assert e.core.calls == 0
    finally:
        e.domain.close()


def historical(e):
    row = reserve(e)
    e.domain.start(row['id'], principal=PRINCIPAL)
    e.domain.close()
    e.service.config['config_revision'] += 1
    e.inventory['meta']['owner']['config_revision'] += 1
    calls = []
    def status(owner, namespace, request_id):
        calls.append((owner, namespace, request_id))
        assert owner.principal == PRINCIPAL
        return e.core.rows.get(request_id)
    e.core.store = SimpleNamespace(status=status)
    e.domain = RepairService(e.service, core_factory=lambda profile: e.core)
    return row, calls


@pytest.mark.parametrize('terminal', ['archived', 'failed', 'cancelled'])
def test_revision_reconciles_old_receipt_and_releases_only_terminal(env, terminal):
    e = env
    row, calls = historical(e)
    receipt = e.core.rows[row['session']['start_request_id']]
    if terminal == 'archived':
        receipt['archived'] = True
    else:
        receipt['reconciliation'] = terminal
    results = e.domain.reconcile_all()
    assert len(results) == 1
    assert results[0]['state'] in ('closed', 'failed')
    assert results[0]['owner'] == row['owner']
    assert calls[0][2] == row['session']['start_request_id']
    assert e.core.calls == 1
    assert not e.core.resolvers  # status-only recovery never registers start authority
    assert reserve(e, expected_owner=e.inventory['meta']['owner'],
                   idempotency_key='request_0000000000000002')['id'] != row['id']


@pytest.mark.parametrize('state', ['absent', 'unknown', 'completed', 'queued', 'unavailable'])
def test_revision_unknown_and_nonterminal_keep_lock_and_never_restart(env, state):
    e = env
    row, calls = historical(e)
    request_id = row['session']['start_request_id']
    if state == 'absent':
        del e.core.rows[request_id]
    elif state == 'unavailable':
        def fail(*args):
            raise ConnectionError('offline')
        e.core.store.status = fail
    else:
        e.core.rows[request_id]['reconciliation'] = 'unknown_outcome' if state == 'unknown' else state
    assert len(e.domain.reconcile_all()) == 1
    with e.domain._db() as db:
        assert db.execute('SELECT active FROM repair_domain_records WHERE id=?', (row['id'],)).fetchone()[0] == 1
    with pytest.raises(ValueError, match='owner_conflict'):
        e.domain.start(row['id'], principal=PRINCIPAL)
    with pytest.raises(ValueError, match='owner_conflict'):
        reserve(e, expected_owner=e.inventory['meta']['owner'], idempotency_key='request_0000000000000002')
    assert e.core.calls == 1
    assert not e.core.resolvers


@pytest.mark.parametrize('field,value', [('owner_epoch', 2), ('owner_gateway_id', 'other'), ('owner_profile', 'other')])
def test_history_never_crosses_owner_transfer(env, field, value):
    row, calls = historical(env)
    env.service.config[field] = value
    assert env.domain.reconcile_all() == []
    with pytest.raises(ValueError, match='owner_conflict'):
        env.domain.reconcile(row['id'], principal=PRINCIPAL)
    assert not calls


def test_history_principal_fence(env):
    row, calls = historical(env)
    with pytest.raises(PermissionError):
        env.domain.reconcile(row['id'], principal='other')
    assert not calls


def test_current_registration_does_not_authorize_historical_start(env):
    row, calls = historical(env)
    owner = env.domain.register(PRINCIPAL)
    with pytest.raises(ValueError, match='owner_conflict'):
        env.core.start(owner, 'runners', row['session']['start_request_id'])
    assert env.core.calls == 1
    assert not calls


def test_revision_reconciliation_real_core_store(env, monkeypatch, tmp_path):
    import importlib.util
    import os
    import socket
    import sys
    from pathlib import Path
    from types import ModuleType
    root = os.environ.get('RUNNERS_CORE_ROOT')
    if not root:
        pytest.skip('RUNNERS_CORE_ROOT not provided')
    monkeypatch.setenv('HERMES_HOME', str(tmp_path / 'isolated-home'))
    monkeypatch.setattr(sys, 'path', [*sys.path, root])
    spec = importlib.util.spec_from_file_location('tui_gateway.session_start_requests',
        Path(root) / 'tui_gateway/session_start_requests.py')
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    bootstrap = ModuleType('hermes_bootstrap')
    bootstrap._happy_eyeballs_create_connection = socket.create_connection
    monkeypatch.setitem(sys.modules, 'hermes_bootstrap', bootstrap)
    from hermes_state import SessionDB
    db = SessionDB(tmp_path / 'core-state.sqlite')
    scheduled = []
    class Runtime:
        def check_supported(self, owner):
            pass
        def schedule(self, row, intent, finish):
            scheduled.append(row)
            assert finish('failed')
    core = module.StartService(module.StartStore(db), Runtime())
    e = env
    e.domain.close()
    e.domain = RepairService(e.service, core_factory=lambda profile: core)
    try:
        row = reserve(e)
        owner = e.domain.register(PRINCIPAL)
        core.start(owner, 'runners', row['session']['start_request_id'])
        e.domain.close()
        e.service.config['config_revision'] += 1
        e.domain = RepairService(e.service, core_factory=lambda profile: core)
        assert e.domain.reconcile_all()[0]['state'] == 'failed'
        assert not core._resolvers
        assert len(scheduled) == 1
        with e.domain._db() as check:
            assert check.execute('SELECT active FROM repair_domain_records').fetchone()[0] == 0
        assert e.domain.reconcile_all() == []
    finally:
        e.domain.close()
        db.close()
