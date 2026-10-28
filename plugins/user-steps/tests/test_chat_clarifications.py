import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


core = load('clarification_core', ROOT / 'core.py')
chat = load('clarification_chat', ROOT / 'chat.py')
OWNER = {'gatewayId': 'test', 'profile': 'default', 'storedSessionId': 'session'}
QUESTION = 'Do you mean open a pull request implementing these strategy changes, or something else?'


@pytest.fixture
def env(tmp_path):
    st = core.ActionStore(tmp_path / 'actions.sqlite3')
    step = dict(id='us_test', text=QUESTION, paragraph=QUESTION, kind='choose',
                session='session', options=['open a pull request', 'something else', 'invented permission'])
    r = st.migrate_step(OWNER, step)
    db = tmp_path / 'state.db'
    with sqlite3.connect(db) as c:
        c.execute('CREATE TABLE messages (id INTEGER PRIMARY KEY,session_id TEXT,role TEXT,content TEXT,active INTEGER,display_kind TEXT,tool_calls TEXT)')
        c.executemany('INSERT INTO messages VALUES (?,?,?,?,1,NULL,NULL)', [
            (1, 'session', 'assistant', core.directive(['us_test'], 'session', QUESTION)),
            (2, 'session', 'user', 'make the changes you want'),
            (3, 'session', 'assistant', 'Updated PLAN.md, GROWTH.md and OUTCOMES.md locally. This directory is not a git repository; no pull request was created.')])
    return st, r, db


def judged(system, user):
    return json.dumps({'outcome': 'superseded', 'quote': json.loads(user)['answer']})


def test_literal_options_and_non_authorizing_answer(env):
    st, r, _ = env
    assert r['state'] == 'open' and r['scope']['complete']
    # desktop-1321064734: the question's real alternative is the button; 'something else' is the free-text answer
    pr = 'Open a pull request implementing these strategy changes'
    assert [c['label'] for c in r['choices']] == [pr, 'Answer in your own words']
    d = dict(requestId=r['requestId'], expectedRevision=1, choiceId='answer-0',
             revisionText=pr, idempotencyKey='test')
    op = st.decide(OWNER, d)['operation']
    assert op['intent']['executionAuthorized'] is False
    assert op['intent']['permissionGrants'] == []


def test_transcript_redirect_supersedes_without_button_approval(env):
    st, r, db = env
    result = chat.reconcile(core, st, OWNER, judged, db)
    assert result[0]['request']['state'] == 'superseded'
    assert 'resolution' not in result[0]['request']
    assert result[0]['chatEvidence']['messageIds'] == [1, 2, 3]
    assert result[0]['chatEvidence']['executionAuthorized'] is False
    with st._connect() as c:
        assert c.execute('SELECT count(*) FROM operations').fetchone()[0] == 0
    assert chat.reconcile(core, st, OWNER, judged, db) == []


@pytest.mark.parametrize('mutation', [
    "UPDATE messages SET session_id='other' WHERE id=2",
    "UPDATE messages SET active=0 WHERE id=2",
    "UPDATE messages SET display_kind='notice' WHERE id=2",
    "UPDATE messages SET content='yes' WHERE id=2",
    "UPDATE messages SET content='do it' WHERE id=2",
    "DELETE FROM messages WHERE id=3",
    "UPDATE messages SET content='unbound question' WHERE id=1",
])
def test_bad_evidence_cannot_retire(env, mutation):
    st, r, db = env
    with sqlite3.connect(db) as c:
        c.execute(mutation)
    assert chat.reconcile(core, st, OWNER, judged, db) == []
    assert st.get(OWNER, r['requestId'])['state'] == 'open'


def test_unmatched_reply_remains_open(env):
    st, r, db = env
    assert chat.reconcile(core, st, OWNER, lambda *_: '{"outcome":"unmatched"}', db) == []
    assert st.get(OWNER, r['requestId'])['state'] == 'open'


def test_stale_counts_flag_answered_but_open_until_retired(env):
    """Detection (desktop-1321064734): a request still up after the user answered in chat is counted."""
    st, r, db = env
    ap = st.migrate_step(OWNER, dict(id='us_ap', text='Approve the deploy', paragraph='Want me to deploy?',
                                     kind='approve', session='session', options=[]))
    assert ap['state'] == 'needs_context'
    got = chat.stale_counts(core, st, db)
    assert got['answeredOpen'] == 1 and got['answeredOpenIds'] == [r['requestId']]
    assert got['planPending'] == 1
    chat.reconcile(core, st, OWNER, judged, db)
    assert chat.stale_counts(core, st, db)['answeredOpen'] == 0


def test_revision_and_owner_safety(env):
    st, r, db = env
    with pytest.raises(core.ActionError):
        chat.reconcile(core, st, dict(OWNER, storedSessionId='other'), judged, db,
                       request_id=r['requestId'], expected_revision=1)
    st.revise(OWNER, r['requestId'], 1, r)
    with pytest.raises(core.ActionError):
        chat.reconcile(core, st, OWNER, judged, db, request_id=r['requestId'], expected_revision=1)
    assert chat.reconcile(core, st, OWNER, judged, db) == []


def test_changed_evidence_during_judgment_rejected(env):
    st, r, db = env
    def race(system, user):
        with sqlite3.connect(db) as c:
            c.execute('UPDATE messages SET active=0 WHERE id=2')
        return judged(system, user)
    assert chat.reconcile(core, st, OWNER, race, db) == []


def test_approval_never_reconciled(env):
    st, r, db = env
    other = dict(id='us_approval', text=QUESTION, paragraph=QUESTION, kind='approve', session='session')
    approval = st.migrate_step(OWNER, other)
    chat.reconcile(core, st, OWNER, judged, db)
    assert st.get(OWNER, approval['requestId'])['state'] == 'needs_context'


def test_missing_plan_returns_agent_work(monkeypatch, env):
    plugin = load('clarification_plugin', ROOT / '__init__.py')
    st, r, _ = env
    monkeypatch.delenv('HERMES_KANBAN_TASK', raising=False)
    monkeypatch.delenv('HERMES_USER_STEPS_OFF', raising=False)
    monkeypatch.setattr(plugin.core, 'server_owner', lambda _: OWNER)
    monkeypatch.setattr(plugin.core, 'ActionStore', lambda: st)
    monkeypatch.setattr(plugin.core, '_worth_asking', lambda _: True)
    monkeypatch.setattr(plugin.core, 'extract', lambda *_: {'steps': [{'kind': 'approve', 'text': 'Approve the changes?'}]})
    assert plugin.publication_buffer(session_id='session', final_response='Approve?')['hold']
    assert 'replace' in plugin.publication_buffer(session_id='session', final_response='Approve?', attempt=2)
    result = json.loads(plugin.publish_action_request({'request': {'kind': 'approval', 'scope': {'complete': False}}}, 'session'))
    assert result['status'] == 422 and 'agent work' in result['error']
    broken = dict(schemaVersion=2, owner=OWNER, requestId='00000000-0000-0000-0000-000000000099',
        revision=1, kind='approval', title='Approve the changes?',
        scope={'complete': True, 'markdown': 'Exact plan.', 'sourceRefs': []},
        limits=[], exclusions=[], outcomes={'revise':'Revise the plan'},
        choices=[{'id':'revise','label':'Revise','effect':'revise'}], aliases=[], state='open')
    st.create(OWNER, broken)
    assert plugin.publication_buffer(session_id='session', final_response='Approve?')['hold']
    complete = dict(broken, outcomes={'approve':'Apply exactly this plan','revise':'Change plan','decline':'Do nothing'},
        choices=[{'id':e, 'label':e, 'effect':e} for e in ('approve','revise','decline')])
    st.revise(OWNER, broken['requestId'], 1, complete)
    assert plugin.publication_buffer(session_id='session', final_response='Approve?') is None
    monkeypatch.setattr(plugin.core, 'extract', lambda *_: {'steps': [{'kind': 'choose', 'text': QUESTION}]})
    assert plugin.publication_buffer(session_id='session', final_response=QUESTION) is None


def test_existing_legacy_question_upgraded_and_chat_still_retires(env):
    st, r, db = env
    old = dict(r, requestId='00000000-0000-0000-0000-000000000001', state='needs_context')
    old['scope'] = dict(r['scope'], complete=False)
    old['aliases'] = [dict(r['aliases'][0], id='us_old')]
    old['scope']['sourceRefs'] = old['aliases']
    old['choices'] = [{'id': 'request-context', 'label': 'Request complete action', 'effect': 'revise'}]
    st.create(OWNER, old)
    upgraded = st.migrate_step(OWNER, dict(id='us_old', text=QUESTION, paragraph=QUESTION,
        kind='choose', session='session', options=['something else']))
    assert upgraded['revision'] == 2 and upgraded['state'] == 'open'
    # desktop-1321064734: the real alternative, not a lone 'something else' button
    assert upgraded['choices'][0]['label'] == 'Open a pull request implementing these strategy changes'
    with sqlite3.connect(db) as c:
        c.execute('UPDATE messages SET content=? WHERE id=1', (core.directive(['us_old'], 'session', QUESTION),))
    out = chat.reconcile(core, st, OWNER, judged, db, request_id=upgraded['requestId'], expected_revision=2)
    assert out[0]['request']['state'] == 'superseded'


def test_clarification_answer_outcome_contract(env):
    _, r, _ = env
    r['outcomes'] = {'answer': 'Send only this answer; no permission granted.'}
    assert core.validate_action(r)['outcomes'] == r['outcomes']


def test_explicit_producer_question_not_duplicated(env, tmp_path):
    st, r, db = env
    r.update(requestId='00000000-0000-0000-0000-000000000088', aliases=[])
    st.create(OWNER, r)
    ledger = core.Ledger(tmp_path / 'ledger', db)
    out = core.process_reply(QUESTION, 'session', ledger=ledger, action_store=st, action_owner=OWNER,
        llm=lambda *_: json.dumps({'steps':[{'text':QUESTION, 'kind':'choose',
            'options':['open a pull request', 'something else'], 'verbatim_span':QUESTION}]}))
    assert out['steps'] == []
    assert len(st.requests(OWNER)) == 2


def test_pending_button_decision_cannot_be_retired(env):
    st, r, db = env
    def race(system, user):
        st.decide(OWNER, dict(requestId=r['requestId'], expectedRevision=1,
            choiceId='answer-text', revisionText='something else', idempotencyKey='pending'))
        return judged(system, user)
    assert chat.reconcile(core, st, OWNER, race, db) == []
    assert st.get(OWNER, r['requestId'])['state'] == 'open'


def test_repeated_retired_question_is_held(env, tmp_path):
    st, r, db = env
    ledger = core.Ledger(tmp_path / 'ledger', db)
    step = dict(id='us_test', text=QUESTION, paragraph=QUESTION, verbatim_span=QUESTION,
                kind='choose', options=['open a pull request', 'something else'], session='session', status='open',
                created=1, requestId=r['requestId'])
    with ledger.edit('session') as data:
        data['steps'].append(step)
    chat.reconcile(core, st, OWNER, judged, db)
    out = core.process_reply(QUESTION, 'session', ledger=ledger, action_store=st, action_owner=OWNER,
        llm=lambda *_: json.dumps({'steps': [step], 'resolved': []}))
    assert out['text'] == core.HELD_REPLY


def test_repair_rest_rejects_client_evidence_and_stale_revision(env, monkeypatch, tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    st, r, db = env
    api = load('clarification_api', ROOT / 'dashboard/plugin_api.py')
    monkeypatch.setattr(api._load('chat'), 'llm', judged)
    # Function defaults bind at import; use a wrapper to inject the test judge.
    module = api._load('chat')
    original = module.reconcile
    monkeypatch.setattr(module, 'reconcile', lambda c, s, o, **kw: original(c, s, o, judged, **kw))
    app = FastAPI()
    app.include_router(api.create_router(tmp_path / 'ledger', db, action_store=st,
                                        owner_resolver=lambda sid: dict(OWNER, storedSessionId=sid)))
    client = TestClient(app)
    path = f'/requests/{r["requestId"]}/reconcile-chat'
    body = {'session': 'session', 'expectedRevision': 1}
    assert client.post(path, json={**body, 'answer': 'fake'}).status_code == 422
    assert client.post(path, json={**body, 'expectedRevision': 2}).status_code == 409
    result = client.post(path, json=body)
    assert result.status_code == 200 and result.json()['request']['state'] == 'superseded'
    assert client.get(f'/requests/{r["requestId"]}', params={'session':'session'}).json()['request']['state'] == 'superseded'


@pytest.mark.parametrize('wrap', [
    lambda j: '```json\n' + j + '\n```',   # observed live (Diplomacy request bedaa07f, 2026-10-27)
    lambda j: '```\n' + j + '\n```',
    lambda j: '  ' + j + '\n',
])
def test_fenced_judge_output_still_reconciles(env, wrap):
    st, r, db = env
    out = chat.reconcile(core, st, OWNER, lambda s, u: wrap(judged(s, u)), db)
    assert out and out[0]['request']['state'] == 'superseded'
    assert out[0]['chatEvidence']['executionAuthorized'] is False


@pytest.mark.parametrize('raw', [
    'Sure! ```json\n{"outcome":"answered","quote":"make the changes you want"}\n```',
    '```json\n{"outcome":"answered","quote":"make the changes you want"}\n``` trailing',
    '{"outcome":"answered","quote":"make the changes you want"} {"outcome":"unmatched"}',
    '```json\n["answered"]\n```',
])
def test_judge_prose_or_extra_objects_fail_closed(env, raw):
    st, r, db = env
    assert chat.reconcile(core, st, OWNER, lambda s, u: raw, db) == []
    assert st.get(OWNER, r['requestId'])['state'] == 'open'
