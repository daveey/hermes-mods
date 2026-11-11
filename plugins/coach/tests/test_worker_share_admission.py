"""Admission pressure must spill below the nominal run cap too."""
from datetime import datetime

import pytest
from test_worker_share_watch import w, home, registry, go, NOW_MS
from test_worker_share_spill import audit, Mover


def log_refusal(home, at):
    path = home / 'logs' / 'agent.log'
    path.parent.mkdir(exist_ok=True)
    stamp = datetime.fromtimestamp(at / 1000).strftime('%Y-%m-%d %H:%M:%S,%f')[:-3]
    path.write_text(f'{stamp} INFO hermes_cli.kanban_admission: Hub admission refused Kanban claim t_test: hub_cpu_unknown\n')


@pytest.mark.parametrize('pressure', ['cap', 'drain', 'admission'])
def test_each_pressure_spills_only_eligible(home, pressure):
    audit(home, 't_a1')
    audit(home, 't_a2', 'home gateway marker')
    if pressure == 'drain':
        path = home / 'core-update' / 'drain-hold'
        path.parent.mkdir()
        path.touch()
    if pressure == 'admission':
        log_refusal(home, NOW_MS - 1000)
    out = go(home, Mover(home), registry(hub_runs=7 if pressure == 'cap' else 3), 0, streak_needed=1)
    assert out['sample']['starved']
    assert out['moved'] == [{'task_id': 't_a1', 'machine': 'worker-1'}]
    assert pressure in out['sample']['hub_pressure']


@pytest.mark.parametrize('age', [600001, -1000])
def test_outside_window_refusals_do_not_spill(home, age):
    audit(home, 't_a1')
    log_refusal(home, NOW_MS - age)
    out = go(home, Mover(home), registry(hub_runs=3), 0, streak_needed=1)
    assert not out['sample']['starved']
    assert out['moved'] == []


def test_recent_refusal_expires_and_missing_logs_are_ok(home):
    log_refusal(home, NOW_MS - 1000)
    assert w.hub_pressure(home, NOW_MS) == ['admission']
    assert w.hub_pressure(home, NOW_MS + 600000) == []
    (home / 'logs' / 'agent.log').unlink()
    assert w.hub_pressure(home, NOW_MS) == []
