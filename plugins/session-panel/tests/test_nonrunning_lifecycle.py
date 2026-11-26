"""Real disposable lifecycle producers -> actual source/wait reader -> reducer."""
import importlib.util
from dataclasses import asdict
from pathlib import Path
import time

import pytest

spec = importlib.util.spec_from_file_location('lifecycle_producers', Path(__file__).with_name('test_nonrunning_producer.py'))
assert spec is not None and spec.loader is not None
producers = importlib.util.module_from_spec(spec)
spec.loader.exec_module(producers)
kanban_home = producers.kanban_home
panel = producers.panel
kb = producers.kb


def read_bundle(conn, tid, now):
    from hermes_cli.kanban_session_wait_rows import _local_wait_info
    bundle = producers.diagnostic_bundle(conn, tid, 'fixture', now)
    row = bundle['tasks'][0]
    row.update(asdict(kb.get_task(conn, tid)))
    waits = _local_wait_info(conn, [tid], now=now)
    if tid in waits:
        row['wait'] = waits[tid]
    bundle['cards'][tid] = {'detail': {'events': [asdict(e) for e in kb.list_events(conn, tid)]}}
    return bundle


def actual_capacity(conn):
    tid = kb.create_task(conn, title='Profile slot wait', assignee='default')
    captured = producers.capacity.capture(conn)
    result = producers.dispatch.DispatchResult()
    row = conn.execute('SELECT * FROM tasks WHERE id=?', (tid,)).fetchone()
    assert not producers.dispatch._dispatch_lane_task(
        conn, row, 'default', result, lane='ready', dry_run=True, ttl_seconds=60,
        board='default', failure_limit=3, spawn_fn=lambda *_: None,
        per_profile_cap=1, per_profile_running={'default': 1})
    assert result.skipped_per_profile_capped == [(tid, 'default', 1)]
    assert producers.capacity.record(conn, result, board='default', machine='fixture', captured=captured)
    now = captured['observed_at'] + 1
    return read_bundle(conn, tid, now), now


def actual_schedule(conn):
    from datetime import datetime, timezone
    now = time.time()
    tid = kb.create_task(conn, title='Cooldown wait', assignee='default')
    wake = datetime.fromtimestamp(now + 3600, timezone.utc).isoformat()
    assert kb.schedule_task(conn, tid, reason='SCHEDULED_UNTIL=' + wake + ' — cooldown',
                            clock_only=True, awaiting={'kind': 'clock', 'why': 'cooldown window'})
    return read_bundle(conn, tid, now + 1), now + 1


def actual_human(conn):
    tid = kb.create_task(conn, title='Owner decision', assignee='default')
    request = {'action': 'Reply A or B. Unblock = build the pick; archive = drop it.',
               'why_human': 'Only the owner can choose the release audience.',
               'interaction': 'reply', 'scope': 'Release audience',
               'evidence': 'Two equally suitable audiences require a choice.'}
    assert kb.block_task(conn, tid, kind='needs_input', reason='Choose the release audience', human_request=request)
    now = time.time() + 1
    return read_bundle(conn, tid, now), now


def actual_startup(conn, workspace):
    from hermes_cli import kanban_source_launch as launch
    tid = kb.create_task(conn, title='Startup failure', assignee='default')
    claimed = kb.claim_task(conn, tid)
    assert claimed is not None
    result = producers.dispatch.DispatchResult()
    def unavailable(*args, **kwargs):
        raise OSError('fixture executable unavailable')
    assert not launch.launch(conn, claimed, workspace, None, None, lane='ready', board='default',
                             failure_limit=1, spawn_fn=unavailable, result=result, count_spawn=lambda _: None)
    assert result.auto_blocked == [tid]
    assert kb.get_task(conn, tid).status == 'triage'
    now = time.time() + 1
    return read_bundle(conn, tid, now), now


def actual_unobserved_review(conn):
    # Real ready accounting refusal followed by the native review transition.
    # A replacement lifecycle must never inherit the old blocked diagnosis.
    bundle, _ = producers.actual_refusal(conn)
    tid = bundle['tasks'][0]['id']
    assert kb.request_review(conn, tid, summary='Implementation ready for independent review')
    now = time.time() + 1
    bundle = read_bundle(conn, tid, now)
    assert bundle['tasks'][0]['status'] == 'review'
    raw = bundle['tasks'][0]['execution_observations'][0]
    assert raw['non_running_reason']['freshness'] == 'unknown'
    assert raw['non_running_reason']['unknown_reason'] == 'lifecycle_reason_unobserved'
    return bundle, now


def test_real_review_transition_cannot_reuse_prior_ready_refusal(kanban_home):
    with producers.kbc.connect_closing() as conn:
        bundle, now = actual_unobserved_review(conn)
        model = panel.reduce_panel(bundle, now=now)
        assert not model['sections']['needs_you'] and not model['sections']['stuck']
        view = model['sections']['waiting'][0]
        assert 'execution reason unknown' in view['detail']
        assert 'usage_accounting_pending' not in str(model)
        assert 'waiting for review' not in view['detail']


def test_real_capacity_gate_is_ready_wait_not_blocked(kanban_home):
    with producers.kbc.connect_closing() as conn:
        bundle, now = actual_capacity(conn)
        model = panel.reduce_panel(bundle, now=now)
        assert not model['sections']['stuck'] and not model['sections']['needs_you']
        view = model['sections']['ready'][0]
        assert view['non_running_reason']['family'] == 'capacity'
        assert view['non_running_reason']['scope'] == 'observing_dispatcher'
        assert 'Waiting for worker capacity' in view['detail']


def test_real_schedule_preserves_actual_clock_not_capacity(kanban_home):
    with producers.kbc.connect_closing() as conn:
        bundle, now = actual_schedule(conn)
        model = panel.reduce_panel(bundle, now=now)
        assert not model['sections']['stuck'] and not model['sections']['needs_you']
        view = model['sections']['waiting'][0]
        assert view['wait_kind'] == 'clock'
        assert 'left (until' in view['detail']
        assert 'worker' not in view['detail']


def test_real_human_block_keeps_the_authentic_ask(kanban_home):
    with producers.kbc.connect_closing() as conn:
        bundle, now = actual_human(conn)
        model = panel.reduce_panel(bundle, now=now)
        assert not model['sections']['stuck'] and not model['sections']['ready']
        assert len(model['sections']['needs_you']) == 1
        assert 'Reply A or B' in str(model['sections']['needs_you'][0])


def test_real_startup_rejection_exposes_failure_not_unspecified_triage(kanban_home, tmp_path):
    with producers.kbc.connect_closing() as conn:
        bundle, now = actual_startup(conn, tmp_path / 'workspace')
        model = panel.reduce_panel(bundle, now=now)
        assert not model['sections']['needs_you']
        assert 'startup' in str(model).lower()
        assert 'worker could not be launched' in str(model)
        assert 'Inspect the recorded launch failure' in str(model)
        assert 'this chat’s agent' not in str(model)
        assert "this chat's agent" not in str(model)
        assert 'waiting to be specified' not in str(model)


@pytest.mark.parametrize('kind', ['schedule', 'human', 'startup'])
def test_normal_release_and_claim_clear_prior_wait(kanban_home, tmp_path, kind):
    with producers.kbc.connect_closing() as conn:
        if kind == 'startup':
            bundle, now = actual_startup(conn, tmp_path / 'workspace')
        else:
            bundle, now = globals()['actual_' + kind](conn)
        tid = bundle['tasks'][0]['id']
        assert kb.unblock_task(conn, tid)
        assert kb.claim_task(conn, tid) is not None
        current = read_bundle(conn, tid, now + 1)
        assert current['tasks'][0]['status'] == 'running'
        assert not current['tasks'][0].get('wait')
        model = panel.reduce_panel(current, now=now + 1)
        assert not model['sections']['needs_you']
        assert 'startup failure' not in str(model)
        assert 'left (until' not in str(model)
        assert 'Reply A or B' not in str(model)
