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
    assert f['variant'] == 'free_slot' and f['fix']['kind'] == 'auto'
    assert f['evidence']['running'] == 0 and f['evidence']['limit'] == 4
    assert any('dispatch' in c for c in f['fix']['commands'])


def test_ready_card_not_flagged_when_recent(env):
    none(env, 'BS-ready-worker-free', F.ready_worker_free(env.conn, env.now, waited_s=120))


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


def test_scheduled_with_no_wake_published(env):
    f = one(env, 'BS-overdue-wake', F.scheduled_no_wake(env.conn, env.now))
    assert f['variant'] == 'no_wake' and f['fix']['kind'] == 'card'


def test_scheduled_with_no_wake_just_parked_is_fine(env):
    none(env, 'BS-overdue-wake', F.scheduled_no_wake(env.conn, env.now, parked_s=300))


def test_wake_probe_past_its_next_run(env):
    f = one(env, 'BS-overdue-wake', F.overdue_probe(env.conn, env.now))
    assert f['variant'] == 'probe_overdue' and f['evidence']['probe_cmd'] == 'true'


def test_wake_probe_on_time_is_fine(env):
    none(env, 'BS-overdue-wake', F.overdue_probe(env.conn, env.now, late_s=-300))


# BS-unexplained-wait
def test_blocked_with_no_reason(env):
    f = one(env, 'BS-unexplained-wait', F.blocked_no_reason(env.conn, env.now))
    assert f['variant'] == 'no_reason'


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


# BS-parent-done
def test_todo_whose_parents_finished(env):
    f = one(env, 'BS-parent-done', F.todo_parents_done(env.conn, env.now))
    assert f['variant'] == 'parents_done' and 'promote' in f['fix']['commands'][0]


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


def test_running_whose_claim_expired(env):
    assert one(env, 'BS-stuck-run', F.running_claim_expired(env.conn, env.now))['variant'] == 'claim_expired'


def test_running_and_heartbeating_is_fine(env):
    none(env, 'BS-stuck-run', F.running_healthy(env.conn, env.now))


# BS-review-no-reviewer
def test_review_with_no_review_run(env):
    f = one(env, 'BS-review-no-reviewer', F.review_idle(env.conn, env.now))
    assert f['evidence']['last_review_event'] == 'review_requested'


def test_review_with_a_review_running_is_fine(env):
    none(env, 'BS-review-no-reviewer', F.review_running(env.conn, env.now))


def test_review_just_requested_is_fine(env):
    none(env, 'BS-review-no-reviewer', F.review_idle(env.conn, env.now, idle_s=600))


# BS-broken-chain
def test_waiting_on_a_card_no_board_has(env):
    f = one(env, 'BS-broken-chain', F.missing_parent(env.conn, env.now))
    assert f['variant'] == 'missing_parent' and f['evidence']['missing'] == ['t_00000000deadbeef']


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
    assert hits[0]['fingerprint'] == 'BS-notice-undelivered:chat/C1' and 'C1' in hits[0]['fix']['commands'][0]


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
