"""Id-keyed triage hydration must respect the listed owner before presentation defaults."""
import copy

import pytest

from test_model import NOW, bundle, model, task


@pytest.mark.parametrize('field', ['gateway', 'profile', 'board'])
@pytest.mark.parametrize('age', [599, 601])
def test_foreign_triage_detail_never_supplies_clock_or_attention(field, age):
    pytest.importorskip('hermes_cli.kanban_triage_attention')
    listed = task(status='triage', block_kind='decision_pending')
    hydrated = {**listed, field: 'foreign-owner'}
    events = [{'kind': 'decision_retained', 'created_at': NOW - age,
               'payload': {'disposition': {'reason': 'scope_stopped'}}}]
    b = bundle(listed, cards={listed['id']: {'detail': {'task': hydrated, 'events': events}}})
    before = copy.deepcopy(b)
    result = model.reduce_panel(b, now=NOW)
    assert not result['sections']['stuck']
    row = result['sections']['waiting'][0]
    assert 'entry time unavailable' in row['detail']
    assert 'scope stopped' not in row['detail']
    assert model.triage_clocks({**b, 'model': result}) == []
    # The off-list group memo must not merge a foreign task over group ownership.
    group = {**listed, 'key': model.key(listed), 'section': 'waiting'}
    assert model.triage_clocks({**b, 'tasks': [], 'model': {'groups': [group]}}) == []
    assert b == before

    # The same episode at the actual owner retains its clock and strict >600 s attention.
    b['cards'][listed['id']]['detail']['task'] = dict(listed)
    owned = model.reduce_panel(b, now=NOW)
    shown = owned['sections']['stuck' if age > 600 else 'waiting'][0]
    assert (shown.get('attention') == 'triage_overdue') is (age > 600)
    assert 'scope stopped' in shown['detail']
    assert bool(model.triage_clocks({**b, 'model': owned})) is (age <= 600)


@pytest.mark.parametrize('field', ['gateway', 'profile', 'board'])
def test_foreign_hold_history_is_not_an_agent_hold(field):
    listed = task(status='blocked')
    b = bundle(listed, cards={listed['id']: {'detail': {
        'task': {**listed, field: 'foreign-owner'},
        'events': [{'kind': 'decision_retained', 'payload': {'disposition': {'reason': 'scope_stopped'}}}],
    }}})
    assert not model.agent_hold(listed, b)
    assert model.held_cause(listed, b) == ''
    b['cards'][listed['id']]['detail']['task'] = dict(listed)
    assert model.agent_hold(listed, b)
    assert model.held_cause(listed, b) == 'scope stopped'


@pytest.mark.parametrize('field', ['gateway', 'profile', 'board'])
@pytest.mark.parametrize('clock_source', ['events', 'triage_entered_at'])
@pytest.mark.parametrize('age', [599, 601])
def test_group_reducer_rejects_foreign_triage_history(field, clock_source, age):
    pytest.importorskip('hermes_cli.kanban_triage_attention')
    parent = task('t_parent', status='triage')
    children = [task(tid, status='todo', upstream=parent) for tid in ('t_child1', 't_child2')]
    detail = {'task': {**parent, field: 'foreign-owner'}, 'events': []}
    if clock_source == 'events':
        detail['events'] = [{'kind': 'decision_retained', 'created_at': NOW - age,
                             'payload': {'disposition': {'reason': 'scope_stopped'}}}]
    else:
        detail['task']['triage_entered_at'] = NOW - age
    b = bundle(*children, cards={parent['id']: {'detail': detail}})
    result = model.reduce_panel(b, now=NOW)
    group = result['groups'][0]
    assert group[field] == parent[field]
    assert group.get('attention') != 'triage_overdue'
    assert 'entry time unavailable' in group['detail']
    assert 'scope stopped' not in group['detail']
    assert model.triage_clocks({**b, 'model': result}) == []
    detail['task'][field] = parent[field]
    owned = model.reduce_panel(b, now=NOW)
    assert (owned['groups'][0].get('attention') == 'triage_overdue') is (age > 600)
    assert bool(model.triage_clocks({**b, 'model': owned})) is (age <= 600)


@pytest.mark.parametrize('field', ['gateway', 'profile', 'board'])
def test_foreign_explicit_triage_timestamp_is_not_hydrated(field):
    pytest.importorskip('hermes_cli.kanban_triage_attention')
    listed = task(status='triage')
    detail = {'task': {**listed, field: 'foreign-owner', 'triage_entered_at': NOW - 601}, 'events': []}
    b = bundle(listed, cards={listed['id']: {'detail': detail}})
    result = model.reduce_panel(b, now=NOW)
    assert not result['sections']['stuck']
    assert 'entry time unavailable' in result['sections']['waiting'][0]['detail']
    assert model.triage_clocks({**b, 'model': result}) == []


@pytest.mark.parametrize('status', ['blocked', 'triage'])
def test_accepted_legacy_history_survives_serving_defaults(status):
    listed = task(status=status)
    listed.pop('gateway')
    b = bundle(listed, gateway='serving-owner', cards={listed['id']: {'detail': {
        'task': {**listed, 'gateway': 'detail-owner'},
        'events': [{'kind': 'decision_retained', 'created_at': NOW - 599,
                    'payload': {'disposition': {'reason': 'scope_stopped', 'summary': 'review the stopped scope'}}}],
    }}})
    result = model.reduce_panel(b, now=NOW)
    row = result['sections']['waiting'][0]
    assert row['gateway'] == 'serving-owner'
    assert row['wait_kind'] == 'agent'
    assert 'scope stopped' in row['detail']
    assert "waiting on this chat's agent" in row['detail']
    assert 'review the stopped scope' in row['detail']


def test_hydration_uses_listed_coordinates_before_serving_defaults():
    # Legacy listed rows omit gateway; the serving identity is only a display default.
    # This detail is compatible with the listed row, but not with the inherited gateway.
    listed = task(status='ready')
    listed.pop('gateway')
    b = bundle(listed, gateway='serving-owner', cards={listed['id']: {'detail': {
        'task': {**listed, 'gateway': 'detail-owner', 'title': 'Detail title'},
    }}})
    result = model.reduce_panel(b, now=NOW)
    row = result['sections']['ready'][0]
    assert row['gateway'] == 'serving-owner'
    assert row['title'] == listed['title']  # list remains authoritative
    assert model.owned_card(listed, b)
    assert not model.owned_card(model.serving_row(listed, b), b)
    # Supplementary session identity yields a real reducer action only if detail was accepted.
    b['cards'][listed['id']]['detail']['task']['session_id'] = 'detail-chat'
    result = model.reduce_panel(b, now=NOW)
    action = next(a for a in result['sections']['ready'][0]['actions'] if a['kind'] == 'open_session')
    assert action['target'] == {'session': 'detail-chat', 'gateway': 'serving-owner', 'profile': 'default'}
