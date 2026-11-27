"""False-positive classes found in the shadow soak: nudges on parks with a healthy live producer,
and a no-wake overdue finding on a superseded park whose core backstop has not come due yet."""
import json

import pytest
pytest.importorskip('hermes_cli.kanban_db')
from sr_helpers import Env, fixtures
F = fixtures()


@pytest.fixture
def env(tmp_path, monkeypatch):
    e = Env(tmp_path, monkeypatch)
    e.now = int(e.now)
    return e


def long_wait_hits(env, tid):
    return env.findings(rule='BS-long-wait', card=tid)


RUN = 'https://github.com/example/project/actions/runs/123'
PR = 'https://github.com/example/project/pull/205'


def watched_wait(env, monkeypatch, kind='external', ref='job', park_check='python3 probe.py', waited_s=40000,
                 event=True, event_at=-100, **watch):
    monkeypatch.setattr(env.scan_mod.Sources, 'producer_state', lambda *a: {'state': 'not_resolvable', 'happened': False})
    tid = F.card(env.conn, 'Waiting for producer', status='scheduled')
    F.age_all_events(env.conn, tid, env.now - waited_s)
    park = {'awaiting': {'kind': kind, 'ref': ref}, 'reason': 'producer', 'wake_at': env.now + 86400}
    if park_check:
        park['wake_check'] = park_check
    F.event(env.conn, tid, 'scheduled', park, at=env.now - waited_s)
    payload = {'schema': 1, 'adapter': 'shell', 'health': 'ok', 'state': 'external_unknown',
               'observed_at': env.now - 100, 'next_due_at': env.now + 800, 'every': 900,
               'consecutive_failures': 0, 'failing_since': None}
    payload.update(watch)
    if event:
        F.event(env.conn, tid, 'external_watch', payload, at=env.now + event_at)
    return tid


@pytest.mark.parametrize('kind', ['external', 'ci', 'deploy'])
@pytest.mark.parametrize('watch', [
    {},                                                  # shell probe ok, condition not yet met
    {'state': 'external_running'},                       # probe reports the producer running
])
def test_shell_watched_live_producer_is_not_a_stalled_park(env, monkeypatch, kind, watch):
    assert long_wait_hits(env, watched_wait(env, monkeypatch, kind=kind, **watch)) == []


@pytest.mark.parametrize('kind', ['external', 'ci', 'deploy'])
def test_shell_probe_reporting_degraded_does_not_hide_a_stall(env, monkeypatch, kind):
    # nothing in core bounds a shell probe's degraded/running, so a wedged deploy must still be nudged
    tid = watched_wait(env, monkeypatch, kind=kind, health='degraded', state='external_running')
    assert len(long_wait_hits(env, tid)) == 1


@pytest.mark.parametrize('ref,adapter,watch', [
    # queued behind saturated runners: the run is alive, health is only degraded
    (RUN, 'github_run', {'health': 'degraded', 'state': 'external_running'}),
    # PR checks still running: native GitHub watcher, no shell command
    (PR, 'github_pr_checks', {'health': 'ok', 'state': 'external_running'}),
])
def test_native_github_watcher_on_the_exact_target_is_not_a_stalled_park(env, monkeypatch, ref, adapter, watch):
    tid = watched_wait(env, monkeypatch, kind='ci', ref=ref, park_check='', adapter=adapter, url=ref, **watch)
    assert long_wait_hits(env, tid) == []


@pytest.mark.parametrize('kw', [
    {'health': 'probe_broken', 'state': 'external_down', 'consecutive_failures': 2, 'failing_since': 1},
    {'health': 'degraded', 'state': 'external_unknown'},
    {'consecutive_failures': 1},
    {'observed_at': 1, 'next_due_at': 2},                # watcher stopped polling long ago
    {'every': 0},
    {'wake_check': 'python3 other.py'},                  # a different probe's receipt
    {'event': False},                                    # no watcher event at all
    {'event_at': -50000},                                # receipt predates the park
])
def test_unhealthy_silent_or_foreign_watcher_is_still_nudged(env, monkeypatch, kw):
    if kw.get('observed_at') == 1:
        kw = {'observed_at': env.now - 9000, 'next_due_at': env.now - 8000}
    assert len(long_wait_hits(env, watched_wait(env, monkeypatch, **kw))) == 1


@pytest.mark.parametrize('kw', [
    {'ref': RUN, 'adapter': 'github_run', 'url': RUN + '9', 'health': 'degraded', 'state': 'external_running'},  # wrong run
    {'ref': 'job', 'adapter': 'shell', 'state': 'external_running'},                                              # shell, no command
])
def test_native_watcher_must_name_the_exact_target(env, monkeypatch, kw):
    tid = watched_wait(env, monkeypatch, kind='ci', park_check='', **kw)
    assert len(long_wait_hits(env, tid)) == 1


def test_adverse_event_after_healthy_watch_keeps_the_nudge(env, monkeypatch):
    tid = watched_wait(env, monkeypatch)
    F.event(env.conn, tid, 'wake_check_broken', {}, at=env.now - 50)
    assert len(long_wait_hits(env, tid)) == 1


def test_overdue_backstop_keeps_the_nudge_despite_a_healthy_watcher(env, monkeypatch):
    tid = watched_wait(env, monkeypatch)
    env.conn.execute("UPDATE task_events SET payload=json_set(payload,'$.wake_at',?) WHERE task_id=? AND kind='scheduled'",
                     (env.now - 1, tid))
    env.conn.commit()
    assert len(long_wait_hits(env, tid)) == 1


def producer_card_wait(env, producer_status, *, wake_in=None, run=False):
    prod = F.card(env.conn, 'Producer', status=producer_status)
    F.age_all_events(env.conn, prod, env.now - 8000)
    if wake_in is not None:
        F.event(env.conn, prod, 'scheduled', {'reason': 'timed', 'wake_at': int(env.now + wake_in)}, at=env.now - 7000)
    if run:
        env.conn.execute("INSERT INTO task_runs (task_id, status, started_at) VALUES (?, 'running', ?)",
                         (prod, int(env.now - 7000)))
        env.conn.commit()
    tid = F.card(env.conn, 'Consumer', status='scheduled')
    F.age_all_events(env.conn, tid, env.now - 40000)
    F.event(env.conn, tid, 'scheduled', {'awaiting': {'kind': 'card', 'ref': prod}, 'reason': 'producer',
                                         'wake_at': env.now + 86400}, at=env.now - 40000)
    return tid


def test_card_waiting_on_a_producer_parked_with_a_future_wake_is_not_stalled(env):
    assert long_wait_hits(env, producer_card_wait(env, 'scheduled', wake_in=3600)) == []


def test_card_waiting_on_a_producer_with_a_live_run_is_not_stalled(env):
    assert long_wait_hits(env, producer_card_wait(env, 'running', run=True)) == []


@pytest.mark.parametrize('status,kw', [
    ('scheduled', {'wake_in': -600}),   # its wake is overdue
    ('scheduled', {}),                  # no timed wake at all
    ('running', {}),                    # status says running, no live run
    ('ready', {}),                      # quiet and unclaimed: existing policy keeps the nudge
    ('triage', {}),
    ('blocked', {}),
])
def test_card_waiting_on_a_producer_that_is_not_live_is_still_nudged(env, status, kw):
    assert len(long_wait_hits(env, producer_card_wait(env, status, **kw))) == 1


def observation_wait(env, monkeypatch, *, wake_in=3000, waited_s=3300, **changes):
    monkeypatch.setattr(env.scan_mod.Sources, 'producer_state', lambda *a: {'state': 'not_resolvable', 'happened': False})
    tid = F.card(env.conn, 'Observe natural bounded rollout', status='scheduled')
    F.age_all_events(env.conn, tid, env.now - waited_s)
    payload = {'awaiting': {'kind': 'deploy', 'ref': 'opaque-rollout'},
               'reason': 'Observe natural rollover at the scheduled checkpoint',
               'wake_at': env.now + wake_in}
    payload.update(changes)
    F.event(env.conn, tid, 'scheduled', payload, at=env.now - waited_s)
    return tid


def test_bounded_unprobed_observation_wait_respects_owner_checkpoint(env, monkeypatch):
    # Natural rollout/sweep was deliberately due in 50m, not stalled after 55m.
    assert long_wait_hits(env, observation_wait(env, monkeypatch)) == []


@pytest.mark.parametrize('wake_in', [0, -1, 3901])
def test_observation_boundary_or_overlong_window_keeps_nudge(env, monkeypatch, wake_in):
    assert len(long_wait_hits(env, observation_wait(env, monkeypatch, wake_in=wake_in))) == 1


@pytest.mark.parametrize('changes', [
    {'wake_check': 'python3 probe.py'},
    {'awaiting': {'kind': 'deploy', 'ref': PR}},
    {'awaiting': {'kind': 'ci', 'ref': 'opaque-rollout'}},
    {'awaiting': {'kind': 'deploy', 'ref': ''}},
    {'wake_at': 'not-a-time'},
])
def test_observation_guard_never_masks_pollable_or_invalid_waits(env, monkeypatch, changes):
    assert len(long_wait_hits(env, observation_wait(env, monkeypatch, **changes))) == 1


@pytest.mark.parametrize('kind', ['wake_check_broken', 'wake_check_lost', 'wake_check_stale', 'condition_met'])
def test_adverse_event_overrides_observation_checkpoint(env, monkeypatch, kind):
    tid = observation_wait(env, monkeypatch)
    F.event(env.conn, tid, kind, {}, at=env.now - 10)
    assert len(long_wait_hits(env, tid)) == 1


def test_completed_producer_overrides_observation_checkpoint(env, monkeypatch):
    tid = observation_wait(env, monkeypatch)
    monkeypatch.setattr(env.scan_mod.Sources, 'producer_state',
                        lambda *a: {'state': 'done', 'happened': True, 'happened_at': env.now - 601})
    assert long_wait_hits(env, tid)[0]['variant'] == 'event_happened'


# ── clock-overdue class: a superseded park is backstopped by core, not unwoken ───────────────────────────────────
def superseded_park(env, parked_s):
    """A spent park (a later run claimed the card) with no replacement park imported yet."""
    tid = F.card(env.conn, 'Superseded park', status='scheduled')
    F.age_all_events(env.conn, tid, env.now - parked_s)
    pid = F.event(env.conn, tid, 'scheduled', {'reason': ''}, at=env.now - parked_s)
    F.event(env.conn, tid, 'claimed', {}, at=env.now - parked_s + 1, run_id=None)
    from hermes_cli import kanban_continuation as kc
    assert kc.park_state(env.conn, tid)['superseded'], 'fixture: core treats this park as superseded'
    return tid


def overdue_hits(env, tid):
    return env.findings(rule='BS-overdue-wake', card=tid)


def test_superseded_park_before_core_backstop_is_not_overdue(env):
    from hermes_cli import kanban_continuation as kc
    tid = superseded_park(env, 2100)  # 35 min: past the 30 min generic limit, before core's grace
    assert 2100 < kc.SUPERSEDED_PARK_GRACE_SECONDS
    assert overdue_hits(env, tid) == []


def test_superseded_park_whose_core_backstop_failed_is_overdue(env):
    from hermes_cli import kanban_continuation as kc
    tid = superseded_park(env, kc.SUPERSEDED_PARK_GRACE_SECONDS + 3600)
    hits = overdue_hits(env, tid)
    assert len(hits) == 1 and hits[0]['variant'] == 'no_wake'
