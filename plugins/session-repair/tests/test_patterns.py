"""One positive and one negative case per broken-session pattern, on a board built with core's kanban_db."""
import json

import pytest

pytest.importorskip('hermes_cli.kanban_db')

from sr_helpers import Env, fixtures  # noqa: E402

F = fixtures()


@pytest.fixture
def env(tmp_path, monkeypatch):
    return Env(tmp_path, monkeypatch)


def one(env, rule, card):
    hits = env.findings(rule=rule, card=card)
    assert len(hits) == 1, (rule, card, env.scan()['findings'], env.last_sources.errors)
    f = hits[0]
    assert f['fingerprint'] == f'{rule}:default/{card}'
    assert f['chat'] == F.CHAT or f['chat'] is None
    return f


def none(env, rule, card):
    hits = env.findings(rule=rule, card=card)
    assert hits == [], hits


# BS-ready-worker-free
def test_ready_card_unclaimed_while_a_slot_is_free(env):
    tid = F.ready_worker_free(env.conn, env.now)
    f = one(env, 'BS-ready-worker-free', tid)
    assert f['variant'] == 'free_slot' and f['fix']['kind'] == 'auto' and f['area'] == 'hermes'
    assert f['evidence']['running'] == 0 and f['evidence']['limit'] == 4
    assert any('dispatch' in c for c in f['fix']['commands'])


def test_ready_local_lane_uses_cores_30_minute_unrecorded_rule(env):
    from hermes_cli import kanban_dispatch_skip as kds
    assert kds.UNRECORDED_AFTER_SECONDS == 30 * 60
    none(env, 'BS-ready-worker-free', F.ready_worker_free(env.conn, env.now, waited_s=20 * 60))


def test_ready_card_not_flagged_when_recent(env):
    none(env, 'BS-ready-worker-free', F.ready_worker_free(env.conn, env.now, waited_s=120))


def test_a_skip_recorded_in_the_episode_explains_the_wait_however_old(env):
    tid = F.ready_with_old_skip(env.conn, env.now)
    from hermes_cli import kanban_db, kanban_queue_position as kqp
    src = env.sources()
    assert (kqp.read(src.conn('default'), kanban_db.get_task(src.conn('default'), tid), 'default') or {}).get('skip'), \
        'fixture: core sees the recorded skip'
    none(env, 'BS-ready-worker-free', tid)


def test_pool_could_run_is_its_own_variant_with_a_replace_card(env, monkeypatch):
    from hermes_cli import kanban_queue_position as kqp
    monkeypatch.setattr(kqp, 'read', lambda conn, task, board=None: {
        'lane': 'local', 'running': 4, 'limit': 4, 'pool_could_run': ['w1']})
    f = one(env, 'BS-ready-worker-free', F.ready_worker_free(env.conn, env.now, waited_s=900))
    assert f['variant'] == 'pool_could_run' and f['fix']['kind'] == 'card'
    assert not any('dispatch' in c for c in f['fix']['commands']), 'never a local dispatch for pool work'


def test_ready_card_not_flagged_when_every_slot_is_busy(tmp_path, monkeypatch):
    env = Env(tmp_path, monkeypatch, max_in_progress=1)
    F.running_healthy(env.conn, env.now)  # takes the one slot
    none(env, 'BS-ready-worker-free', F.ready_worker_free(env.conn, env.now))


def test_ready_card_holding_a_leftover_claim(env):
    f = one(env, 'BS-ready-worker-free', F.ready_leftover_claim(env.conn, env.now))
    assert f['variant'] == 'leftover_claim' and f['fix']['kind'] == 'card'


# BS-overdue-wake
def test_clock_wake_past_due(env):
    f = one(env, 'BS-overdue-wake', F.overdue_clock(env.conn, env.now))
    assert f['variant'] == 'clock_overdue' and f['evidence']['late_s'] >= 3600
    assert f['fix']['kind'] == 'auto' and 'unblock' in f['fix']['commands'][0]


def test_clock_wake_only_slightly_late_is_fine(env):
    none(env, 'BS-overdue-wake', F.overdue_clock(env.conn, env.now, late_s=600))


@pytest.mark.parametrize('factory', [F.overdue_clock, F.overdue_probe, F.scheduled_no_wake])
def test_reserved_final_run_is_not_an_overdue_wake(env, factory):
    from hermes_cli import kanban_continuation as kc
    tid = factory(env.conn, env.now)
    park = kc.park_state(env.conn, tid)
    F.event(env.conn, tid, 'terminal_run_reserved', {'park_event_id': park['event_id']}, at=env.now - 60)
    assert kc._reserved_for(env.conn, tid, park), 'fixture: core preserves the exact parked final run'
    none(env, 'BS-overdue-wake', tid)


def test_reservation_on_older_park_does_not_hide_a_genuinely_overdue_clock(env):
    from hermes_cli import kanban_continuation as kc
    tid = F.overdue_clock(env.conn, env.now)
    old = kc.park_state(env.conn, tid)
    F.event(env.conn, tid, 'terminal_run_reserved', {'park_event_id': old['event_id']}, at=env.now - 120)
    F.event(env.conn, tid, 'scheduled', {'wake_at': int(env.now - 3600)}, at=env.now - 90)
    assert not kc._reserved_for(env.conn, tid, kc.park_state(env.conn, tid))
    assert one(env, 'BS-overdue-wake', tid)['variant'] == 'clock_overdue'


def test_scheduled_with_no_wake_published(env):
    f = one(env, 'BS-overdue-wake', F.scheduled_no_wake(env.conn, env.now))
    assert f['variant'] == 'no_wake' and f['fix']['kind'] == 'card'


def test_scheduled_with_no_wake_just_parked_is_fine(env):
    none(env, 'BS-overdue-wake', F.scheduled_no_wake(env.conn, env.now, parked_s=300))


def test_wake_probe_past_its_next_run(env):
    f = one(env, 'BS-overdue-wake', F.overdue_probe(env.conn, env.now))
    assert f['variant'] == 'probe_overdue' and f['evidence']['probe_cmd'] == 'true'
    assert f['fix']['kind'] == 'card', 'never an automatic unblock before the probe is run'
    assert f['fix']['commands'][0] == 'true' and 'unblock' in f['fix']['commands'][1], 'probe first, then unblock'


def test_wake_probe_on_time_is_fine(env):
    none(env, 'BS-overdue-wake', F.overdue_probe(env.conn, env.now, late_s=-300))


# BS-unexplained-wait
def test_blocked_with_no_reason(env):
    f = one(env, 'BS-unexplained-wait', F.blocked_no_reason(env.conn, env.now))
    assert f['variant'] == 'no_reason'


def test_no_reason_has_its_own_30_minute_threshold(env):
    assert env.scan_mod.NO_REASON_S == 30 * 60
    none(env, 'BS-unexplained-wait', F.blocked_no_reason(env.conn, env.now, waited_s=20 * 60))


def test_a_reason_given_as_error_is_a_reason(env):
    none(env, 'BS-unexplained-wait', F.blocked_reason_in_error(env.conn, env.now))


def test_wait_vocabulary_comes_from_core(env):
    from hermes_cli import kanban_wait_info as kwi
    assert env.scan_mod._core_wait() == (kwi.PARK_EVENT_KINDS, kwi.AGENT_BLOCK_KINDS)
    assert not hasattr(env.scan_mod, 'AGENT_BLOCK_KINDS') and not hasattr(env.scan_mod, 'PARK_EVENTS')


def test_blocked_with_an_invalid_human_request(env):
    f = one(env, 'BS-unexplained-wait', F.blocked_invalid_ask(env.conn, env.now))
    assert f['variant'] == 'invalid_human_request' and f['fix']['kind'] == 'card'


def test_blocked_on_a_valid_ask_is_the_users_move_not_a_finding(env):
    none(env, 'BS-unexplained-wait', F.blocked_valid_ask(env.conn, env.now))


def test_agent_owned_block_stalled_over_two_hours(env):
    f = one(env, 'BS-unexplained-wait', F.blocked_agent_owned(env.conn, env.now))
    assert f['variant'] == 'agent_owned_stalled'


def test_agent_owned_block_under_two_hours_is_fine(env):
    none(env, 'BS-unexplained-wait', F.blocked_agent_owned(env.conn, env.now, waited_s=3600))


def test_new_triage_cannot_inherit_previous_schedule_age(env):
    tid = F.card(env.conn, 'New triage after a live run', status='triage', block_kind='transient')
    F.age_all_events(env.conn, tid, env.now - 26 * 3600)
    F.event(env.conn, tid, 'scheduled', {'reason': 'old scheduled reason'}, at=env.now - 25 * 3600)
    F._run(env.conn, tid, env.now, started_s=13 * 60, status='completed')
    F.event(env.conn, tid, 'gave_up', {'reason': 'new triage'}, at=env.now - 12 * 60)
    none(env, 'BS-unexplained-wait', tid)
    # A genuinely abandoned current triage still surfaces, with the new reason.
    hits = env.findings(rule='BS-unexplained-wait', card=tid, now=env.now + 3 * 3600)
    assert len(hits) == 1 and hits[0]['evidence']['reason'] == 'new triage'
    assert hits[0]['evidence']['waiting_s'] < 4 * 3600


def test_retained_spend_hold_is_explained_and_never_needs_unblock(env):
    tid = F.card(env.conn, 'Explicit retained spend HOLD', status='triage', block_kind='transient')
    F.age_all_events(env.conn, tid, env.now - 27 * 3600)
    F.event(env.conn, tid, 'gave_up', {'reason': 'old wait'}, at=env.now - 26 * 3600)
    F._run(env.conn, tid, env.now, started_s=4 * 3600, status='completed')
    F.event(env.conn, tid, 'spend_budget_triaged', {'reason': 'Spend limit reached'}, at=env.now - 3 * 3600)
    F.event(env.conn, tid, 'spend_budget_decision_ready', {'producer': {'kind': 'chats'}}, at=env.now - 3 * 3600)
    F.event(env.conn, tid, 'decision_retained', {'disposition': {'reason': 'decision_pending'},
            'summary': 'HOLD pending independent prerequisites; no new allowance'}, at=env.now - 2 * 3600)
    before = env.conn.total_changes
    none(env, 'BS-unexplained-wait', tid)
    assert env.conn.total_changes == before
    assert env.conn.execute('SELECT status,block_kind FROM tasks WHERE id=?', (tid,)).fetchone()[:] == ('triage', 'transient')


def test_long_live_run_time_is_not_wait_time(env):
    from hermes_cli import kanban_db
    tid = F.blocked_agent_owned(env.conn, env.now, waited_s=26 * 3600)
    rid = F._run(env.conn, tid, env.now, started_s=5 * 3600, status='completed')
    env.conn.execute('UPDATE task_runs SET ended_at=? WHERE id=?', (int(env.now - 300), rid))
    env.conn.commit()
    card = env.scan_mod.Card(tid, 'default', F.CHAT, kanban_db.get_task(env.conn, tid), env.conn,
                            {'wait': {'agent_owned': True, 'waiting_since': env.now - 26 * 3600}})
    assert env.scan_mod.rule_unexplained_wait(card, env.sources()) is None


def test_stale_display_age_is_fenced_by_latest_admission(env):
    from hermes_cli import kanban_db
    tid = F.blocked_agent_owned(env.conn, env.now, waited_s=26 * 3600)
    F._run(env.conn, tid, env.now, started_s=12 * 60, status='completed')
    card = env.scan_mod.Card(tid, 'default', F.CHAT, kanban_db.get_task(env.conn, tid), env.conn,
                            {'wait': {'agent_owned': True, 'waiting_since': env.now - 26 * 3600}})
    assert env.scan_mod.rule_unexplained_wait(card, env.sources()) is None


# BS-parent-done
def test_todo_whose_parents_finished(env):
    f = one(env, 'BS-parent-done', F.todo_parents_done(env.conn, env.now))
    assert f['variant'] == 'parents_done' and f['fix']['kind'] == 'card', 'a residual core miss: a card, not a promote'


def test_a_cancelled_parent_does_not_satisfy_core_promotion(env):
    none(env, 'BS-parent-done', F.todo_parent_cancelled(env.conn, env.now))


def test_unexplained_containment_hold_never_requests_generic_unblock(env, monkeypatch):
    from hermes_cli import kanban_containment_connections as containment
    tid = F.blocked_agent_owned(env.conn, env.now, waited_s=30 * 3600)
    monkeypatch.setattr(containment, 'held', lambda conn, task_id: task_id == tid)
    none(env, 'BS-unexplained-wait', tid)


def test_core_deliberate_holds_are_not_findings(env, monkeypatch):
    none(env, 'BS-parent-done', F.blocked_dependency_breaker(env.conn, env.now))  # recovery_held (breaker)
    tid = F.todo_parents_done(env.conn, env.now)
    from hermes_cli import kanban_containment_connections as containment
    monkeypatch.setattr(containment, 'held', lambda conn, task_id: task_id == tid)
    none(env, 'BS-parent-done', tid)


@pytest.mark.parametrize('status', ['done', 'archived'])
@pytest.mark.parametrize('cause', ['scope_stopped', 'safety_refusal'])
def test_terminal_but_restricted_parent_does_not_authorize_promotion(env, status, cause):
    from hermes_cli import kanban_db
    tid = F.todo_parents_done(env.conn, env.now)
    pid = kanban_db.parent_states(env.conn, tid)[0][0]
    env.conn.execute('UPDATE tasks SET status=? WHERE id=?', (status, pid))
    F.event(env.conn, pid, 'spend_budget_triaged', {'cause': cause}, at=env.now - 1800)
    assert kanban_db.unsatisfied_parents(env.conn, tid)  # real core, not a mocked status
    before = env.conn.total_changes
    none(env, 'BS-parent-done', tid)
    assert env.conn.total_changes == before


def test_worker_copies_are_skipped_by_every_rule(env, monkeypatch):
    tids = [F.todo_parents_done(env.conn, env.now), F.overdue_clock(env.conn, env.now),
            F.blocked_no_reason(env.conn, env.now), F.running_quiet(env.conn, env.now)]
    from hermes_cli import kanban_wake_check
    monkeypatch.setattr(kanban_wake_check, 'foreign_ids', lambda conn, own=None: set(tids))
    out = env.scan()
    assert [f for f in out['findings'] if f['card'] in tids] == []


def test_todo_whose_parent_just_finished_is_fine(env):
    none(env, 'BS-parent-done', F.todo_parents_done(env.conn, env.now, done_s=120))


def test_todo_with_an_open_parent_is_fine(env):
    none(env, 'BS-parent-done', F.todo_parent_open(env.conn, env.now))


def test_park_awaiting_a_finished_card(env):
    tid, awaited = F.scheduled_awaiting_done(env.conn, env.now)
    f = one(env, 'BS-parent-done', tid)
    assert f['variant'] == 'awaited_done' and f['evidence']['awaited'] == awaited


def test_park_awaiting_a_card_that_just_finished_is_fine(env):
    tid, _ = F.scheduled_awaiting_done(env.conn, env.now, done_s=60)
    none(env, 'BS-parent-done', tid)


# BS-stuck-run
def test_running_with_no_event_for_an_hour(env):
    f = one(env, 'BS-stuck-run', F.running_quiet(env.conn, env.now))
    assert f['variant'] == 'quiet' and f['fix']['kind'] == 'card'


def test_running_whose_lease_expired(env):
    f = one(env, 'BS-stuck-run', F.running_lease_expired(env.conn, env.now))
    assert f['variant'] == 'lease_expired' and f['evidence']['lease_holder'] == 'worker-x'


def test_running_with_no_claim_lease_or_run(env):
    assert one(env, 'BS-stuck-run', F.running_no_claim(env.conn, env.now))['variant'] == 'no_claim'


def test_running_whose_claim_expired(env):
    assert one(env, 'BS-stuck-run', F.running_claim_expired(env.conn, env.now))['variant'] == 'claim_expired'


def test_running_and_heartbeating_is_fine(env):
    none(env, 'BS-stuck-run', F.running_healthy(env.conn, env.now))


# BS-review-no-reviewer
def test_review_with_no_review_run(env):
    f = one(env, 'BS-review-no-reviewer', F.review_idle(env.conn, env.now))
    assert f['evidence']['last_review_event'] == 'review_requested'


def test_review_with_review_dispatch_off_is_a_card_for_its_owner(tmp_path, monkeypatch):
    env = Env(tmp_path, monkeypatch)
    (tmp_path / 'config.yaml').write_text('kanban:\n  max_in_progress: 4\n  review_dispatch: false\n')
    from hermes_cli import kanban_db_dispatch
    monkeypatch.setattr(kanban_db_dispatch, 'review_dispatch_enabled', lambda: False)
    f = one(env, 'BS-review-no-reviewer', F.review_idle(env.conn, env.now))
    assert f['variant'] == 'idle_manual_review' and f['fix']['kind'] == 'card' and f['area'] == 'card'


def test_review_with_a_review_running_is_fine(env):
    none(env, 'BS-review-no-reviewer', F.review_running(env.conn, env.now))


def test_review_just_requested_is_fine(env):
    none(env, 'BS-review-no-reviewer', F.review_idle(env.conn, env.now, idle_s=600))


# BS-broken-chain
def test_waiting_on_a_card_no_board_has(env):
    f = one(env, 'BS-broken-chain', F.missing_parent(env.conn, env.now))
    assert f['variant'] == 'missing_parent' and f['evidence']['missing'] == ['t_00000000deadbeef']


def test_parked_on_a_card_no_board_has(env):
    f = one(env, 'BS-broken-chain', F.awaiting_missing(env.conn, env.now))
    assert f['variant'] == 'missing_awaited' and f['evidence']['missing'] == ['t_00000000feedface']


def test_dependency_cycle(env):
    a, b = F.cycle(env.conn, env.now)
    f = one(env, 'BS-broken-chain', a)
    assert f['variant'] == 'cycle' and set(f['evidence']['cycle']) == {a, b}


def test_waiting_on_a_real_parent_is_not_a_broken_chain(env):
    none(env, 'BS-broken-chain', F.todo_parent_open(env.conn, env.now))


# BS-notice-undelivered
def test_notice_whose_closeout_failed_for_good(env):
    F.notice_chat(env.state, 'C1', env.now, age_s=3600, closeout={'state': 'error', 'at': env.now - 3000})
    hits = env.findings(rule='BS-notice-undelivered')
    assert [(h['chat'], h['variant']) for h in hits] == [('C1', 'closeout_error')]
    assert hits[0]['fingerprint'] == 'BS-notice-undelivered:chat/C1' and hits[0]['area'] == 'hermes'
    import shlex
    from hermes_cli._parser import build_top_level_parser
    argv = shlex.split(hits[0]['fix']['commands'][0])
    args = build_top_level_parser()[0].parse_args(argv[1:])  # core's own parser accepts the re-delivery command
    assert args.resume == 'C1' and args.oneshot_exit and args.query.startswith('[Kanban close-out]')


def test_notice_never_claimed_for_hours(env):
    F.notice_chat(env.state, 'C2', env.now, age_s=3 * 3600)
    assert [h['variant'] for h in env.findings(rule='BS-notice-undelivered')] == ['never_claimed']


def test_notice_in_the_retry_policy_or_answered_is_fine(env):
    F.notice_chat(env.state, 'C3', env.now, age_s=600)                                # within the sweeper's grace
    F.notice_chat(env.state, 'C4', env.now, age_s=3 * 3600, followed=True)            # a turn followed it
    F.notice_chat(env.state, 'C5', env.now, age_s=3600,
                  closeout={'state': 'deferred', 'retry_at': env.now + 600, 'attempts': 1})  # retry still due
    F.notice_chat(env.state, 'C6', env.now, age_s=3 * 3600, archived=1)               # archived chat
    F.notice_chat(env.state, 'C7', env.now, age_s=3 * 3600, blocked_on_human=True)    # core never wakes these
    F.notice_chat(env.state, 'C8', env.now, age_s=3 * 3600, closeout={'state': 'started', 'at': env.now - 3 * 3600})
    assert env.findings(rule='BS-notice-undelivered') == []


def test_notice_about_a_card_blocked_on_a_person_is_left_alone(env):
    tid = F.blocked_valid_ask(env.conn, env.now)
    F.notice_chat(env.state, 'C9', env.now, age_s=3 * 3600, card_id=tid)
    assert env.findings(rule='BS-notice-undelivered') == []


def test_subscription_with_an_undelivered_event(env):
    tid = F.blocked_agent_owned(env.conn, env.now, waited_s=600)
    env.state.execute("INSERT INTO sessions (id, title, archived) VALUES ('C10', 'x', 0)")
    env.state.commit()
    env.conn.execute("INSERT INTO kanban_notify_subs (task_id, platform, chat_id, created_at, last_event_id) "
                     "VALUES (?, 'tui', 'C10', ?, 0)", (tid, int(env.now - 7200)))
    env.conn.execute("UPDATE task_events SET created_at = ? WHERE task_id = ?", (int(env.now - 7200), tid))
    env.conn.commit()
    hits = env.findings(rule='BS-notice-undelivered')
    assert [(h['card'], h['chat'], h['variant']) for h in hits] == [(tid, 'C10', 'subscription_lag')]
    env.conn.execute("UPDATE kanban_notify_subs SET last_event_id = (SELECT MAX(id) FROM task_events)")
    env.conn.commit()
    assert env.findings(rule='BS-notice-undelivered') == [], 'delivered: cursor past every event'


# the chain: a card a linked card waits on is judged too
def test_a_chain_card_of_another_chat_is_judged_through_the_linked_card(env):
    parent = F.overdue_clock(env.conn, env.now)
    env.conn.execute("UPDATE tasks SET session_id = NULL WHERE id = ?", (parent,))
    child = F.card(env.conn, 'Waits on the parked parent', status='todo', parents=[parent])
    env.conn.commit()
    hits = env.findings(rule='BS-overdue-wake', card=parent)
    assert len(hits) == 1 and hits[0]['chat'] == F.CHAT, (hits, child)


def test_a_healthy_board_has_no_findings(env):
    F.running_healthy(env.conn, env.now)
    F.todo_parent_open(env.conn, env.now)
    F.blocked_valid_ask(env.conn, env.now)
    out = env.scan()
    assert out['findings'] == [], out
    assert out['chats'] >= 1 and out['cards'] >= 3


# BS-ready-worker-free, worker pool lane: free slots on a fitting machine, shared fairly among the waiters
def test_machine_lane_free_slots_are_alloted_to_the_longest_waiters(env, monkeypatch):
    from hermes_cli import kanban_worker_node as kwn
    from hermes_cli import kanban_queue_position as kqp
    now_ms = int(env.now * 1000)
    machines = {'w1': {'state': 'active', 'hold': False, 'max_card_runs': 4, 'last_seen': now_ms,
                       'metrics': {'active_card_runs': 3, 'drain_active': 0}},
                'w2': {'state': 'active', 'hold': False, 'max_card_runs': 4, 'last_seen': now_ms,
                       'metrics': {'active_card_runs': 0, 'drain_active': 1}},
                'w3': {'state': 'active', 'hold': True, 'max_card_runs': 4, 'last_seen': now_ms,
                       'metrics': {'active_card_runs': 0, 'drain_active': 0}}}
    monkeypatch.setattr(kwn, 'registry', lambda: {'machines': machines, 'me': 'hub'})
    monkeypatch.setattr(kqp, 'read', lambda conn, task, board=None: {
        'lane': 'machine', 'machines': ['w1', 'w2', 'w3'], 'draining': ['w2'], 'held': ['w3']})
    older = F.ready_worker_free(env.conn, env.now, waited_s=3600)
    newer = F.ready_worker_free(env.conn, env.now, waited_s=1800)
    hits = env.findings(rule='BS-ready-worker-free')
    assert [(h['card'], h['variant'], h['evidence']['slot']) for h in hits] == [(older, 'free_worker', 'w1')], \
        'one free slot (w1: 3 of 4), drained w2 and held w3 do not count: only the longest waiter is flagged'
    assert hits[0]['fix']['kind'] == 'card'
    machines['w1']['metrics']['active_card_runs'] = 4
    assert env.findings(rule='BS-ready-worker-free') == [], 'no free slot: a queue, not a finding'
    machines['w1']['metrics']['active_card_runs'] = 2
    machines['w1']['last_seen'] = now_ms - 3600 * 1000
    assert env.findings(rule='BS-ready-worker-free') == [], 'a machine not seen recently is not a free worker'


# the repair session never repairs itself
def test_the_repair_chat_and_its_own_cards_are_never_judged(env):
    F.notice_chat(env.state, 'R1', env.now, age_s=3 * 3600, closeout={'state': 'error'})
    env.state.execute("UPDATE sessions SET title = 'Session repair' WHERE id = 'R1'")
    env.state.commit()
    mine = F.card(env.conn, 'Filed by the repair chat', status='blocked', session='R1')
    F.event(env.conn, mine, 'blocked', {}, at=env.now - 7200)
    ours = F.repair_card(env.conn, env.now)
    out = env.scan()
    assert out['excluded_chats'] == ['R1']
    assert [f for f in out['findings'] if f['chat'] == 'R1' or f['card'] in (mine, ours)] == [], out['findings']


def test_chat_json_names_the_repair_chat_too(env):
    (env.home / 'session-repair').mkdir()
    (env.home / 'session-repair' / 'chat.json').write_text('{"session": "R2"}')
    F.notice_chat(env.state, 'R2', env.now, age_s=3 * 3600, closeout={'state': 'error'})
    assert env.findings(rule='BS-notice-undelivered') == []


def test_no_fix_kind_human_is_emitted(env):
    for build in (F.blocked_invalid_ask, F.blocked_no_reason, F.scheduled_no_wake, F.missing_parent):
        build(env.conn, env.now)
    assert {f['fix']['kind'] for f in env.scan()['findings']} <= {'auto', 'card'}


def test_chats_over_the_cap_are_counted_and_logged(env, monkeypatch, caplog):
    for i in range(3):
        F.card(env.conn, f'c{i}', status='todo', session=f'S{i}')
    monkeypatch.setattr(env.scan_mod, 'MAX_CHATS', 2)
    out = env.scan()
    assert out['truncated_chats'] == 1 and any('MAX_CHATS' in e['error'] for e in out['errors'])


# an open fix card from an earlier run (any day) is named, so the next day comments instead of filing again
def test_a_finding_with_an_open_fix_card_names_it_second_day_no_duplicate(env):
    tid = F.blocked_no_reason(env.conn, env.now)
    fp = f'BS-unexplained-wait:default/{tid}'
    fix = F.card(env.conn, 'Fix: blocked with no reason', status='ready', session='R9')
    env.conn.execute('UPDATE tasks SET idempotency_key = ? WHERE id = ?', (f'session-repair:{fp}:20261120', fix))
    env.conn.commit()
    f = one(env, 'BS-unexplained-wait', tid)
    assert f['fix']['open_fix_card'] == fix and f['evidence']['open_fix_card']['status'] == 'ready'
    assert env.findings(card=fix) == [], 'the fix card itself is ours: never judged'
    env.conn.execute("UPDATE tasks SET status = 'done' WHERE id = ?", (fix,))
    env.conn.commit()
    assert 'open_fix_card' not in one(env, 'BS-unexplained-wait', tid)['fix'], 'a finished fix card: file a new one'


def test_open_fix_card_lookup_is_a_prefix_not_like(env):
    tid = F.blocked_no_reason(env.conn, env.now)
    other = F.card(env.conn, 'Fix for a different fingerprint', status='ready', session='R9')
    env.conn.execute('UPDATE tasks SET idempotency_key = ? WHERE id = ?',
                     (f'session-repair:BS-unexplained-wait:defaultX{tid[1:]}:20261120', other))  # "_" would match "X" in LIKE
    env.conn.commit()
    assert 'open_fix_card' not in one(env, 'BS-unexplained-wait', tid)['fix']


def test_cards_tracked_from_the_ledger_and_the_self_work_intake_are_ours(env):
    a = F.blocked_no_reason(env.conn, env.now)
    b = F.blocked_no_reason(env.conn, env.now)
    (env.home / 'session-repair').mkdir()
    (env.home / 'session-repair' / 'ledger-live.jsonl').write_text(
        json.dumps({'outcome': 'card', 'filed': a}) + '\n' + json.dumps({'outcome': 'queued', 'filed': 'sw-req1'}) + '\n')
    env.conn.execute('CREATE TABLE IF NOT EXISTS self_work_intake (request_id TEXT PRIMARY KEY NOT NULL, '
                     'request_json TEXT NOT NULL, receipt_json TEXT NOT NULL)')
    env.conn.execute('INSERT INTO self_work_intake VALUES (?, ?, ?)',
                     ('sw-req1', json.dumps({'args': {'title': 'x'}}), json.dumps({'task_id': b, 'created': True})))
    env.conn.commit()
    assert env.findings(card=a) == [] and env.findings(card=b) == []


def test_agent_owned_stall_chat_closeout_already_woke_is_skipped(env, monkeypatch):
    import sqlite3
    tid = F.blocked_agent_owned(env.conn, env.now)
    premise = env.sources().bundle(F.CHAT)['tasks'][0]['wait']['premise']
    db = env.home / 'owner-wake.db'
    c = sqlite3.connect(db)
    c.execute('CREATE TABLE fired (card_key TEXT NOT NULL, premise TEXT NOT NULL, session_id TEXT NOT NULL, '
              'claimed_at REAL NOT NULL, result TEXT, route TEXT, supervisor_task TEXT, PRIMARY KEY (card_key, premise))')
    c.execute('INSERT INTO fired VALUES (?, ?, ?, ?, ?, ?, NULL)', (f'default/{tid}', premise, F.CHAT, env.now - 600,
                                                                    'started', 'chat'))
    c.commit()
    monkeypatch.setenv('CHAT_CLOSEOUT_OWNER_WAKE_DB', str(db))
    none(env, 'BS-unexplained-wait', tid)
    c.execute("UPDATE fired SET result = 'too_old'")
    c.commit()
    assert one(env, 'BS-unexplained-wait', tid)['variant'] == 'agent_owned_stalled', 'too_old never woke anyone'
    c.execute("UPDATE fired SET result = 'started', claimed_at = ?", (env.now - 2 * 86400,))
    c.commit()
    assert one(env, 'BS-unexplained-wait', tid), 'a wake more than 24 h ago did not take: report it'


def ledger(env, *rows):
    (env.home / 'session-repair').mkdir(exist_ok=True)
    (env.home / 'session-repair' / 'ledger-live.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in rows))


def test_a_card_the_turn_only_commented_on_or_fixed_stays_judged(env):
    tid = F.blocked_no_reason(env.conn, env.now)
    fp = f'BS-unexplained-wait:default/{tid}'
    ledger(env, {'fingerprint': fp, 'outcome': 'commented', 'filed': tid, 'commented_on': tid},
           {'fingerprint': fp, 'outcome': 'fixed', 'filed': tid}, {'fingerprint': fp, 'outcome': 'moved_on', 'filed': tid})
    assert one(env, 'BS-unexplained-wait', tid), 'only cards the turn created (card/queued) are excluded'


def test_open_fix_card_found_through_the_ledger_and_the_intake(env):
    tid = F.blocked_no_reason(env.conn, env.now)
    fp = f'BS-unexplained-wait:default/{tid}'
    direct = F.card(env.conn, 'Fix filed without the key', status='ready', session='R9')
    ledger(env, {'fingerprint': fp, 'outcome': 'card', 'filed': direct})
    assert one(env, 'BS-unexplained-wait', tid)['fix']['open_fix_card'] == direct
    env.conn.execute("UPDATE tasks SET status = 'done' WHERE id = ?", (direct,))
    via_intake = F.card(env.conn, 'Fix the intake created', status='todo', session='R9')
    env.conn.execute('CREATE TABLE IF NOT EXISTS self_work_intake (request_id TEXT PRIMARY KEY NOT NULL, '
                     'request_json TEXT NOT NULL, receipt_json TEXT NOT NULL)')
    env.conn.execute('INSERT INTO self_work_intake VALUES (?, ?, ?)', ('sw-r2', '{"args": {}}',
                                                                      json.dumps({'task_id': via_intake})))
    env.conn.commit()
    ledger(env, {'fingerprint': fp, 'outcome': 'card', 'filed': direct}, {'fingerprint': fp, 'outcome': 'queued', 'filed': 'sw-r2'})
    f = one(env, 'BS-unexplained-wait', tid)
    assert f['fix']['open_fix_card'] == via_intake and env.findings(card=via_intake) == []


def owner_wake(env, monkeypatch, card, premise, claimed_at, result):
    import sqlite3
    db = env.home / 'owner-wake.db'
    c = sqlite3.connect(db)
    c.execute('CREATE TABLE IF NOT EXISTS fired (card_key TEXT NOT NULL, premise TEXT NOT NULL, session_id TEXT NOT NULL,'
              ' claimed_at REAL NOT NULL, result TEXT, route TEXT, supervisor_task TEXT, PRIMARY KEY (card_key, premise))')
    c.execute('DELETE FROM fired')
    c.execute('INSERT INTO fired VALUES (?, ?, ?, ?, ?, NULL, NULL)', (f'default/{card}', premise, F.CHAT, claimed_at, result))
    c.commit()
    monkeypatch.setenv('CHAT_CLOSEOUT_OWNER_WAKE_DB', str(db))


def test_only_started_routed_or_live_claims_count_as_chat_closeout_handling(env, monkeypatch):
    tid = F.blocked_agent_owned(env.conn, env.now)
    premise = env.sources().bundle(F.CHAT)['tasks'][0]['wait']['premise']
    assert env.scan_mod.closeout_claim_ttl() == 600.0, "read from chat-closeout's owner_wake.py"
    for result, age, skipped in (('routed', 600, True), (None, 60, True), (None, 1200, False),
                                 ('error', 60, False), ('refused', 60, False), ('busy', 60, False)):
        owner_wake(env, monkeypatch, tid, premise, env.now - age, result)
        hits = env.findings(rule='BS-unexplained-wait', card=tid)
        assert (hits == []) == skipped, (result, age, hits)


def test_machine_lane_skip_recorded_in_the_episode_explains_the_wait(env, monkeypatch):
    from hermes_cli import kanban_worker_node as kwn
    from hermes_cli import kanban_queue_position as kqp
    now_ms = int(env.now * 1000)
    monkeypatch.setattr(kwn, 'registry', lambda: {'me': 'hub', 'machines': {'w1': {
        'state': 'active', 'hold': False, 'max_card_runs': 4, 'last_seen': now_ms,
        'metrics': {'active_card_runs': 0, 'drain_active': 0}}}})
    monkeypatch.setattr(kqp, 'read', lambda conn, task, board=None: {'lane': 'machine', 'machines': ['w1']})
    tid = F.ready_with_old_skip(env.conn, env.now)  # the machine-lane place has no `skip`: core's latest() does
    none(env, 'BS-ready-worker-free', tid)


def test_a_timed_skip_whose_until_has_passed_no_longer_explains(env):
    tid = F.ready_worker_free(env.conn, env.now, waited_s=7200)
    F.event(env.conn, tid, 'dispatch_skipped', {'reason': 'pinned_quota_wall', 'detail': 'm (p)',
                                                'until': int(env.now - 600)}, at=env.now - 3000)
    assert one(env, 'BS-ready-worker-free', tid)['variant'] == 'free_slot', 'the hold ended 10 min ago'
    S = env.scan_mod._skip_explains
    assert S({'reason': 'x', 'until': env.now + 600}, env.now) and S({'reason': 'x'}, env.now)
    assert not S({'reason': 'x', 'until': (env.now - 60) * 1000}, env.now), 'a ms stamp in the past'
    assert not S(None, env.now)


def test_ledger_rows_that_are_not_objects_are_ignored(env):
    tid = F.blocked_no_reason(env.conn, env.now)
    (env.home / 'session-repair').mkdir()
    (env.home / 'session-repair' / 'ledger-live.jsonl').write_text('[1, 2]\n"x"\n' + json.dumps(
        {'fingerprint': 'f', 'outcome': 'card', 'filed': tid}) + '\n')
    assert env.sources().tracked() == {'f': {tid}}


# BS-long-wait: fixed acceptance boundary fixtures.
@pytest.mark.parametrize('event_kind', ['wake_check_alive', 'external_watch'])
@pytest.mark.parametrize('happened', [False, True])
def test_future_healthy_park_is_not_stuck_even_if_pr_is_merged(env, monkeypatch, event_kind, happened):
    monkeypatch.setattr(env.scan_mod.Sources, 'producer_state', lambda *a: {'state':'merged','happened':happened})
    tid = F.legacy_probe_wait(env.conn, env.now, waited_s=40000)
    payload = ({'wake_check':'python3 rollout_ready.py'} if event_kind == 'wake_check_alive' else
               {'health':'ok','state':'external_running','observed_at':env.now-20,
                'next_due_at':env.now+880,'every':900,'consecutive_failures':0})
    F.event(env.conn, tid, event_kind, payload, at=env.now-20)
    none(env, 'BS-long-wait', tid)


@pytest.mark.parametrize('condition', ['missing', 'stale', 'broken', 'wrong_command', 'old_episode', 'overdue'])
def test_long_wait_without_live_probe_still_nudged(env, monkeypatch, condition):
    monkeypatch.setattr(env.scan_mod.Sources, 'producer_state', lambda *a: {'state':'not_resolvable','happened':False})
    tid = F.legacy_probe_wait(env.conn, env.now, waited_s=40000)
    if condition != 'missing':
        at = env.now - (30000 if condition == 'stale' else 50000 if condition == 'old_episode' else 20)
        F.event(env.conn, tid, 'wake_check_alive', {'wake_check':
                'python3 other.py' if condition == 'wrong_command' else 'python3 rollout_ready.py'}, at=at)
    if condition == 'broken':
        F.event(env.conn, tid, 'wake_check_broken', {}, at=env.now-10)
    if condition == 'overdue':
        env.conn.execute("UPDATE task_events SET payload=json_set(payload, '$.wake_at', ?) WHERE task_id=? AND kind='scheduled'",
                         (env.now-1,tid))
        env.conn.commit()
    one(env, 'BS-long-wait', tid)

@pytest.mark.parametrize('waited_s,expected', [(3000, 1), (1800, 0)])
def test_long_wait_ci_boundary(env, monkeypatch, waited_s, expected):
    monkeypatch.setattr(env.scan_mod.Sources, 'producer_state', lambda *a: {'state':'pending','happened':False})
    tid = F.long_wait(env.conn, env.now, waited_s=waited_s)
    fs = env.findings(rule='BS-long-wait', card=tid)
    assert len(fs) == expected
    if fs:
        assert fs[0]['fix']['kind'] == 'nudge'


@pytest.mark.parametrize('kind', ['ci', 'deploy', 'external'])
def test_long_wait_completed_green_at_five_minutes(env, monkeypatch, kind):
    monkeypatch.setattr(env.scan_mod.Sources, 'producer_state', lambda *a: {'state':'completed green','happened':True,'happened_at':env.now-601})
    f = one(env, 'BS-long-wait', F.long_wait(env.conn, env.now, kind=kind, waited_s=300))
    assert f['variant']=='event_happened' and f['fix']['kind']=='nudge'


@pytest.mark.parametrize('state', ['pending', 'readback_failed'])
@pytest.mark.parametrize('waited_s,expected', [(300, 0), (3000, 1)])
def test_deploy_wait_readback_age_fallback(env, monkeypatch, state, waited_s, expected):
    calls = []
    def readback(self, kind, ref):
        calls.append((kind, ref))
        return {'state': state, 'happened': False}
    monkeypatch.setattr(env.scan_mod.Sources, 'producer_state', readback)
    tid = F.long_wait(env.conn, env.now, kind='deploy', waited_s=waited_s)
    fs = env.findings(rule='BS-long-wait', card=tid)
    assert calls and calls[0][0] == 'deploy'
    assert len(fs) == expected
    if fs:
        assert fs[0]['variant'] == 'too_long'
        assert fs[0]['evidence']['live_producer']['state'] == state


def test_long_wait_human_ask_and_running_never_nudged(env):
    none(env, 'BS-long-wait', F.blocked_valid_ask(env.conn, env.now))
    none(env, 'BS-long-wait', F.long_wait(env.conn, env.now, status='running'))


@pytest.mark.parametrize('reason,waited_s,expected', [
    ('deploy rollout: waiting for worker update window', 3000, 'deploy'),
    ('CI run pending', 3000, 'ci'),
    ('waiting for vendor ticket', 3000, None),
    ('waiting for vendor ticket', 10900, 'external'),
    ('deploy rollout pending', 1800, None),
])
def test_long_wait_legacy_probe_with_backstop(env, monkeypatch, reason, waited_s, expected):
    monkeypatch.setattr(env.scan_mod.Sources, 'producer_state', lambda *a: {'state':'pending','happened':False})
    tid = F.legacy_probe_wait(env.conn, env.now, reason=reason, waited_s=waited_s)
    fs = env.findings(rule='BS-long-wait', card=tid)
    assert len(fs) == bool(expected)
    if expected:
        assert fs[0]['evidence']['kind'] == expected
        assert fs[0]['evidence']['waited_s'] == waited_s


def test_long_wait_legacy_deploy_wake_and_clock_exclusion(env, monkeypatch):
    from sr_helpers import PLUGIN, load
    from hermes_cli import kanban_db as kb
    mod = load('_sr_legacy_wake', PLUGIN/'nudge.py')
    monkeypatch.setattr(env.scan_mod.Sources, 'producer_state', lambda *a: {'state':'pending','happened':False})
    tid = F.legacy_probe_wait(env.conn, env.now)
    f = one(env, 'BS-long-wait', tid)
    text = mod.message(f, [])
    cid = kb.add_comment(env.conn, tid, 'session-repair', text)
    rec = {'comment_id':cid,'board':'default','card':tid}
    assert mod.wake_owner(env.scan_mod, env.sources(), f, rec, text)['outcome'] == 'woken'
    clock = F.long_wait(env.conn, env.now, kind='clock', waited_s=20000)
    none(env, 'BS-long-wait', clock)
    pure = F.legacy_probe_wait(env.conn, env.now, waited_s=20000, reason='deploy later', wake_check='')
    none(env, 'BS-long-wait', pure)


def test_long_wait_legacy_core_row_is_used_and_age_is_episode_fenced(env, monkeypatch):
    from hermes_cli import kanban_db as kb
    monkeypatch.setattr(env.scan_mod.Sources, 'producer_state', lambda *a: {'state':'pending','happened':False})
    tid = F.legacy_probe_wait(env.conn, env.now, reason='rollout pending')
    task = kb.get_task(env.conn, tid)
    row = {'wait':{'wait_state':'external_running','waiting_on':'deploy rollout PR42',
                   'waiting_since':env.now-20000}}
    card = env.scan_mod.Card(tid,'default',None,task,env.conn,row)
    f = env.scan_mod.rule_long_wait(card,env.sources()).to_dict()
    assert f['evidence']['kind']=='deploy' and f['evidence']['waited_s']==3000


def nudge_target(env, monkeypatch):
    from sr_helpers import PLUGIN, load
    from hermes_cli import kanban_db as kb
    mod = load('_sr_wake_patterns', PLUGIN/'nudge.py')
    monkeypatch.setattr(env.scan_mod.Sources, 'producer_state', lambda *a: {'state':'pending','happened':False})
    tid = F.long_wait(env.conn, env.now)
    f = one(env, 'BS-long-wait', tid)
    text = mod.message(f, []) + '\n[session-repair receipt test]'
    cid = kb.add_comment(env.conn, tid, 'session-repair', text)
    return mod, tid, f, text, {'comment_id':cid,'board':'default','card':tid}


def test_scheduled_nudge_wakes_owner_with_exact_idempotent_receipt(env, monkeypatch):
    from hermes_cli import kanban_db as kb
    mod,tid,f,text,rec = nudge_target(env,monkeypatch)
    result = mod.wake_owner(env.scan_mod, env.sources(), f, rec, text)
    assert result['outcome']=='woken', result
    assert kb.get_task(env.conn,tid).status=='ready'
    event=env.conn.execute('SELECT payload FROM task_events WHERE id=?',(result['event_id'],)).fetchone()
    assert json.loads(event[0])['session_repair_nudge']==rec['comment_id']
    assert mod.wake_owner(env.scan_mod,env.sources(),f,rec,text)==result
    assert len(env.scan_mod._events(env.conn,tid,kinds=('unblocked',),limit=10))==1


@pytest.mark.parametrize('guard', ['human','running','clock','changed_park','new_comment','containment','spend','owner_event'])
def test_nudge_wake_transaction_refuses_changed_or_held_target(env, monkeypatch, guard):
    from hermes_cli import kanban_db as kb, kanban_spend_decision as spend
    mod,tid,f,text,rec = nudge_target(env,monkeypatch)
    if guard=='human':
        monkeypatch.setattr(env.scan_mod,'_human_request',lambda t:({},True))
    elif guard=='running':
        env.conn.execute("UPDATE tasks SET status='running' WHERE id=?",(tid,)); env.conn.commit()
    elif guard in ('clock','changed_park'):
        F.event(env.conn,tid,'scheduled',{'awaiting':{'kind':'clock' if guard=='clock' else 'ci','ref':'other'}},at=env.now)
    elif guard=='new_comment':
        kb.add_comment(env.conn,tid,'owner','New instructions')
    elif guard=='containment':
        monkeypatch.setattr(env.scan_mod,'_promotion_held',lambda *a:'containment')
    elif guard=='spend':
        monkeypatch.setattr(spend,'pending',lambda *a:True)
    elif guard=='owner_event':
        F.event(env.conn,tid,'worker_bound',at=env.now)
    result=mod.wake_owner(env.scan_mod,env.sources(),f,rec,text)
    assert result['outcome']=='wake_refused', result
    assert not env.scan_mod._events(env.conn,tid,kinds=('unblocked',),limit=1)


def test_nudge_transaction_rechecks_health_after_finding(env, monkeypatch):
    mod,tid,f,text,rec = nudge_target(env,monkeypatch)
    env.conn.execute("UPDATE task_events SET payload=json_set(payload, '$.wake_check', 'python3 rollout_ready.py') WHERE id=?",
                     (f['evidence']['park_id'],))
    env.conn.commit()
    F.event(env.conn,tid,'wake_check_alive',{'wake_check':'python3 rollout_ready.py'},at=env.now)
    monkeypatch.setattr(mod.time,'time',lambda:env.now)
    result=mod.wake_owner(env.scan_mod,env.sources(),f,rec,text)
    assert result['outcome']=='wake_refused', result
    assert not env.scan_mod._events(env.conn,tid,kinds=('unblocked',),limit=1)


def test_tick_recovers_committed_wake_without_candidates(env, monkeypatch):
    from sr_helpers import PLUGIN, load
    mod,tid,f,text,rec = nudge_target(env,monkeypatch)
    run = load('_sr_recovery_tick',PLUGIN/'run.py')
    sd = env.home/'session-repair'; sd.mkdir()
    (sd/'config.json').write_text(json.dumps({'nudge_mode':'live'}))
    # The comment and wake committed, but the caller lost the acknowledgement.
    wake = mod.wake_owner(env.scan_mod,env.sources(),f,rec,text)
    assert wake['outcome']=='woken'
    path = sd/'nudges-live.json'
    run._write_json(path, {'fps':{f['fingerprint']:{'times':[], 'pending':{
        'at':env.now-10, 'text':text, 'finding':f}}}, 'sent':[env.now-10]})
    monkeypatch.setattr(run,'load_scan',lambda:env.scan_mod)
    monkeypatch.setattr(run,'load_nudge',lambda:mod)
    result = {'findings':[], 'errors':[]}
    run.tick(home=env.home,scanner=lambda:result,now=env.now,run_mode='off')
    assert run._read_json(path,{})['fps'][f['fingerprint']]['pending']
    out = run.tick(home=env.home,scanner=lambda:result,now=env.now,run_mode='shadow')
    assert out['nudges'][0]['recovered']
    assert out['nudges'][0]['wake']['event_id']==wake['event_id']
    h = run._read_json(path,{})['fps'][f['fingerprint']]
    assert 'pending' not in h and h['times']==[env.now-10]
    assert len(env.scan_mod._events(env.conn,tid,kinds=('unblocked',),limit=10))==1
    assert env.conn.execute('SELECT COUNT(*) FROM task_comments WHERE task_id=?',(tid,)).fetchone()[0]==1


def test_probe_only_history_keeps_two_hour_cadence_and_escalation(env,monkeypatch):
    from sr_helpers import PLUGIN, load
    mod,tid,f,text,rec = nudge_target(env,monkeypatch)
    run = load('_sr_wake_run',PLUGIN/'run.py')
    src=env.sources()
    monkeypatch.setattr(mod,'fresh',lambda *a:(f,src))
    sd=env.home/'session-repair'; sd.mkdir()
    def tick(now):
        return mod.process([f],home=env.home,now=now,scan=env.scan_mod,runner=None,argv=[],
                           read_json=run._read_json,write_json=run._write_json,append=run._append)
    base=env.now
    assert tick(base)[0][0]['nudge_count']==1
    for i,kind in enumerate(('external_watch','wake_check_alive','wake_probe_unproven_notice')):
        F.event(env.conn,tid,kind,at=base+100+i)
    assert not tick(base+18*60)[0]
    assert not tick(base+7140)[0]
    assert tick(base+7260)[0][0]['nudge_count']==2
    assert tick(base+14520)[0][0]['nudge_count']==3
    assert len(tick(base+21780)[1])==1
