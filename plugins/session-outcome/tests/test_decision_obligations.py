"""Core-free source tests: request resolution does not settle the answer outbox."""
import copy
import json
import uuid

import pytest

import test_session_outcome as helpers

home = helpers.home
fake = helpers.fake
plugin = helpers.plugin


@pytest.fixture
def action(plugin, monkeypatch):
    us = plugin._user_steps()
    monkeypatch.setattr(us, 'server_owner', lambda sid: dict(
        gatewayId='example-install', profile='default', storedSessionId=sid))
    monkeypatch.setattr(us, 'lineage', lambda sid, *args: [sid])
    owner = us.server_owner('S1')
    step = us.add_step('S1', 'Select a configuration', ['One', 'Two'])
    store = us.ActionStore()
    request = store.migrate_step(owner, step)
    return plugin, us, owner, store, request, step


def choose(store, owner, request):
    return store.decide(owner, dict(requestId=request['requestId'], expectedRevision=1,
        choiceId=request['choices'][0]['id'], revisionText=request['choices'][0]['label'],
        idempotencyKey=str(uuid.uuid4())))['operation']


def test_open_and_pending_have_distinct_responsibility(action):
    plugin, us, owner, store, request, step = action
    assert plugin.provider('S1')['decision_items'][0]['id'] == step['id']
    op = choose(store, owner, request)
    got = plugin.provider('S1')
    assert got['decision_items'] == []
    assert got['waiting_on'] == [{'kind': 'goal', 'id': 'decision-answer:' + op['operationId'],
        'label': 'Answer pending on agent: Select a configuration', 'state': 'pending'}]


def test_resolved_request_with_undelivered_answer_stays_pending(action):
    plugin, us, owner, store, request, step = action
    op = choose(store, owner, request)
    # Historical producer shape from the incident: request resolved before
    # delivery. Resolution alone cannot certify settlement of the operation.
    with store._tx() as c:
        c.execute("UPDATE requests SET state='resolved' WHERE id=?", (request['requestId'],))
    assert not us.open_steps('S1')
    assert plugin.provider('S1')['waiting_on'][0]['state'] == 'pending'


@pytest.mark.parametrize('terminal', ['delivered', 'cancelled'])
def test_terminal_outbox_disposes_obligation(action, terminal):
    plugin, us, owner, store, request, step = action
    op = choose(store, owner, request)
    with store._tx() as c:
        op['dispatchState'] = terminal
        c.execute('UPDATE operations SET body=? WHERE id=?', (json.dumps(op), op['operationId']))
    assert plugin.provider('S1') is None


def test_foreign_profile_and_session_do_not_hold_this_chat(action):
    plugin, us, owner, store, request, step = action
    choose(store, owner, request)
    assert plugin.provider('T1') is None
    other = dict(owner, profile='other')
    value = copy.deepcopy(request)
    value.update(owner=other, requestId=str(uuid.uuid4()), aliases=[])
    store.create(other, value)
    choose(store, other, value)
    assert plugin.provider('T1') is None


def test_click_during_capture_cannot_drop_both_ask_and_answer(action, monkeypatch):
    plugin, us, owner, store, request, step = action
    get = us.ActionStore._get
    clicked = []
    def click_after_ask_read(self, conn, own, rid):
        value = get(self, conn, own, rid)
        if self.read_only and not clicked:
            clicked.append(True)
            choose(store, owner, request)
        return value
    monkeypatch.setattr(us.ActionStore, '_get', click_after_ask_read)
    # WAL writer commits after the read snapshot has seen an open request.
    # This pass retains that ask; the next observes the pending answer instead.
    got = plugin.provider('S1')
    assert got['decision_items'] or got['waiting_on']
    assert plugin.provider('S1')['waiting_on'][0]['state'] == 'pending'
