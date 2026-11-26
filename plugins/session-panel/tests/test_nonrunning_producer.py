"""Real claim refusal -> source observation -> real reducer (optional core).

This exercises local acquisition, NOT replica authentication. No live stores.
"""
import copy
import importlib.util
import json
import os
from pathlib import Path
import time

import pytest

try:
    import hermes_cli.kanban_nonrunning_reason  # noqa: F401
except ImportError:
    if os.environ.get('HERMES_CORE_SOURCE'):
        raise  # an explicitly selected but incompatible core is not public-CI absence
    pytest.skip('non-running producer requires compatible Hermes core', allow_module_level=True)
from hermes_cli import kanban_capacity_evidence as capacity
from hermes_cli import kanban_db as kb
from hermes_cli import kanban_db_connect as kbc
from hermes_cli import kanban_db_dispatch as dispatch
from hermes_cli import kanban_db_sync as sync
from hermes_cli import kanban_dispatch_verdict as verdict
from hermes_cli import kanban_execution_source as source
from tests.hermes_cli import test_kanban_usage_accounting as accounting

from tests.hermes_cli import test_kanban_nonrunning_transport as remote_helpers
svc = remote_helpers.svc
gws = remote_helpers.gws
kanban_home = accounting.kanban_home
spec = importlib.util.spec_from_file_location('nonrunning_panel_model', Path(__file__).resolve().parents[1] / 'model.py')
panel = importlib.util.module_from_spec(spec)
spec.loader.exec_module(panel)


def actual_refusal(conn):
    tid, _ = accounting.released(conn)
    captured = capacity.capture(conn)
    assert kb.claim_task(conn, tid) is None
    result = dispatch.DispatchResult()
    verdict.claim_refused(result, tid, sync.pop_claim_deferral(tid))
    now = time.time()
    assert capacity.record(conn, result, board='default', machine='fixture', now=now, captured=captured)
    with source.transaction(conn):
        sources = source.assemble(conn, [tid], board='default', gateway='fixture', profile='default', observed_at=now + 1)
    raw = source.raw({'id': tid}, sources[tid])
    assert raw['non_running_reason']['code'] == 'usage_accounting_pending'
    row = {'id': tid, 'title': 'Accounting refusal', 'status': 'ready', 'gateway': 'fixture',
           'profile': 'default', 'board': 'default', 'execution_observations': [raw]}
    return {'tasks': [row], 'done': [], 'cards': {}, 'gateway': 'fixture', 'profile': 'default',
            'read_at': now + 1, 'computed_at': now + 1}, now + 1


def test_real_producer_is_agent_blocked_not_human(kanban_home):
    with kbc.connect_closing() as conn:
        bundle, now = actual_refusal(conn)
        before = copy.deepcopy(bundle)
        model = panel.reduce_panel(bundle, now=now)
        assert len(model['sections']['stuck']) == 1
        assert model['sections']['needs_you'] == []
        assert model['sections']['ready'] == []
        view = model['sections']['stuck'][0]
        assert view['detail'] == 'blocked · Run usage accounting is unsettled'
        assert 'Next actor: agent' in view['details']
        assert any('original source accounting' in s for s in view['details'])
        assert view['evidence_state'] == 'unknown'
        assert bundle == before


@pytest.mark.parametrize('mutation', ['expired', 'missing', 'scope', 'conflict', 'new_unknown'])
def test_refusal_cannot_survive_unknown_evidence(kanban_home, mutation):
    with kbc.connect_closing() as conn:
        bundle, now = actual_refusal(conn)
        row = bundle['tasks'][0]
        raw = row['execution_observations'][0]
        if mutation == 'expired':
            now += 181
        elif mutation == 'missing':
            row.pop('execution_observations')
        elif mutation == 'scope':
            raw['identity']['gateway'] = 'unrelated'
        else:
            other = copy.deepcopy(raw)
            why = other['non_running_reason']
            if mutation == 'conflict':
                why['code'] = 'conflicting'
            else:
                why.update(freshness='unknown', observed_at=why['observed_at'] + .01)
            row['execution_observations'].append(other)
        model = panel.reduce_panel(bundle, now=now)
        assert model['sections']['stuck'] == []
        assert model['sections']['needs_you'] == []
        assert 'execution reason unknown' in model['sections']['ready'][0]['detail']
        assert 'for a worker' not in str(model)


@pytest.mark.parametrize('status', ['running', 'done', 'scheduled', 'blocked', 'todo', 'review'])
def test_later_board_phase_never_keeps_old_refusal(kanban_home, status):
    with kbc.connect_closing() as conn:
        bundle, now = actual_refusal(conn)
        bundle['tasks'][0]['status'] = status
        model = panel.reduce_panel(bundle, now=now)
        assert 'Run usage accounting is unsettled' not in str(model)


def remote_refusal(gws):
    home, worker, tid = remote_helpers.prepared(gws)
    at = remote_helpers.publish(worker, tid)
    home.round()
    home.enter()
    with source.transaction(home.conn):
        sources = source.assemble(home.conn, [tid], board='default', gateway=home.name, profile='default', observed_at=at + 1)
    raw = source.raw({'id': tid}, sources[tid])
    assert raw['non_running_reason']['observer'] == worker.name
    row = {'id': tid, 'title': 'Transported accounting refusal', 'status': 'ready', 'gateway': home.name,
           'profile': 'default', 'board': 'default', 'execution_observations': [raw]}
    return {'tasks': [row], 'done': [], 'cards': {}, 'gateway': home.name, 'profile': 'default',
            'read_at': at + 1, 'computed_at': at + 1}, at + 1


def test_real_foreign_refusal_reaches_actual_reducer(gws):
    bundle, now = remote_refusal(gws)
    model = panel.reduce_panel(bundle, now=now)
    assert len(model['sections']['stuck']) == 1
    assert 'Run usage accounting is unsettled' in model['sections']['stuck'][0]['detail']
    assert model['sections']['needs_you'] == []
    expired = panel.reduce_panel(bundle, now=now + 180)
    assert expired['sections']['stuck'] == []
    assert 'execution reason unknown' in expired['sections']['ready'][0]['detail']


def actual_drain(conn, monkeypatch, *, code, gateway='fixture'):
    """Exercise the real pre-claim gate, not a constructed DispatchResult reason."""
    from hermes_cli import kanban_gateway_drain as drain
    from hermes_cli import profiles
    monkeypatch.setattr(profiles, 'resolve_profile_env', lambda _: None)
    monkeypatch.setattr(drain, 'dispatch_paused', lambda _: code == 'gateway_draining')
    monkeypatch.setattr(drain, 'core_update_hold', lambda: {} if code == 'update_admission_hold' else None)
    tid = kb.create_task(conn, title='Supervised admission wait', assignee='default')
    captured = capacity.capture(conn)
    assert drain.claim_if_admitting(conn, tid, 'default', 'ready', 60) is None
    result = dispatch.DispatchResult()
    verdict.claim_refused(result, tid, sync.pop_claim_deferral(tid))
    assert result.claim_deferred[0][1] == code
    now = time.time()
    capacity.record(conn, result, board='default', machine=gateway, now=now, captured=captured)
    return diagnostic_bundle(conn, tid, gateway, captured['observed_at'] + 1), captured['observed_at'] + 1


def diagnostic_bundle(conn, tid, gateway, now):
    with source.transaction(conn):
        sources = source.assemble(conn, [tid], board='default', gateway=gateway, profile='default', observed_at=now)
    raw = source.raw({'id': tid}, sources[tid])
    task = kb.get_task(conn, tid)
    assert task is not None
    row = {'id': tid, 'title': 'Supervised admission wait', 'status': task.status, 'gateway': gateway,
           'profile': 'default', 'board': 'default', 'execution_observations': [raw]}
    return {'tasks': [row], 'done': [], 'cards': {}, 'gateway': gateway, 'profile': 'default',
            'read_at': now, 'computed_at': now}


def remote_drain(gws, monkeypatch, code):
    from hermes_cli import kanban_gateway_drain as drain
    from hermes_cli import kanban_dispatch_skip as skips
    from hermes_cli import profiles
    home, worker, tid = remote_helpers.prepared(gws)
    monkeypatch.setattr(profiles, 'resolve_profile_env', lambda _: None)
    monkeypatch.setattr(drain, 'dispatch_paused', lambda _: code == 'gateway_draining')
    monkeypatch.setattr(drain, 'core_update_hold', lambda: {} if code == 'update_admission_hold' else None)
    captured = capacity.capture(worker.conn)
    assert drain.claim_if_admitting(worker.conn, tid, 'default', 'ready', 60) is None
    result = dispatch.DispatchResult()
    verdict.claim_refused(result, tid, sync.pop_claim_deferral(tid))
    assert result.claim_deferred[0][1] == code
    assert skips.record_tick(worker.conn, result, captured=captured, machine=worker.name) == 1
    worker.round()
    home.round()
    home.enter()
    now = captured['observed_at'] + 1
    return diagnostic_bundle(home.conn, tid, home.name, now), now


@pytest.mark.parametrize('code', ['gateway_draining', 'update_admission_hold'])
def test_real_drain_is_observer_wait_not_task_block(kanban_home, monkeypatch, code):
    with kbc.connect_closing() as conn:
        bundle, now = actual_drain(conn, monkeypatch, code=code)
        model = panel.reduce_panel(bundle, now=now)
        assert not model['sections']['stuck'] and not model['sections']['needs_you']
        view = model['sections']['ready'][0]
        assert view['non_running_reason']['code'] == code
        assert view['non_running_reason']['scope'] == 'observing_dispatcher'
        assert view['non_running_reason']['disposition'] == 'waiting'
        assert 'Observing dispatcher:' in view['detail']
        assert 'for a worker' not in view['detail']
        expired = panel.reduce_panel(bundle, now=now + 181)
        assert 'execution reason unknown' in expired['sections']['ready'][0]['detail']
        # Actual gate recovery and normal claim, not a manually relabelled row.
        from hermes_cli import kanban_gateway_drain as drain
        monkeypatch.setattr(drain, 'dispatch_paused', lambda _: False)
        monkeypatch.setattr(drain, 'core_update_hold', lambda: None)
        tid = bundle['tasks'][0]['id']
        assert drain.claim_if_admitting(conn, tid, 'default', 'ready', 60) is not None
        recovered = diagnostic_bundle(conn, tid, 'fixture', now + 2)
        assert recovered['tasks'][0]['status'] == 'running'
        assert 'non_running_reason' not in recovered['tasks'][0]['execution_observations'][0]
        assert 'Observing dispatcher:' not in str(panel.reduce_panel(recovered, now=now + 2))


@pytest.mark.parametrize('code', ['gateway_draining', 'update_admission_hold'])
def test_real_transported_drain_remains_observer_scoped(gws, monkeypatch, code):
    bundle, now = remote_drain(gws, monkeypatch, code)
    model = panel.reduce_panel(bundle, now=now)
    assert not model['sections']['stuck'] and not model['sections']['needs_you']
    view = model['sections']['ready'][0]
    assert view['non_running_reason']['code'] == code
    assert view['non_running_reason']['scope'] == 'observing_dispatcher'
    assert view['non_running_reason']['observer'] != bundle['gateway']
    assert 'Observing dispatcher:' in view['detail']


def start_gate_result(conn, tid, monkeypatch):
    """Run the real last preclaim gate; only the external plugin answer is controlled."""
    from hermes_cli import kanban_start_gate as gate
    from hermes_cli import lifecycle
    monkeypatch.setattr(lifecycle, 'has_hook', lambda name: name == gate.HOOK)
    def answer(name, **kwargs):
        assert name == gate.HOOK and kwargs['task_id'] == tid
        return [{'hold': True, 'reason': 'private_plugin_detail'}]
    monkeypatch.setattr(lifecycle, 'invoke_hook', answer)
    row = conn.execute('SELECT * FROM tasks WHERE id=?', (tid,)).fetchone()
    result = dispatch.DispatchResult()
    assert gate.pre_claim_hold(conn, row, 'ready', result, assignee='default', board='default')
    assert result.respawn_guarded == [(tid, 'start_gate:private_plugin_detail')]
    task = kb.get_task(conn, tid)
    assert task is not None and task.status == 'ready'
    assert task.current_run_id is None
    return result


def actual_start_gate(conn, monkeypatch):
    tid = kb.create_task(conn, title='Plugin start wait', assignee='default')
    captured = capacity.capture(conn)
    result = start_gate_result(conn, tid, monkeypatch)
    assert capacity.record(conn, result, board='default', machine='fixture', captured=captured)
    now = captured['observed_at'] + 1
    return diagnostic_bundle(conn, tid, 'fixture', now), now


def remote_start_gate(gws, monkeypatch):
    from hermes_cli import kanban_dispatch_skip as skips
    home, worker, tid = remote_helpers.prepared(gws)
    captured = capacity.capture(worker.conn)
    result = start_gate_result(worker.conn, tid, monkeypatch)
    assert skips.record_tick(worker.conn, result, captured=captured, machine=worker.name) == 1
    worker.round()
    home.round()
    home.enter()
    now = captured['observed_at'] + 1
    return diagnostic_bundle(home.conn, tid, home.name, now), now


def assert_start_gate_wait(bundle, now):
    model = panel.reduce_panel(bundle, now=now)
    assert not model['sections']['stuck'] and not model['sections']['needs_you']
    view = model['sections']['ready'][0]
    why = view['non_running_reason']
    assert why['code'] == 'start_gate'
    assert why['freshness'] == 'current'
    assert why['scope'] == 'observing_dispatcher'
    assert why['disposition'] == 'waiting' and why['actor'] == 'dispatcher'
    assert 'plugin start gate' in view['detail']
    assert 'next dispatch pass' in why['next_action']
    assert 'private_plugin_detail' not in str(model)
    assert 'execution reason unknown' in panel.reduce_panel(bundle, now=now + 181)['sections']['ready'][0]['detail']


def test_real_plugin_start_gate_is_scoped_wait_with_normal_recovery(kanban_home, monkeypatch):
    from hermes_cli import kanban_start_gate as gate
    from hermes_cli import lifecycle
    with kbc.connect_closing() as conn:
        bundle, now = actual_start_gate(conn, monkeypatch)
        assert_start_gate_wait(bundle, now)
        tid = bundle['tasks'][0]['id']
        monkeypatch.setattr(lifecycle, 'invoke_hook', lambda *args, **kwargs: [])
        row = conn.execute('SELECT * FROM tasks WHERE id=?', (tid,)).fetchone()
        assert not gate.pre_claim_hold(conn, row, 'ready', dispatch.DispatchResult(), assignee='default', board='default')
        assert kb.claim_task(conn, tid) is not None
        recovered = diagnostic_bundle(conn, tid, 'fixture', now + 1)
        assert 'non_running_reason' not in recovered['tasks'][0]['execution_observations'][0]
        assert 'plugin start gate' not in str(panel.reduce_panel(recovered, now=now + 1))


def test_real_transported_plugin_gate_keeps_observer_and_original_clock(gws, monkeypatch):
    bundle, now = remote_start_gate(gws, monkeypatch)
    assert_start_gate_wait(bundle, now)
    why = panel.reduce_panel(bundle, now=now)['sections']['ready'][0]['non_running_reason']
    assert why['observer'] != bundle['gateway']


def test_models_are_serializable_for_actual_renderer(kanban_home, tmp_path):
    with kbc.connect_closing() as conn:
        bundle, now = actual_refusal(conn)
        models = {name: {'model': panel.reduce_panel(bundle, now=clock, wire=True), 'now': clock}
                  for name, clock in [('current', now), ('expired', now + 181)]}
        # A caller may collect these real models for a mounted DOM contract.
        output = tmp_path / 'nonrunning-models.json'
        output.write_text(json.dumps(models))
        assert json.loads(output.read_text())['current']['model']['sections']['stuck']
