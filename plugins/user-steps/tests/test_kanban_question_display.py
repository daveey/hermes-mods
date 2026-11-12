"""Display projection must not mutate a frozen card request or its authority."""
import json
import sqlite3
import uuid

import pytest
from test_kanban_mirror import fixture, decide, OWNER, core


def test_display_keeps_frozen_identity_and_recommendation_ids(tmp_path):
    mirror, store, path, hr = fixture(tmp_path)
    hr['action'] = 'Pick a color for t_deadbeef. Unblock = continue; archive = abandon.'
    hr['choices'][1]['recommended'] = True
    with sqlite3.connect(path) as c:
        c.execute('UPDATE tasks SET human_request=?', (json.dumps(hr),))
    mirror.reconcile(OWNER)
    old = store.requests(OWNER)[0]
    binding = mirror.binding(OWNER, old['requestId'])
    display = mirror.present(store.view(OWNER, old['requestId']))['presentation']
    assert display['question'] == 'Pick a color for this card.'
    assert display['recommendedChoiceId'] == 'card-1'
    assert all(hr[k] in display['details'] for k in ('why_human', 'scope', 'evidence'))
    assert display['other'] is True
    mirror.reconcile(OWNER)
    assert store.requests(OWNER) == [old]
    assert mirror.binding(OWNER, old['requestId']) == binding
    identity = json.dumps([OWNER, 'default', json.loads(binding['snapshot'])], sort_keys=True, separators=(',', ':'), ensure_ascii=False)
    assert old['requestId'] == str(uuid.uuid5(uuid.NAMESPACE_URL, 'hermes:kanban-ask:' + identity))
    assert mirror.deliver(decide(store, old))['value'] == 'green'


def test_other_is_bound_informational_and_idempotent(tmp_path):
    mirror, store, path, _ = fixture(tmp_path)
    mirror.reconcile(OWNER)
    old = store.requests(OWNER)[0]
    payload = dict(requestId=old['requestId'], expectedRevision=1, choiceId='answer-text', revisionText='Use purple instead', idempotencyKey='other-one')
    with pytest.raises(core.ActionError, match='Unknown choice'):
        store.decide(OWNER, payload)
    result = mirror.decide_other(OWNER, payload)
    op = result['operation']
    assert op['intent']['request'] == old
    assert op['intent']['executionAuthorized'] is False
    assert op['intent']['permissionGrants'] == []
    assert mirror.decide_other(OWNER, payload)['operation']['operationId'] == op['operationId']
    receipt = mirror.deliver(op)
    assert receipt['value'] == 'Use purple instead'
    assert mirror.deliver(op) == receipt
    with sqlite3.connect(path) as c:
        assert c.execute('SELECT status FROM tasks').fetchone()[0] == 'ready'
        comments = c.execute('SELECT body FROM task_comments').fetchall()
    assert len(comments) == 1 and 'Use purple instead' in comments[0][0]


def test_other_rejects_changed_binding_and_stale_source(tmp_path):
    mirror, store, path, hr = fixture(tmp_path)
    mirror.reconcile(OWNER)
    old = store.requests(OWNER)[0]
    payload = dict(requestId=old['requestId'], expectedRevision=1, choiceId='answer-text', revisionText='archive', idempotencyKey='other-stale')
    op = mirror.decide_other(OWNER, payload)['operation']
    hr['action'] = 'Different question'
    with sqlite3.connect(path) as c:
        c.execute('UPDATE tasks SET human_request=?', (json.dumps(hr),))
    with pytest.raises(core.ActionError, match='changed|refused'):
        mirror.deliver(op)
    with sqlite3.connect(path) as c:
        assert c.execute('SELECT status FROM tasks').fetchone()[0] == 'blocked'
        assert c.execute('SELECT COUNT(*) FROM task_comments').fetchone()[0] == 0


def test_display_label_recommendation_and_fallback(tmp_path):
    mirror, store, path, hr = fixture(tmp_path)
    hr['action'] = 'Unblock = continue; archive = abandon.'
    hr['choices'][0]['label'] += ' (recommended)'
    with sqlite3.connect(path) as c:
        c.execute('UPDATE tasks SET human_request=?', (json.dumps(hr),))
    mirror.reconcile(OWNER)
    r = store.requests(OWNER)[0]
    view = mirror.present(r)['presentation']
    assert view['question'] == 'Choose a color'
    assert view['recommendedChoiceId'] == 'card-0'


@pytest.mark.parametrize('marked,expected', [((1,), 'card-1'), ((2,), 'card-2'), ((0, 2), None), ((), None)])
def test_display_label_recommendation_on_any_single_choice(tmp_path, marked, expected):
    mirror, store, path, hr = fixture(tmp_path)
    for i in marked:
        hr['choices'][i]['label'] += ' (Recommended)'
    with sqlite3.connect(path) as c:
        c.execute('UPDATE tasks SET human_request=?', (json.dumps(hr),))
    mirror.reconcile(OWNER)
    view = mirror.present(store.requests(OWNER)[0])['presentation']
    assert view['recommendedChoiceId'] == expected


@pytest.mark.parametrize('case', ['owner', 'revision', 'blank', 'unbound', 'revised'])
def test_other_rejects_invalid_authority(tmp_path, case):
    mirror, store, path, _ = fixture(tmp_path)
    mirror.reconcile(OWNER)
    r = store.requests(OWNER)[0]
    owner = OWNER
    if case == 'unbound':
        r = dict(r, requestId=str(uuid.uuid4()))
        store.create(OWNER, r)
    if case == 'revised':
        store.revise(OWNER, r['requestId'], 1, dict(r, title='Changed'))
    payload = dict(requestId=r['requestId'], expectedRevision=2 if case in ('revision', 'revised') else 1,
                   choiceId='answer-text', revisionText='' if case == 'blank' else 'Different answer', idempotencyKey='invalid')
    if case == 'owner':
        owner = dict(OWNER, storedSessionId='session-b')
    with pytest.raises(core.ActionError):
        mirror.decide_other(owner, payload)
    with sqlite3.connect(path) as c:
        assert c.execute('SELECT status FROM tasks').fetchone()[0] == 'blocked'
        assert c.execute('SELECT COUNT(*) FROM task_comments').fetchone()[0] == 0


@pytest.mark.parametrize('choices', [True, False])
def test_rest_other_roundtrip_keeps_identity_and_never_archives_typed_archive(tmp_path, choices):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from test_withdrawal import ROOT, load_module
    mirror, store, path, hr = fixture(tmp_path)
    if not choices:
        hr.pop('choices')
        with sqlite3.connect(path) as c:
            c.execute('UPDATE tasks SET human_request=?', (json.dumps(hr),))
    api = load_module('question_display_api', ROOT / 'dashboard' / 'plugin_api.py')
    api.RETRY_INTERVAL_S = 0
    app = FastAPI()
    app.include_router(api.create_router(ledger_dir=tmp_path / 'ledger', state_db=tmp_path / 'missing.db',
        action_store=store, owner_resolver=lambda sid: dict(OWNER, storedSessionId=sid),
        kanban_boards=[('default', path)], kanban_mirror=mirror))
    with TestClient(app) as client:
        r = client.get('/requests', params={'session': 'session-a'}).json()['requests'][0]
        assert r['presentation']['sourceRef'] == dict(gatewayId=OWNER['gatewayId'], profile=OWNER['profile'], type='kanban', board='default', id='t_test')
        assert r['presentation']['question'] == 'Pick one.'
        if not choices:
            assert [c['label'] for c in r['choices']] == ['Unblock', 'Archive']
        payload = dict(session='session-a', expectedRevision=1, choiceId='answer-text', revisionText='archive', idempotencyKey='rest-other')
        url = f"/requests/{r['requestId']}/decisions"
        reply = client.post(url, json=payload)
        assert reply.status_code == 200, reply.text
        op = reply.json()['operation']
        assert op['dispatchState'] == 'delivered'
        assert op['intent']['executionAuthorized'] is False
        assert client.post(url, json=payload).json()['operation']['operationId'] == op['operationId']
        current = client.get(f"/requests/{r['requestId']}", params={'session': 'session-a'}).json()['request']
        assert current['requestId'] == r['requestId'] and current['revision'] == 1
    with sqlite3.connect(path) as c:
        assert c.execute('SELECT status FROM tasks').fetchone()[0] == 'ready'
        assert c.execute('SELECT COUNT(*) FROM task_comments').fetchone()[0] == 1
