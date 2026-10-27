"""Offline collector repair reconciliation; no runtime or credentials."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from plugins.runners.collector import Collector
from plugins.runners.store import Store
from plugins.runners.tests.test_collector import config, Adapter
from plugins.runners.scripts.run_collector import _readonly


@pytest.fixture
def core_path(monkeypatch, tmp_path):
    monkeypatch.setenv('HERMES_HOME', str(tmp_path / 'isolated-home'))
    monkeypatch.setattr('os.execv', lambda *args: pytest.fail('unexpected runtime exec'))
    monkeypatch.setattr('os.execve', lambda *args: pytest.fail('unexpected runtime exec'))
    import os
    import sys
    path = os.environ.get('HERMES_CORE_TEST_PATH')
    if not path:
        pytest.skip('set HERMES_CORE_TEST_PATH to exercise real core SessionDB')
    monkeypatch.setattr(sys, 'path', [*sys.path, path])


def test_explicit_core_bootstrap_uses_requested_checkout(core_path):
    import os
    from plugins.runners.scripts.run_collector import configure_core
    configure_core(os.environ['HERMES_CORE_TEST_PATH'])
    from tui_gateway.session_start_requests import StartIntent
    assert dict(StartIntent('Inspection', 'Diagnosis only', metadata={'runner_repair_id': 'repair_example'}).metadata) == {
        'runner_repair_id': 'repair_example'}


def test_reconcile_cadence_and_feature_gate(tmp_path):
    cfg = config()
    cfg['features']['repair_sessions'] = True
    now = [0]
    repairs = Mock()
    repairs.verification_targets.return_value = []
    with Store(tmp_path / 'state.sqlite') as store:
        collector = Collector(store, cfg, Adapter(), clock=lambda: 1000,
                              monotonic=lambda: now[0], repair_service=repairs)
        for at in (0, 1, 29, 30, 31, 60):
            now[0] = at
            collector.tick()
        assert repairs.reconcile_all.call_count == 3
        repairs.start.assert_not_called()
        cfg['features']['repair_sessions'] = False
        now[0] = 90
        collector.tick()
        assert repairs.reconcile_all.call_count == 3


def test_reconcile_failure_retries_without_advancing_clock(tmp_path):
    cfg = config()
    cfg['features']['repair_sessions'] = True
    repairs = Mock()
    repairs.verification_targets.return_value = []
    repairs.reconcile_all.side_effect = [RuntimeError('unavailable'), []]
    with Store(tmp_path / 'state.sqlite') as store:
        collector = Collector(store, cfg, Adapter(), repair_service=repairs)
        with pytest.raises(RuntimeError, match='unavailable'):
            collector.tick()
        collector.tick()
        assert repairs.reconcile_all.call_count == 2


def test_diagnosis_flag_allowed_but_mutation_refused():
    cfg = config()
    cfg['features']['repair_sessions'] = True
    _readonly(cfg)
    for flag in ('management_operations', 'dispatch', 'workspace_jobs'):
        cfg['features'][flag] = True
        with pytest.raises(RuntimeError, match='observe_only_required'):
            _readonly(cfg)
        cfg['features'][flag] = False


def test_status_adapter_uses_canonical_owner_db(monkeypatch, tmp_path, core_path):
    from plugins.runners.repair_status import repair_reconciler
    from plugins.runners.service import Service
    from hermes_cli import profiles
    from hermes_state import SessionDB
    from tui_gateway import session_start_requests as core
    home = tmp_path / 'owner'
    home.mkdir()
    db = SessionDB(db_path=home / 'state.db')
    db.close()
    requested = []
    monkeypatch.setattr(profiles, 'get_profile_dir', lambda profile: requested.append(profile) or home)
    monkeypatch.setattr(core, 'get_service', lambda *_: pytest.fail('runtime lookup'))
    cfg = config()
    cfg['features']['repair_sessions'] = True
    service = Service(tmp_path / 'state.sqlite', cfg)
    with repair_reconciler(service) as repairs:
        owner = repairs.register('example-principal')
        adapter = repairs._core('example-principal')[1]
        assert not hasattr(adapter, 'start')
        assert adapter.store.db.db_path == home / 'state.db'
        # Registered resolver still authorizes the repair identity.
        with pytest.raises(KeyError):
            adapter.status(owner, 'runners', 'missing')
    assert requested == ['example']


def test_missing_canonical_db_never_created(monkeypatch, tmp_path, core_path):
    from plugins.runners.repair_status import repair_reconciler
    from hermes_cli import profiles
    monkeypatch.setattr(profiles, 'get_profile_dir', lambda _: tmp_path / 'absent')
    service = SimpleNamespace(config=config())
    with pytest.raises(RuntimeError, match='repair_session_store_unavailable'):
        with repair_reconciler(service):
            pytest.fail('opened absent database')
    assert not (tmp_path / 'absent').exists()


@pytest.mark.parametrize('receipt,expected,released', [
    (None, 'unknown_outcome', False),
    ('completed', 'paused', False),
    ('failed', 'failed', True),
    ('cancelled', 'closed', True),
    ('archived', 'closed', True),
])
def test_real_core_receipts_and_restart_locks(monkeypatch, tmp_path, core_path,
                                               receipt, expected, released):
    from plugins.runners.repair_status import repair_reconciler
    from plugins.runners.tests.test_collector import F
    from hermes_cli import profiles
    from hermes_state import SessionDB
    from tui_gateway import session_start_requests as core
    home = tmp_path / 'owner'
    home.mkdir()
    db = SessionDB(db_path=home / 'state.db')
    db.close()
    monkeypatch.setattr(profiles, 'get_profile_dir', lambda _: home)
    monkeypatch.setattr(core, 'get_service', lambda *_: pytest.fail('runtime lookup'))
    cfg = config()
    cfg['features']['repair_sessions'] = True
    owner = dict(gateway_id=cfg['owner_gateway_id'], profile=cfg['owner_profile'],
                 owner_epoch=cfg['owner_epoch'], config_revision=cfg['config_revision'])
    runner = dict(F['Runner'], host_id=None)
    service = SimpleNamespace(path=tmp_path / 'state.sqlite', config=cfg,
        clock=lambda: 1000, inventory=lambda: dict(meta={'owner': owner},
        data={'runners': [runner], 'hosts': []}),
        inspect=lambda rid, limit=100: {'data': {'incidents': [dict(id='incident_0000000000000001', entity_id=rid, resolved_at=None)]}})
    request = dict(entity_id=runner['id'], incident_id='incident_0000000000000001',
        idempotency_key='request_0000000000000001', mode='diagnose', expected_owner=owner)
    principal = 'example-principal'
    with repair_reconciler(service) as repairs:
        repair = repairs.reserve(request, F['Requester'], principal=principal)
        start_owner, adapter = repairs._core(principal)
        ledger = adapter.store
        # Seed a claimed durable receipt, not a runtime turn or agent import.
        row = dict(stored_session_id='fixture-session', first_turn_id='fixture-turn')
        def seed(conn):
            conn.execute('INSERT INTO sessions(id,source,started_at) VALUES (?,?,?)',
                         (row['stored_session_id'], 'desktop', 1000))
            conn.execute('INSERT INTO session_start_requests '
                '(gateway_id,profile,principal,namespace,request_id,intent_digest,stored_session_id,'
                'first_turn_id,user_row_id,state,runtime_token,created_at,updated_at) '
                'VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (start_owner.gateway_id, start_owner.profile, principal, 'runners',
                 repair['session']['start_request_id'], 'fixture-digest',
                 row['stored_session_id'], row['first_turn_id'], 1, 'running',
                 'fixture-token', 1000, 1000))
        ledger.db._execute_write(seed)
        if receipt == 'archived':
            ledger.db._execute_write(lambda conn: conn.execute(
                'UPDATE sessions SET archived=1 WHERE id=?', (row['stored_session_id'],)))
        elif receipt:
            assert ledger.finish(row['first_turn_id'], 'fixture-token', receipt)
    # A fresh collector process has no runtime, but still sees canonical receipts.
    with repair_reconciler(service) as repairs:
        with Store(service.path) as store:
            Collector(store, cfg, Adapter(), repair_service=repairs).tick()
        result = repairs.get(repair['id'], principal=principal)
        assert result['state'] == expected
        assert result['session']['stored_session_id'] == row['stored_session_id']
        again = repairs.reserve(dict(request, idempotency_key='request_0000000000000002'),
                                F['Requester'], principal=principal)
        assert (again['id'] != repair['id']) is released
        assert repairs._core(principal)[1].store.lookup(start_owner, 'runners', 'never-dispatched') is None


def test_supervisor_wires_status_lane_and_closes(monkeypatch, tmp_path):
    import json
    from contextlib import contextmanager
    from plugins.runners.scripts import run_collector as runtime
    from plugins.runners import repair_status
    from plugins.runners.tests.test_supervisor import files, GATEWAY
    cfg, config_file, private = files(tmp_path)
    cfg['features']['repair_sessions'] = True
    config_file.write_text(json.dumps(cfg))
    repairs = Mock()
    repairs.verification_targets.return_value = []
    closed = []

    @contextmanager
    def factory(service):
        assert service.config == cfg
        assert service.path == tmp_path / 'state' / 'state.sqlite'
        try:
            yield repairs
        finally:
            closed.append(True)

    monkeypatch.setattr(repair_status, 'repair_reconciler', factory)
    runtime.run(config_file, private, (GATEWAY, 'example', 1), tmp_path / 'state',
                once=True, startup_delay=0)
    repairs.reconcile_all.assert_called_once_with()
    repairs.start.assert_not_called()
    assert closed == [True]


@pytest.mark.parametrize('elapsed,expected_wait', [(5, 25), (40, 0)])
def test_supervisor_repair_poll_is_start_to_start(monkeypatch, tmp_path, elapsed, expected_wait):
    import json
    from contextlib import nullcontext
    from plugins.runners.scripts import run_collector as runtime
    from plugins.runners import repair_status
    from plugins.runners.tests.test_supervisor import files, GATEWAY
    cfg, config_file, private = files(tmp_path)
    cfg['features']['repair_sessions'] = True
    config_file.write_text(json.dumps(cfg))
    monkeypatch.setattr(repair_status, 'repair_reconciler', lambda _: nullcontext(Mock()))
    monkeypatch.setattr(runtime, 'Collector', Mock())
    times = iter((100, 100 + elapsed))
    monkeypatch.setattr(runtime.time, 'monotonic', lambda: next(times))
    stop = Mock()
    stop.is_set.return_value = False
    stop.wait.side_effect = [False, True]
    runtime.run(config_file, private, (GATEWAY, 'example', 1), tmp_path / 'state',
                stop=stop, startup_delay=0, interval=300)
    assert [call.args[0] for call in stop.wait.call_args_list] == [0, expected_wait]
