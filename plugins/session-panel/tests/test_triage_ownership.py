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


@pytest.mark.parametrize('field', ['gateway', 'profile', 'board'])
@pytest.mark.parametrize('helper', ['triage_clock', 'agent_hold', 'held_cause', 'wait_reason',
                                    'spend_review', 'wait_target', 'wait_parents', 'wait_parents_next', 'agent_hold_text'])
def test_every_history_reader_rejects_foreign_same_id(field, helper):
    parent = task('t_parent', status='blocked')
    events = [
        {'kind': 'decision_retained', 'created_at': NOW - 601,
         'payload': {'disposition': {'reason': 'scope_stopped', 'summary': 'owner-only reason'}}},
        {'kind': 'spend_review_escalated', 'created_at': NOW - 1801,
         'payload': {'reason': 'owner-only review'}},
    ]
    detail = {'task': {**parent, field: 'foreign-owner', 'title': 'owner-only title',
                       'triage_entered_at': NOW - 601}, 'events': events,
              'links': {'parents': ['t_ancestor']},
              'link_tasks': [task('t_ancestor', title='owner-only ancestor')]}
    b = bundle(cards={parent['id']: {'detail': detail}})
    child = task(status='todo', upstream=parent)
    hold = task(status='triage', wait={'contained': {'authority': parent}})
    def read():
        if helper == 'wait_target':
            return model.wait_target(child, b, {})
        if helper == 'wait_parents':
            return model.wait_parents(parent, b)
        if helper == 'wait_parents_next':
            b['cards'][child['id']] = {'detail': {'task': child, 'links': {'parents': [parent['id']]},
                                                 'link_tasks': [parent]}}
            return model.wait_parents(child, b)
        if helper == 'agent_hold_text':
            return model.agent_hold_text(hold, b)
        return getattr(model, helper)(parent, b)
    foreign = read()
    if helper == 'triage_clock':
        assert not foreign.get('events')
    elif helper == 'wait_target':
        assert foreign == parent  # listed ref survives; foreign supplementary episode does not
    elif helper == 'wait_parents_next':
        assert foreign == [parent]
    elif helper == 'agent_hold_text':
        assert 'owner-only' not in foreign
    else:
        assert not foreign
    detail['task'][field] = parent[field]
    owned = read()
    assert owned
    if helper == 'wait_target':
        assert owned['triage_entered_at'] == NOW - 601
    if helper == 'wait_parents_next':
        assert owned[0]['triage_entered_at'] == NOW - 601
    if helper == 'agent_hold_text':
        assert 'owner-only title' in owned


@pytest.mark.parametrize('field', ['gateway', 'profile', 'board'])
@pytest.mark.parametrize('grouped', [False, True])
def test_foreign_spend_review_cannot_escalate_rows_or_groups(field, grouped):
    parent = task('t_parent', status='triage')
    rows = [task(tid, status='todo', upstream=parent) for tid in ('t_child1', 't_child2')] if grouped else [parent]
    detail = {'task': {**parent, field: 'foreign-owner'}, 'events': [
        {'kind': 'spend_review_escalated', 'created_at': NOW - 1801,
         'payload': {'reason': 'owner-only review'}}]}
    b = bundle(*rows, cards={parent['id']: {'detail': detail}})
    foreign = model.reduce_panel(b, now=NOW)
    assert not foreign['sections']['stuck']
    assert 'owner-only review' not in str(foreign)
    detail['task'][field] = parent[field]
    owned = model.reduce_panel(b, now=NOW)
    assert 'owner-only review' in str(owned)
    assert owned['groups'][0]['section'] == 'stuck' if grouped else owned['sections']['stuck']


def test_group_listed_status_wins_over_hydrated_status():
    parent = task('t_parent', status='triage')
    children = [task(tid, status='todo', upstream=parent) for tid in ('t_child1', 't_child2')]
    b = bundle(*children, cards={parent['id']: {'detail': {'task': {**parent, 'status': 'running'}}}})
    assert model.reduce_panel(b, now=NOW)['groups'][0]['status'] == 'triage'


def test_explicit_triage_timestamp_without_events_is_supported():
    pytest.importorskip('hermes_cli.kanban_triage_attention')
    parent = task(status='triage')
    b = bundle(parent, cards={parent['id']: {'detail': {
        'task': {**parent, 'triage_entered_at': NOW - 601}}}})
    assert model.reduce_panel(b, now=NOW)['sections']['stuck'][0]['attention'] == 'triage_overdue'


@pytest.mark.parametrize('field', ['gateway', 'profile', 'board'])
def test_rejected_detail_cannot_supply_reducer_parent_links(field):
    listed = task(status='todo')
    parent = task('t_parent', title='Foreign-only parent')
    b = bundle(listed, cards={listed['id']: {'detail': {
        'task': {**listed, field: 'foreign-owner'},
        'links': {'parents': [parent['id']]}, 'link_tasks': [parent]}}})
    result = model.reduce_panel(b, now=NOW)
    assert result['sections']['ready'][0]['id'] == listed['id']
    assert not result['groups']
    assert 'Foreign-only parent' not in str(result)
    assert '_accepted_detail' not in str(result)


@pytest.mark.parametrize('helper', ['wait_target', 'wait_parents'])
def test_slim_parent_navigation_preserves_record_profile(helper):
    parent = task('t_parent', title='Slim parent', status='blocked')
    parent.pop('gateway')
    parent.pop('profile')
    child = task(status='todo', upstream={'id': parent['id'], 'status': 'blocked'})
    detail = {'task': child, 'links': {'parents': [parent['id']]}, 'link_tasks': [parent]}
    b = bundle(cards={parent['id']: {'profile': 'parent-profile', 'board': 'default',
        'detail': {'task': parent, 'source': 'replica', 'owner': 'parent-owner'}},
        child['id']: {'detail': detail}}, gateway='serving-owner', profile='default')
    dep = model.wait_target(child, b, detail) if helper == 'wait_target' else model.wait_parents(child, b)[0]
    assert dep['profile'] == 'parent-profile'
    assert dep['gateway'] == 'parent-owner'


@pytest.mark.parametrize('detail_field', ['gateway', 'home_gateway', 'profile'])
@pytest.mark.parametrize('grouped', [False, True])
def test_accepted_legacy_detail_keeps_unfinished_parent(detail_field, grouped):
    parent = task('t_parent', title='Parent card', status='blocked', gateway='hub')
    rows = [task(tid, status='todo') for tid in ('t_child1', 't_child2')[:2 if grouped else 1]]
    cards = {parent['id']: {'detail': {'task': parent, 'events': [
        {'kind': 'blocked', 'payload': {'reason': 'parent own reason'}}]}}}
    for row in rows:
        row.pop('profile' if detail_field == 'profile' else 'gateway')
        cards[row['id']] = {'detail': {
            'task': {**row, detail_field: 'detail-owner'},
            'links': {'parents': [parent['id']]}, 'link_tasks': [parent]}}
    b = bundle(*rows, gateway='serving-owner', profile='default', cards=cards)
    before = copy.deepcopy(b)
    result = model.reduce_panel(b, now=NOW)
    assert not result['sections']['ready']
    shown = result['sections']['waiting']
    assert len(shown) == len(rows)
    for row in shown:
        assert 'waiting on: Parent card (blocked) on hub' in row['detail']
        assert 'parent own reason' in row['detail']
        assert any(a.get('target') == {'id': parent['id'], 'gateway': 'hub',
                   'profile': 'default', 'board': 'default'} for a in row['actions'])
    assert len(result['groups']) == int(grouped)
    if grouped:
        assert all(r['parent'] == result['groups'][0]['key'] for r in shown)
    assert b == before


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
