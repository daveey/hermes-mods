"""Runtime renewal must re-read evidence without renewing board trust."""
from copy import deepcopy

from test_feed import make


def setup():
    f, r, pushed, clock = make()
    r.links = {}
    r.finished = {}
    r.panel_inputs = lambda *args: {'requests': [], 'outcome_json': {}, 'processes': []}
    r.decision_inputs = lambda *args: {'requests': [], 'outcome_json': {}}
    r.panel_files_stamp = lambda: 1
    r.decision_store_stamp = lambda: 1
    r.panel_stamps = lambda ids: {s: 1 for s in ids}
    r.runtime_stamp = lambda: 1
    calls = []
    def capture(*args):
        calls.append(args)
        return {'outcome_json': {}, 'processes': [{'id': 'proc_owned', 'title': 'sleep 600',
                'execution_observations': [], 'probe_at': clock['now'] - 2}]}
    r.runtime_inputs = capture
    f.changes(['S1'])
    return f, r, clock, calls


def test_runtime_capture_does_not_certify_the_sidebar_pre_birth_cache(monkeypatch):
    import pytest
    import time
    from test_inputs import inputs
    producer = pytest.importorskip('hermes_cli.process_session_activity')
    observations = pytest.importorskip('hermes_cli.kanban_execution_observations')
    monkeypatch.setattr(observations, 'local_scope', lambda: ('edge', 'default'))
    monkeypatch.setattr(producer, '_cache', (time.monotonic() + 100, {}))
    monkeypatch.setattr(producer, '_details', {})
    monkeypatch.setattr(producer, '_evidence', {})
    calls = []
    def capture():
        calls.append('kernel probe')
        producer._details = {'proc_birth': {'id': 'proc_birth', 'command': 'sleep 600'}}
        producer._evidence = {'proc_birth': {
            'id': 'proc_birth', 'pid': 12345, 'host_start_time': 999,
            'pid_scope': 'host', 'profile': 'default', 'source': 'process.registry',
            'observed_at': time.time(), 'owners': frozenset(['S1'])}}
        return {'S1': frozenset(['proc_birth'])}
    monkeypatch.setattr(producer, '_collect', capture)
    rows = inputs.process_inputs('S1', ['S1'], 'default')
    assert calls == ['kernel probe']
    assert rows[0]['title'] == 'sleep 600'
    assert rows[0]['execution_observations'][0]['state'] == 'running'


def test_live_process_renews_from_probe_and_stable_stamps_do_not_skip_it():
    f, r, clock, calls = setup()
    assert f.refresh_decisions() == 1
    baseline = f.bundles['S1']['bundle']['computed_at']
    assert len(calls) == 1
    clock['now'] += 46
    assert f.refresh_decisions() == 1
    b = f.bundles['S1']['bundle']
    assert len(calls) == 2
    assert b['processes'][0]['probe_at'] == clock['now'] - 2
    assert b['computed_at'] == baseline
    assert f._runtime_retry_at == clock['now'] + 45


def test_exit_removes_process_and_disarms_reprobe_without_board_pass():
    f, r, clock, calls = setup()
    f.refresh_decisions()
    r.runtime_inputs = lambda *args: {'outcome_json': {}, 'processes': []}
    clock['now'] += 46
    f.refresh_decisions()
    assert f.bundles['S1']['bundle']['processes'] == []
    assert f._runtime_retry_at is None
    clock['now'] += 600
    assert f.refresh_decisions() == 0
    assert len(calls) == 1


def test_process_birth_stamp_bypasses_unchanged_decision_stamps():
    f, r, clock, calls = setup()
    r.runtime_stamp = lambda: None
    f.refresh_decisions()
    assert calls == []
    r.runtime_stamp = lambda: 2
    f.refresh_decisions()
    assert len(calls) == 1
    assert f.bundles['S1']['bundle']['processes'][0]['title'] == 'sleep 600'


def test_probe_failure_keeps_old_evidence_and_uses_bounded_retry():
    f, r, clock, calls = setup()
    f.refresh_decisions()
    old = deepcopy(f.bundles['S1']['bundle']['processes'])
    def fail(*args):
        raise OSError('checkpoint unavailable')
    r.runtime_inputs = fail
    clock['now'] += 46
    f.refresh_decisions()
    assert f.bundles['S1']['bundle']['processes'] == old
    assert 'S1' in f._decision_failures
    assert f._decision_retry_at > clock['now']
    assert f._runtime_retry_at is None


def test_old_board_capture_cannot_restore_pre_probe_processes():
    f, r, clock, calls = setup()
    old = deepcopy(f.bundles['S1']['bundle'])
    f.refresh_decisions()
    clock['now'] += 46
    f._publish({'S1': {**old, 'computed_seq': 1000}})
    assert f.bundles['S1']['bundle']['processes'][0]['probe_at'] == 998


def test_process_row_opens_its_owner_qualified_process_not_the_parent_chat():
    from test_model import model, bundle, project, NOW
    raw = {'id': 'proc_owned', 'work_id': 'proc_owned', 'title': 'sleep 600',
           'gateway': 'owner', 'profile': 'work',
           'execution_observations': [{'state': 'running', 'identity': {
               'kind': 'process', 'work_id': 'proc_owned', 'gateway': 'owner', 'profile': 'work'}}]}
    b = bundle(session='owner-chat', processes=[raw])
    view = model.reduce_panel(b, now=NOW, projector=project)
    row = view['sections']['running'][0]
    assert row['title'] == 'sleep 600'
    assert row['detail'] == 'running'
    assert row['actions'] == [{'kind': 'open_process', 'label': 'Open process',
        'target': {'id': 'proc_owned', 'session': 'owner-chat', 'gateway': 'owner', 'profile': 'work'}}]
    for missing in ('work_id', 'gateway', 'profile'):
        bad = dict(raw, **{missing: ''})
        result = model.reduce_panel(bundle(session='owner-chat', processes=[bad]), now=NOW, projector=project)
        rows = result['sections']['running'] + result['sections']['waiting']
        assert not rows[0]['actions'], missing
    result = model.reduce_panel(bundle(processes=[raw]), now=NOW, projector=project)
    assert not result['sections']['running'][0]['actions']
