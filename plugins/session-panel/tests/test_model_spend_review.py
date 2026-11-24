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
    assert 'out of budget · the reviewer escalated' in row['detail'] and 'nobody has decided' in row['detail']
    assert m['header']['state'] == 'stuck'


def test_a_new_stop_after_a_grant_starts_over():
    b = _with({'kind': 'spend_review_escalated', 'created_at': NOW - 1900, 'payload': {'reason': 'old'}},
              {'kind': 'spend_budget_triaged', 'created_at': NOW - 60, 'payload': {'limit': 'runs'}})
    m = model.reduce_panel(b, now=NOW)
    assert not m['sections']['stuck'] and 'reviewer' not in _row(m)['detail']
