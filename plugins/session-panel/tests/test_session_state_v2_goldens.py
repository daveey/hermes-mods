"""Golden manifest follows core's shared scenarios, not a second scenario list."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from session_state_v2_fixtures import BASELINE, core_source, run_all


@pytest.fixture(scope='module')
def actual():
    if core_source() is None:
        pytest.skip('compatible core shared fixtures unavailable')
    return run_all()


def test_exact_shared_coverage(actual):
    shared = json.loads((core_source() / 'tests/fixtures/session-state-v2/scenarios.json').read_text())
    ids = [case['id'] for case in shared['scenarios']]
    assert actual['coverage'] == ids
    assert set(actual['cases']) == set(ids)
    assert len(ids) == len(set(ids))


def test_real_feed_model_goldens(actual):
    assert actual == json.loads(BASELINE.read_text())


def test_real_request_bindings(actual):
    for case in actual['cases'].values():
        for row in case['model']['sections']['needs_you']:
            request = next(r for r in case['requests'] if r['requestId'] == row['request_id'])
            assert request['presentation']['kind'] == 'kanban-question'
            action = next(a for a in row['actions'] if a['kind'] == 'decide')
            assert action['owner'] == request['owner']
            assert action['revision'] == request['revision'] == row['request_revision']
            assert [o['choice_id'] for o in row['options']] == [c['id'] for c in request['choices']]


def test_triage_boundary_does_not_invent_human_permission(actual):
    for case in actual['cases'].values():
        rows = [r for values in case['model']['sections'].values() for r in values]
        for row in rows:
            if row.get('attention') == 'triage_overdue':
                assert row not in case['model']['sections']['needs_you']
                assert not any(a['kind'] == 'decide' for a in row['actions'])


def test_spawned_producer_never_writes_the_invoking_users_home(actual, tmp_path):
    """The Desktop-plugin test spawns this script outside pytest's home isolation."""
    home, hermes_home = tmp_path / 'home', tmp_path / 'hermes-home'
    home.mkdir()
    hermes_home.mkdir()
    env = {**os.environ, 'HOME': str(home), 'HERMES_HOME': str(hermes_home),
           'HERMES_CORE_SOURCE': str(core_source())}
    script = Path(__file__).with_name('session_state_v2_fixtures.py')
    out = subprocess.run([sys.executable, str(script)], env=env, check=True, timeout=300,
                         capture_output=True, text=True).stdout
    assert list(home.iterdir()) == [] and list(hermes_home.iterdir()) == []
    assert json.loads(out) == actual
