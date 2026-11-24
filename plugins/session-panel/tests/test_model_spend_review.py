"""Spend stops under core's immediate reviewer (``kanban_spend_reviewer``): the panel says who is deciding, and an
escalated stop nobody decided for 30 min is red, not another 'waiting on this chat's agent'."""
from test_model import NOW, held_bundle, model


def _with(*events):
    b = held_bundle(kind='spend_budget_triaged')
    b['cards']['t_abc123']['detail']['events'] = [
        {'kind': 'spend_budget_triaged', 'created_at': NOW - 3600, 'payload': {'limit': 'runs'}}, *events]
    return b


def _row(m):
    return next(v for s in ('needs_you', 'stuck', 'waiting') for v in m['sections'][s])


def test_a_stop_under_review_says_the_reviewer_is_deciding():
    for kind in ('spend_review_queued', 'spend_review_started'):
        m = model.reduce_panel(_with({'kind': kind, 'created_at': NOW - 30, 'payload': {'episode': 'e'}}), now=NOW)
        row = m['sections']['waiting'][0]
        assert 'the spend reviewer is deciding now' in row['detail']
        assert "this chat's agent" not in row['detail'] and not m['sections']['stuck']


def test_a_fresh_escalation_waits_on_the_agent_with_the_reviewers_reason():
    m = model.reduce_panel(_with({'kind': 'spend_review_escalated', 'created_at': NOW - 600,
                                  'payload': {'episode': 'e', 'reason': 'Runs repeat the same wait.'}}), now=NOW)
    row = m['sections']['waiting'][0]
    assert 'reviewer escalated: Runs repeat the same wait.' in row['detail']
    assert "waiting on this chat's agent to decide" in row['detail'] and not m['sections']['stuck']


def test_an_escalation_nobody_decided_for_30_minutes_is_stuck_not_waiting():
    m = model.reduce_panel(_with({'kind': 'spend_review_escalated', 'created_at': NOW - 1900,
                                  'payload': {'episode': 'e', 'reason': 'Runs repeat the same wait.'}}), now=NOW)
    assert not m['sections']['waiting']
    row = m['sections']['stuck'][0]
    assert row['reason'] == 'stalled'
    assert 'out of budget · the reviewer escalated' in row['detail'] and 'the owner has not decided yet' in row['detail']
    assert m['header']['state'] == 'stuck'


def test_a_new_stop_after_a_grant_starts_over():
    b = _with({'kind': 'spend_review_escalated', 'created_at': NOW - 1900, 'payload': {'reason': 'old'}},
              {'kind': 'spend_budget_triaged', 'created_at': NOW - 60, 'payload': {'limit': 'runs'}})
    m = model.reduce_panel(b, now=NOW)
    assert not m['sections']['stuck'] and 'reviewer' not in _row(m)['detail']


def _escalated(age, reason='Runs repeat the same wait.', **row):
    b = _with({'kind': 'spend_review_escalated', 'created_at': NOW - age, 'payload': {'episode': 'e', 'reason': reason}})
    b['tasks'][0].update(row)
    return b


def test_the_30_minute_boundary_and_millisecond_clocks():
    assert model.reduce_panel(_escalated(1799), now=NOW)['sections']['waiting']
    assert model.reduce_panel(_escalated(1800), now=NOW)['sections']['stuck']
    b = _escalated(0)
    b['cards']['t_abc123']['detail']['events'][-1]['created_at'] = (NOW - 1900) * 1000
    assert model.reduce_panel(b, now=NOW)['sections']['stuck']


def test_the_reason_is_clipped_and_never_carries_a_url_or_hash():
    long = 'See https://github.com/x/y/pull/9 at 0123456789abcdef0123 ' + 'and more words ' * 40
    for age in (600, 1900):
        row = _row(model.reduce_panel(_escalated(age, long), now=NOW))
        assert 'https://' not in row['detail'] and '0123456789abcdef0123' not in row['detail']
        assert len(row['detail']) < 400


def test_a_later_owner_decision_ends_the_escalation():
    b = _escalated(1900)
    b['cards']['t_abc123']['detail']['events'].append(
        {'kind': 'decision_retained', 'created_at': NOW - 60,
         'payload': {'disposition': {'reason': 'decision_pending', 'next_step': 'Wait for the hub deploy.'}}})
    m = model.reduce_panel(b, now=NOW)
    assert not m['sections']['stuck'] and 'Wait for the hub deploy.' in _row(m)['detail']


def test_unreadable_evidence_never_turns_red():
    for flag in ('partial_source_unknown', 'stale'):
        assert not model.reduce_panel(_escalated(1900, **{flag: True}), now=NOW)['sections']['stuck']


def test_a_reviewer_turned_off_mid_review_hands_the_stop_back_to_the_agent():
    b = _with({'kind': 'spend_review_queued', 'created_at': NOW - 7200, 'payload': {'episode': 'e'}},
              {'kind': 'spend_budget_decision_ready', 'created_at': NOW - 60, 'payload': {'episode': 'e'}})
    row = _row(model.reduce_panel(b, now=NOW))
    assert 'deciding now' not in row['detail'] and "waiting on this chat's agent to decide" in row['detail']


def test_an_escalation_keeps_the_stop_cause():
    row = _row(model.reduce_panel(_escalated(600), now=NOW))
    assert row['detail'].startswith('run cap used up · reviewer escalated')


def test_remote_cards_and_wire_clocks_are_stable_across_ticks():
    for remote in (False, True):
        b = _escalated(1900, remote=remote, source='replica' if remote else 'live')
        first = model.reduce_panel(b, now=NOW, wire=True)
        assert first['sections']['stuck']
        b['computed_at'] = NOW + 300
        assert model.reduce_panel(b, now=NOW + 300, wire=True)['revision'] == first['revision']


def test_children_of_an_escalated_parent_agree_with_it():
    from test_model import task
    parent = task('t_abcdef', status='triage', title='Shared stage')
    events = [{'kind': 'spend_budget_triaged', 'created_at': NOW - 3600, 'payload': {'limit': 'runs'}},
              {'kind': 'spend_review_escalated', 'created_at': NOW - 1900, 'payload': {'reason': 'Same wait.'}}]
    b = {'computed_at': NOW, 'tasks': [task(status='todo', upstream=parent)], 'done': [],
         'cards': {parent['id']: {'detail': {'task': parent, 'events': events}}}}
    row = model.reduce_panel(b, now=NOW)['sections']['stuck'][0]
    assert 'the reviewer escalated' in row['detail']
