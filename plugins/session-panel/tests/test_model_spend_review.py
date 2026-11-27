"""Spend stops under core's immediate reviewer (``kanban_spend_reviewer``): the panel says who is deciding, and an
escalated stop nobody decided for 30 min is red, not another 'waiting on this chat's agent'.

User override (overdue triage): any triage episode older than 600 s also paints red as operational attention
(``attention == 'triage_overdue'``) without becoming a human ask. Fixtures that enter triage an hour ago therefore
assert ``_agent_owned`` (not ``needs_you``, not the reviewer's 30-min ``stalled``) instead of the superseded
"not stuck / waiting" placement; ``entered=`` <= 600 keeps the original placement where it is not age-escalated.
Ownership, reviewer and stop-cause text assertions are unchanged.
"""
import pytest

from test_model import NOW, held_bundle, model

try:
    import hermes_cli.kanban_triage_attention  # noqa: F401  shared >600 s policy lives in core
    AGED = True
except ImportError:
    AGED = False


def _with(*events, entered=3600):
    b = held_bundle(kind='spend_budget_triaged')
    b['cards']['t_abc123']['detail']['events'] = [
        {'kind': 'spend_budget_triaged', 'created_at': NOW - entered, 'payload': {'limit': 'runs'}}, *events]
    return b


def _row(m):
    return next(v for s in ('needs_you', 'stuck', 'waiting') for v in m['sections'][s])


def _agent_owned(m):
    """Not a human ask and not the reviewer's escalation; red only through overdue triage (when core ages it)."""
    row = _row(m)
    assert not m['sections']['needs_you'] and row.get('reason') != 'stalled'
    if AGED:
        assert row['attention'] == 'triage_overdue' and row in m['sections']['stuck']
        assert row['detail'].startswith('overdue triage · ')
    else:
        assert row in m['sections']['waiting']
    return row


def test_a_stop_under_review_says_the_reviewer_is_deciding():
    for kind in ('spend_review_queued', 'spend_review_started'):
        # Fresh triage entry (not age-escalated): original placement.
        m = model.reduce_panel(_with({'kind': kind, 'created_at': NOW - 30, 'payload': {'episode': 'e'}},
                                     entered=300), now=NOW)
        row = m['sections']['waiting'][0]
        assert 'the spend reviewer is deciding now' in row['detail']
        assert "this chat's agent" not in row['detail'] and not m['sections']['stuck']
        # An hour in triage: red operational attention, same reviewer ownership.
        row = _agent_owned(model.reduce_panel(_with({'kind': kind, 'created_at': NOW - 30, 'payload': {'episode': 'e'}}),
                                              now=NOW))
        assert 'the spend reviewer is deciding now' in row['detail'] and "this chat's agent" not in row['detail']


@pytest.mark.parametrize('entered', [600, 3600])
def test_a_fresh_escalation_waits_on_the_agent_with_the_reviewers_reason(entered):
    m = model.reduce_panel(_with({'kind': 'spend_review_escalated', 'created_at': NOW - 600,
                                  'payload': {'episode': 'e', 'reason': 'Runs repeat the same wait.'}},
                                 entered=entered), now=NOW)
    if entered <= 600:  # exactly 600 s is not age-escalated
        row = m['sections']['waiting'][0]
        assert not m['sections']['stuck']
    else:
        row = _agent_owned(m)
    assert 'reviewer escalated: Runs repeat the same wait.' in row['detail']
    assert "waiting on this chat's agent to decide" in row['detail']


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
    # The escalation ended; the hour-old triage episode is still overdue (a repeated hold does not reset it).
    assert 'reviewer' not in _agent_owned(m)['detail']


def _escalated(age, reason='Runs repeat the same wait.', **row):
    b = _with({'kind': 'spend_review_escalated', 'created_at': NOW - age, 'payload': {'episode': 'e', 'reason': reason}})
    b['tasks'][0].update(row)
    return b


def test_the_30_minute_boundary_and_millisecond_clocks():
    _agent_owned(model.reduce_panel(_escalated(1799), now=NOW))
    assert model.reduce_panel(_escalated(1800), now=NOW)['sections']['stuck'][0]['reason'] == 'stalled'
    b = _escalated(0)
    b['cards']['t_abc123']['detail']['events'][-1]['created_at'] = (NOW - 1900) * 1000
    assert model.reduce_panel(b, now=NOW)['sections']['stuck'][0]['reason'] == 'stalled'


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
    assert 'Wait for the hub deploy.' in _agent_owned(model.reduce_panel(b, now=NOW))['detail']


def test_unreadable_evidence_never_turns_red():
    for flag in ('partial_source_unknown', 'stale'):
        assert not model.reduce_panel(_escalated(1900, **{flag: True}), now=NOW)['sections']['stuck']


def test_a_reviewer_turned_off_mid_review_hands_the_stop_back_to_the_agent():
    b = _with({'kind': 'spend_review_queued', 'created_at': NOW - 7200, 'payload': {'episode': 'e'}},
              {'kind': 'spend_budget_decision_ready', 'created_at': NOW - 60, 'payload': {'episode': 'e'}})
    row = _row(model.reduce_panel(b, now=NOW))
    assert 'deciding now' not in row['detail'] and "waiting on this chat's agent to decide" in row['detail']


def test_an_escalation_keeps_the_stop_cause():
    row = _agent_owned(model.reduce_panel(_escalated(600), now=NOW))
    assert row['detail'].removeprefix('overdue triage · ').startswith('run cap used up · reviewer escalated')


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


def _shared_parent(age=1900, extra_events=(), **parent_kw):
    from test_model import task
    parent = task('t_abcdef', status='triage', title='Shared stage', **parent_kw)
    events = [{'kind': 'spend_budget_triaged', 'created_at': NOW - 3600, 'payload': {'limit': 'runs'}},
              {'kind': 'spend_review_escalated', 'created_at': NOW - age, 'payload': {'reason': 'Same wait.'}},
              *extra_events]
    return {'computed_at': NOW, 'tasks': [task('t_111111', status='todo', upstream=parent),
                                          task('t_222222', status='todo', upstream=parent)],
            'done': [], 'cards': {parent['id']: {'detail': {'task': parent, 'events': events}}}}


def _all_rows(m):
    rows = [v for s in ('needs_you', 'stuck', 'waiting', 'ready') for v in m['sections'][s]]
    return rows + list(m.get('groups') or [])


def test_a_shared_escalated_parent_turns_its_group_red():
    m = model.reduce_panel(_shared_parent(), now=NOW)
    groups = [g for g in m.get('groups') or [] if g.get('section') == 'stuck']
    assert groups and 'the reviewer escalated' in groups[0]['detail']
    assert not model.reduce_panel(_shared_parent(age=600), now=NOW)['sections']['stuck']


def test_an_unreadable_parent_never_turns_its_children_or_group_red():
    m = model.reduce_panel(_shared_parent(partial_source_unknown=True), now=NOW)
    assert not m['sections']['stuck']
    assert not [g for g in m.get('groups') or [] if g.get('section') == 'stuck']


def test_core_rerouting_after_an_escalation_keeps_the_escalation():
    b = _escalated(1900)
    b['cards']['t_abc123']['detail']['events'].append(
        {'kind': 'spend_budget_decision_ready', 'created_at': NOW - 1890, 'payload': {'episode': 'e'}})
    assert model.reduce_panel(b, now=NOW)['sections']['stuck']
