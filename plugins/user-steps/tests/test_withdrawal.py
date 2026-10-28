"""Withdrawn asks: a moot request leaves the For-you queue without a human decision.

A withdrawal grants nothing and admits no turn. Triggers: the linked Kanban card
finished or had its block cleared after the ask (system), the owning chat said it
is answered/moot or withdrew it (agent), or the user dismissed it (user).
All persistence is isolated under pytest's temporary directory.
"""
import importlib.util
import json
import sqlite3
import time
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


core = load_module('withdrawal_core', ROOT / 'core.py')
OWNER = {'gatewayId': 'test-gateway', 'profile': 'default', 'storedSessionId': 'session-a'}
T0 = 1_800_000_000.0

# The exact 12:05 close-out reply that produced the stale "Review and approve card" ask.
CLOSEOUT = (
    "**The backup viewer works; one remote screen is still unavailable.** The final verification "
    "card is **in review**, not yet approved.\n\n### Delivered\n- **Six-host diagnostics:** the ops "
    "plugin checked all six hosts.\n\n### Still open\n- **Review of `t_1418b643`.**\n\n"
    "**Nothing is currently assigned to you.** This delivers diagnostics across all six hosts.")
SPAN = "The final verification card is **in review**, not yet approved."
INCIDENT_STEP = {'text': 'Review and approve card t_1418b643 (ops verification)', 'kind': 'approve',
                 'verbatim_span': SPAN, 'options': []}


def complete_request(aliases=None, title='Synthetic change', refs=None):
    return dict(schemaVersion=2, owner=OWNER.copy(), requestId=str(uuid.uuid4()), revision=1,
                kind='approval', title=title,
                scope=dict(markdown='Change example.py; run tests.', complete=True, sourceRefs=refs or []),
                limits=[], exclusions=[], outcomes=dict(approve='Authorize this scope only',
                                                        revise='Keep work blocked', decline='Cancel'),
                choices=[dict(id=e, label=e.title(), effect=e) for e in ('approve', 'revise', 'decline')],
                aliases=aliases or [], state='open')


def decision(r, key='key-1', choice='approve', text=None):
    return dict(requestId=r['requestId'], expectedRevision=r['revision'], choiceId=choice,
                revisionText=text, idempotencyKey=key)


class Receiver:
    def deliver(self, op):
        return {'deliveryId': op['operationId']}

    def readback(self, op, receipt):
        return True


def env(tmp_path):
    store = core.ActionStore(tmp_path / 'actions.sqlite3')
    ledger = core.Ledger(tmp_path / 'ledger', tmp_path / 'missing-state.db')
    return store, ledger


def board(tmp_path, *tasks):
    """Minimal Kanban board: tasks(id, status, completed_at) + task_events(task_id, kind, created_at)."""
    path = tmp_path / 'kanban.db'
    c = sqlite3.connect(path)
    c.executescript('CREATE TABLE IF NOT EXISTS tasks (id TEXT PRIMARY KEY, title TEXT, status TEXT, completed_at INTEGER);'
                    'CREATE TABLE IF NOT EXISTS task_events (id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT, '
                    'kind TEXT, payload TEXT, created_at INTEGER NOT NULL);')
    for t in tasks:
        c.execute('INSERT OR REPLACE INTO tasks (id, title, status, completed_at) VALUES (?,?,?,?)',
                  (t['id'], 'card', t['status'], t.get('completed_at')))
        for kind, ts, *payload in t.get('events', []):
            c.execute('INSERT INTO task_events (task_id, kind, payload, created_at) VALUES (?,?,?,?)',
                      (t['id'], kind, payload[0] if payload else None, ts))
    c.commit(); c.close()
    return [('default', path)]


def extract_incident(store, ledger, now=T0):
    out = core.process_reply(SPAN + "\n\nPlease review it.", 'session-a', ledger=ledger, action_store=store,
                             action_owner=OWNER, now=now, llm=lambda *a: json.dumps({'steps': [INCIDENT_STEP], 'resolved': []}))
    assert len(out['steps']) == 1
    return out['steps'][0]


# ---- the withdraw transition ---------------------------------------------------------------------------

def test_withdraw_is_terminal_grants_nothing_and_is_idempotent(tmp_path):
    store, ledger = env(tmp_path)
    step = extract_incident(store, ledger)
    rid = step['requestId']
    first = core.withdraw_request(store, ledger, OWNER, rid, by='agent', reason='Card finished')
    assert first['state'] == 'withdrawn'
    assert first['withdrawal']['by'] == 'agent' and first['withdrawal']['reason'] == 'Card finished'
    assert 'resolution' not in first  # not a decision
    assert core.withdraw_request(store, ledger, OWNER, rid, by='user', reason='again') == first
    with pytest.raises(core.ActionError) as err:
        store.decide(OWNER, decision(first, choice='request-context', text='x'))
    assert err.value.status == 409
    with sqlite3.connect(tmp_path / 'actions.sqlite3') as c:
        assert c.execute('SELECT count(*) FROM operations').fetchone()[0] == 0
    # The legacy ledger step is retired too, so the strip and steps payload stop listing it.
    _, s = ledger.find(step['id'], 'session-a')
    assert s['status'] == 'resolved' and s['resolution'].startswith('No longer needed: ') and s['withdrawn'] is True
    assert core.steps_payload(ledger, 'session-a')['steps'] == []


def test_withdraw_refused_once_a_decision_is_recorded_or_delivered(tmp_path):
    store, ledger = env(tmp_path)
    r = store.create(OWNER, complete_request())
    op = store.decide(OWNER, decision(r))['operation']
    with pytest.raises(core.ActionError) as err:
        core.withdraw_request(store, ledger, OWNER, r['requestId'], by='user', reason='not needed')
    assert err.value.status == 409
    store.dispatch(OWNER, op['operationId'], Receiver())
    with pytest.raises(core.ActionError) as err:
        core.withdraw_request(store, ledger, OWNER, r['requestId'], by='system', reason='card done')
    assert err.value.status == 409
    assert store.get(OWNER, r['requestId'])['state'] == 'resolved'


def test_withdraw_checks_revision_and_owner(tmp_path):
    store, ledger = env(tmp_path)
    r = store.create(OWNER, complete_request())
    with pytest.raises(core.ActionError) as err:
        core.withdraw_request(store, ledger, OWNER, r['requestId'], by='user', reason='x', expected_revision=2)
    assert err.value.status == 409
    with pytest.raises(core.ActionError) as err:
        core.withdraw_request(store, ledger, dict(OWNER, storedSessionId='other'), r['requestId'], by='agent', reason='x')
    assert err.value.status == 404
    with pytest.raises(core.ActionError):
        core.withdraw_request(store, ledger, OWNER, r['requestId'], by='model', reason='x')
    with pytest.raises(core.ActionError):
        core.withdraw_request(store, ledger, OWNER, r['requestId'], by='user', reason='  ')
    assert store.get(OWNER, r['requestId'])['state'] == 'open'


def test_producer_revision_revives_a_withdrawn_request(tmp_path):
    store, ledger = env(tmp_path)
    r = store.create(OWNER, complete_request())
    core.withdraw_request(store, ledger, OWNER, r['requestId'], by='user', reason='not now')
    replacement = dict(r, title='Changed plan')
    revived = store.revise(OWNER, r['requestId'], 1, replacement)
    assert revived['revision'] == 2 and revived['state'] == 'open'
    assert 'withdrawal' not in store.get(OWNER, r['requestId'])


def test_created_at_is_recorded_and_exposed(tmp_path):
    store, ledger = env(tmp_path)
    step = extract_incident(store, ledger, now=T0)
    r = store.view(OWNER, step['requestId'])
    assert r['createdAt'] == core._iso(T0)
    # The frozen decision body stays exact: card bindings compare it byte for byte.
    assert 'createdAt' not in store.get(OWNER, step['requestId'])


# ---- agent-side retirement ----------------------------------------------------------------------------

def test_extractor_resolution_never_withdraws_a_canonical_ask(tmp_path):
    # Reviewers A2/B13: one aux-LLM "resolved" id must not silently remove an ask the user has not answered.
    store, ledger = env(tmp_path)
    step = extract_incident(store, ledger)
    out = core.process_reply("Sure, I'll look at it after lunch.", 'session-a', ledger=ledger, action_store=store,
                             action_owner=OWNER, llm=lambda *a: json.dumps({'steps': [], 'resolved': [step['id']]}))
    assert out['resolved'] == [] and 'withdrawn' not in out
    assert store.get(OWNER, step['requestId'])['state'] == 'needs_context'
    _, s = ledger.find(step['id'], 'session-a')
    assert s['status'] == 'open'


def test_asking_again_after_a_withdrawal_is_a_fresh_ask_not_a_held_repeat(tmp_path):
    # Reviewer B1: a withdrawal is not a decline; repeating the ask must not replace the reply with HELD_REPLY.
    store, ledger = env(tmp_path)
    step = extract_incident(store, ledger)
    core.withdraw_request(store, ledger, OWNER, step['requestId'], by='system', reason='card done')
    out = core.process_reply(SPAN + "\n\nPlease review it.", 'session-a', ledger=ledger, action_store=store,
                             action_owner=OWNER, llm=lambda *a: json.dumps({'steps': [INCIDENT_STEP], 'resolved': []}))
    assert out['text'] != core.HELD_REPLY
    assert len(out['steps']) == 1 and out['steps'][0]['requestId'] != step['requestId']
    assert store.get(OWNER, out['steps'][0]['requestId'])['state'] in ('open', 'needs_context')


def test_action_request_tool_withdraws_only_its_own_session(tmp_path, monkeypatch):
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    monkeypatch.delenv('HERMES_KANBAN_TASK', raising=False)
    plugin = load_module('withdraw_tool_plugin', ROOT / '__init__.py')
    monkeypatch.setattr(plugin.core, 'home', lambda: tmp_path)
    monkeypatch.setattr(plugin.core, 'server_owner', lambda sid: dict(OWNER, storedSessionId=sid))
    payload = complete_request()
    for key in ('owner', 'requestId', 'revision'):
        payload.pop(key)
    r = json.loads(plugin.publish_action_request({'request': payload}, session_id='session-a'))['request']
    other = json.loads(plugin.publish_action_request(
        {'withdraw': {'requestId': r['requestId'], 'reason': 'x'}}, session_id='session-b'))
    assert other['status'] == 404
    both = json.loads(plugin.publish_action_request(
        {'request': payload, 'withdraw': {'requestId': r['requestId'], 'reason': 'x'}}, session_id='session-a'))
    assert 'error' in both
    out = json.loads(plugin.publish_action_request(
        {'withdraw': {'requestId': r['requestId'], 'reason': 'Answered in chat'}}, session_id='session-a'))
    assert out['request']['state'] == 'withdrawn'
    assert out['request']['withdrawal'] == dict(out['request']['withdrawal'], by='agent', reason='Answered in chat')
    assert 'withdraw' in plugin.PRODUCER_SCHEMA['parameters']['properties']


# ---- work-linked auto-retirement (the incident) --------------------------------------------------------

def test_ask_about_a_card_retires_when_the_card_finishes_after_it(tmp_path):
    store, ledger = env(tmp_path)
    boards = board(tmp_path, {'id': 't_1418b643', 'status': 'review', 'events': [('review_requested', T0 - 20)]})
    step = extract_incident(store, ledger, now=T0)
    assert core.reconcile_moot(store, ledger, OWNER, boards=boards, now=T0 + 60) == []
    board(tmp_path, {'id': 't_1418b643', 'status': 'done', 'completed_at': int(T0 + 2633),
                     'events': [('completed', int(T0 + 2633))]})
    out = core.reconcile_moot(store, ledger, OWNER, boards=boards, now=T0 + 2700)
    assert [x['requestId'] for x in out] == [step['requestId']]
    r = store.view(OWNER, step['requestId'])
    assert r['state'] == 'withdrawn' and r['withdrawal']['by'] == 'system'
    assert 't_1418b643' in r['withdrawal']['reason']
    assert core.reconcile_moot(store, ledger, OWNER, boards=boards, now=T0 + 2800) == []


def test_card_already_finished_when_asked_does_not_retire_the_ask(tmp_path):
    store, ledger = env(tmp_path)
    boards = board(tmp_path, {'id': 't_1418b643', 'status': 'done', 'completed_at': int(T0 - 600),
                              'events': [('completed', int(T0 - 600))]})
    step = extract_incident(store, ledger, now=T0)
    assert core.reconcile_moot(store, ledger, OWNER, boards=boards, now=T0 + 600) == []
    assert store.get(OWNER, step['requestId'])['state'] == 'needs_context'


def test_unblocking_a_card_to_retry_keeps_the_ask_until_the_card_is_done(tmp_path):
    # Reviewer B1, the live sequence: blocked -> unblocked to retry -> claimed -> block_loop_detected (triage).
    store, ledger = env(tmp_path)
    boards = board(tmp_path, {'id': 't_54aa263b', 'status': 'blocked', 'events': [('blocked', T0 - 100)]})
    r = store.create(OWNER, complete_request(title='Fix GitHub billing for t_54aa263b'), created_at=T0)
    board(tmp_path, {'id': 't_54aa263b', 'status': 'running', 'events': [
        ('unblocked', T0 + 50), ('claimed', T0 + 60, json.dumps({'lock': 'x'}))]})
    assert core.reconcile_moot(store, ledger, OWNER, boards=boards, now=T0 + 70) == []
    board(tmp_path, {'id': 't_54aa263b', 'status': 'triage', 'events': [('block_loop_detected', T0 + 90)]})
    assert core.reconcile_moot(store, ledger, OWNER, boards=boards, now=T0 + 95) == []
    board(tmp_path, {'id': 't_54aa263b', 'status': 'done', 'completed_at': int(T0 + 200), 'events': [('completed', T0 + 200)]})
    assert [x['requestId'] for x in core.reconcile_moot(store, ledger, OWNER, boards=boards, now=T0 + 210)] == [r['requestId']]


def test_ask_made_while_its_card_is_in_triage_retires_when_the_card_is_done(tmp_path):
    # Reviewer B2: gave_up / block_loop_detected park a card in triage, a human wait.
    store, ledger = env(tmp_path)
    boards = board(tmp_path, {'id': 't_10101010', 'status': 'triage', 'events': [
        ('claimed', T0 - 60, json.dumps({'lock': 'x'})), ('gave_up', T0 - 30)]})
    r = store.create(OWNER, complete_request(title='Sign in to Vercel for t_10101010'), created_at=T0)
    assert core.reconcile_moot(store, ledger, OWNER, boards=boards, now=T0 + 10) == []
    board(tmp_path, {'id': 't_10101010', 'status': 'done', 'completed_at': int(T0 + 100), 'events': [('completed', T0 + 100)]})
    assert [x['requestId'] for x in core.reconcile_moot(store, ledger, OWNER, boards=boards, now=T0 + 110)] == [r['requestId']]


def test_a_named_card_still_in_flight_keeps_the_ask(tmp_path):
    # Reviewer B4: one finished card does not settle an ask that also names a running one.
    store, ledger = env(tmp_path)
    boards = board(tmp_path, {'id': 't_a1a1a1a1', 'status': 'review', 'events': [('review_requested', T0 - 5)]},
                   {'id': 't_b2b2b2b2', 'status': 'running', 'events': [('claimed', T0 - 5, json.dumps({'lock': 'x'}))]})
    r = store.create(OWNER, complete_request(title='Approve shipping t_a1a1a1a1 together with t_b2b2b2b2'), created_at=T0)
    board(tmp_path, {'id': 't_a1a1a1a1', 'status': 'done', 'completed_at': int(T0 + 5), 'events': [('completed', T0 + 5)]})
    assert core.reconcile_moot(store, ledger, OWNER, boards=boards, now=T0 + 10) == []
    board(tmp_path, {'id': 't_b2b2b2b2', 'status': 'done', 'completed_at': int(T0 + 20), 'events': [('completed', T0 + 20)]})
    assert [x['requestId'] for x in core.reconcile_moot(store, ledger, OWNER, boards=boards, now=T0 + 30)] == [r['requestId']]


def test_cards_named_only_in_the_evidence_sentence_are_not_watched(tmp_path):
    # Reviewer B4: "t_X is blocked on CI; meanwhile, which tier?" is not an ask about t_X.
    store, ledger = env(tmp_path)
    boards = board(tmp_path, {'id': 't_c3c3c3c3', 'status': 'blocked', 'events': [('blocked', T0 - 50)]})
    span = 't_c3c3c3c3 is blocked on CI; meanwhile, which pricing tier do you want?'
    step = {'text': 'Pick the pricing tier', 'kind': 'input', 'verbatim_span': span, 'options': []}
    out = core.process_reply(span, 'session-a', ledger=ledger, action_store=store, action_owner=OWNER, now=T0,
                             llm=lambda *a: json.dumps({'steps': [step], 'resolved': []}))
    rid = out['steps'][0]['requestId']
    board(tmp_path, {'id': 't_c3c3c3c3', 'status': 'done', 'completed_at': int(T0 + 60), 'events': [('completed', T0 + 60)]})
    assert core.reconcile_moot(store, ledger, OWNER, boards=boards, now=T0 + 90) == []
    assert store.get(OWNER, rid)['state'] in ('open', 'needs_context')


def test_an_unreadable_board_never_retires_anything(tmp_path):
    # Reviewer B5: a board that cannot be read could hold a duplicate id.
    store, ledger = env(tmp_path)
    boards = board(tmp_path, {'id': 't_d4d4d4d4', 'status': 'review', 'events': [('review_requested', T0 - 5)]})
    r = store.create(OWNER, complete_request(title='Review and approve card t_d4d4d4d4'), created_at=T0)
    board(tmp_path, {'id': 't_d4d4d4d4', 'status': 'done', 'completed_at': int(T0 + 5), 'events': [('completed', T0 + 5)]})
    broken = tmp_path / 'broken.db'
    broken.write_bytes(b'not a database ' * 200)
    assert core.reconcile_moot(store, ledger, OWNER, boards=boards + [('other', broken)], now=T0 + 10) == []
    assert [x['requestId'] for x in core.reconcile_moot(store, ledger, OWNER, boards=boards, now=T0 + 10)] == [r['requestId']]


def test_every_linked_card_must_settle_and_unknown_ids_never_retire(tmp_path):
    store, ledger = env(tmp_path)
    boards = board(tmp_path, {'id': 't_aaaaaaaa', 'status': 'blocked', 'events': [('blocked', T0 - 5)]},
                   {'id': 't_bbbbbbbb', 'status': 'review', 'events': [('review_requested', T0 - 5)]})
    two = store.create(OWNER, complete_request(title='Approve merging t_aaaaaaaa and t_bbbbbbbb'), created_at=T0)
    ghost = store.create(OWNER, complete_request(title='Approve t_cccccccc'), created_at=T0)
    board(tmp_path, {'id': 't_aaaaaaaa', 'status': 'done', 'completed_at': int(T0 + 5)})
    assert core.reconcile_moot(store, ledger, OWNER, boards=boards, now=T0 + 10) == []
    board(tmp_path, {'id': 't_bbbbbbbb', 'status': 'archived', 'events': [('archived', int((T0 + 7) * 1000))]})  # ms stamp
    assert [x['requestId'] for x in core.reconcile_moot(store, ledger, OWNER, boards=boards, now=T0 + 10)] == [two['requestId']]
    assert store.get(OWNER, ghost['requestId'])['state'] == 'open'


def test_explicit_kanban_source_refs_are_watched_but_markdown_mentions_are_not(tmp_path):
    store, ledger = env(tmp_path)
    boards = board(tmp_path, {'id': 't_dddddddd', 'status': 'blocked', 'events': [('blocked', T0 - 5)]},
                   {'id': 't_eeeeeeee', 'status': 'blocked', 'events': [('blocked', T0 - 5)]})
    ref = dict(gatewayId='test-gateway', profile='default', type='kanban', board='default', id='t_dddddddd')
    linked = store.create(OWNER, complete_request(refs=[ref]), created_at=T0)
    prose = complete_request()
    prose['scope']['markdown'] = 'Context: t_eeeeeeee is running a migration. Approve the bot name.'
    prose = store.create(OWNER, prose, created_at=T0)
    board(tmp_path, {'id': 't_dddddddd', 'status': 'done', 'completed_at': int(T0 + 5)},
          {'id': 't_eeeeeeee', 'status': 'done', 'completed_at': int(T0 + 5)})
    assert [x['requestId'] for x in core.reconcile_moot(store, ledger, OWNER, boards=boards, now=T0 + 9)] == [linked['requestId']]
    assert store.get(OWNER, prose['requestId'])['state'] == 'open'


def test_pending_decision_blocks_auto_retirement(tmp_path):
    store, ledger = env(tmp_path)
    boards = board(tmp_path, {'id': 't_ffffffff', 'status': 'blocked', 'events': [('blocked', T0 - 5)]})
    r = store.create(OWNER, complete_request(title='Approve t_ffffffff'), created_at=T0)
    store.decide(OWNER, decision(r))
    board(tmp_path, {'id': 't_ffffffff', 'status': 'done', 'completed_at': int(T0 + 5)})
    assert core.reconcile_moot(store, ledger, OWNER, boards=boards, now=T0 + 9) == []


def test_card_that_was_running_when_asked_is_not_counted(tmp_path):
    # "Check the screenshots when the card finishes" names a running card: its finishing is not the answer.
    store, ledger = env(tmp_path)
    boards = board(tmp_path, {'id': 't_abababab', 'status': 'running', 'events': [('claimed', T0 - 50)]})
    r = store.create(OWNER, complete_request(title='Pick the clearest screenshot from t_abababab when it finishes'),
                     created_at=T0)
    board(tmp_path, {'id': 't_abababab', 'status': 'done', 'completed_at': int(T0 + 60), 'events': [('completed', T0 + 60)]})
    assert core.reconcile_moot(store, ledger, OWNER, boards=boards, now=T0 + 90) == []
    assert store.get(OWNER, r['requestId'])['state'] == 'open'


def test_a_reviewer_claim_keeps_the_review_wait(tmp_path):
    store, ledger = env(tmp_path)
    boards = board(tmp_path, {'id': 't_cdcdcdcd', 'status': 'review', 'events': [
        ('review_requested', T0 - 30), ('claimed', T0 - 10, json.dumps({'source_status': 'review'}))]})
    r = store.create(OWNER, complete_request(title='Review and approve card t_cdcdcdcd'), created_at=T0)
    board(tmp_path, {'id': 't_cdcdcdcd', 'status': 'done', 'completed_at': int(T0 + 60), 'events': [('completed', T0 + 60)]})
    assert [x['requestId'] for x in core.reconcile_moot(store, ledger, OWNER, boards=boards, now=T0 + 90)] == [r['requestId']]


def test_a_worker_claim_ends_the_block_wait(tmp_path):
    # Blocked, then picked up by a worker before the ask: the card was running when asked, so not counted.
    store, ledger = env(tmp_path)
    boards = board(tmp_path, {'id': 't_efefefef', 'status': 'running', 'events': [
        ('blocked', T0 - 30), ('claimed', T0 - 10, json.dumps({'lock': 'x'}))]})
    store.create(OWNER, complete_request(title='Approve t_efefefef'), created_at=T0)
    board(tmp_path, {'id': 't_efefefef', 'status': 'done', 'completed_at': int(T0 + 60), 'events': [('completed', T0 + 60)]})
    assert core.reconcile_moot(store, ledger, OWNER, boards=boards, now=T0 + 90) == []


def test_rerenders_keep_the_ask_time_and_producer_revisions_restart_it(tmp_path):
    store, ledger = env(tmp_path)
    asked = time.time() - 10_000
    r = store.create(OWNER, complete_request(), created_at=asked)
    again = store.revise(OWNER, r['requestId'], 1, dict(r, title='Re-rendered'), fresh=False)  # server-side re-render
    assert store.created_at(OWNER, r['requestId']) == asked
    rescoped = store.revise(OWNER, r['requestId'], again['revision'], dict(again, title='The full plan'))  # producer
    assert store.created_at(OWNER, r['requestId']) > asked + 9_000
    core.withdraw_request(store, ledger, OWNER, r['requestId'], by='agent', reason='moot')
    before = store.created_at(OWNER, r['requestId'])
    store.revise(OWNER, r['requestId'], rescoped['revision'], dict(rescoped, title='Asked again'), fresh=False)
    assert store.created_at(OWNER, r['requestId']) >= before  # a revival is always a fresh ask
    assert 'withdrawal' not in store.view(OWNER, r['requestId'])


def test_a_revision_written_by_an_older_process_is_a_fresh_ask(tmp_path):
    # Reviewer A1: a process running the previous code bumps the revision without stamping the ask time.
    store, ledger = env(tmp_path)
    boards = board(tmp_path, {'id': 't_e5e5e5e5', 'status': 'review', 'events': [('review_requested', T0 - 5)]})
    r = store.create(OWNER, complete_request(title='Review and approve card t_e5e5e5e5'), created_at=T0)
    core.withdraw_request(store, ledger, OWNER, r['requestId'], by='user', reason='not now')
    with sqlite3.connect(tmp_path / 'actions.sqlite3') as c:  # what the previous revise() wrote
        ns, body = c.execute('SELECT namespace, body FROM revisions WHERE id=?', (r['requestId'],)).fetchone()
        c.execute('INSERT INTO revisions VALUES (?,?,?,?)',
                  (ns, r['requestId'], 2, json.dumps(dict(json.loads(body), revision=2))))
        c.execute("UPDATE requests SET revision=2, state='open' WHERE id=?", (r['requestId'],))
    board(tmp_path, {'id': 't_e5e5e5e5', 'status': 'done', 'completed_at': int(T0 + 50), 'events': [('completed', T0 + 50)]})
    assert core.reconcile_moot(store, ledger, OWNER, boards=boards, now=T0 + 100) == []
    assert store.get(OWNER, r['requestId'])['state'] == 'open'
    assert store.created_at(OWNER, r['requestId']) == T0 + 100


def test_requests_bound_to_a_decision_card_are_never_withdrawn(tmp_path):
    # Reviewer A3: withdrawing would leave its dedicated decision card blocked on the board with no trace.
    store, ledger = env(tmp_path)
    ref = dict(gatewayId='test-gateway', profile='default', type='kanban', board='default', id='t_f6f6f6f6')
    boards = board(tmp_path, {'id': 't_f6f6f6f6', 'status': 'blocked', 'events': [('blocked', T0 - 5)]})
    r = store.create(OWNER, complete_request(title='Approve t_f6f6f6f6', aliases=[ref]), created_at=T0)
    with pytest.raises(core.ActionError) as err:
        core.withdraw_request(store, ledger, OWNER, r['requestId'], by='user', reason='not needed')
    assert err.value.status == 409
    board(tmp_path, {'id': 't_f6f6f6f6', 'status': 'done', 'completed_at': int(T0 + 5), 'events': [('completed', T0 + 5)]})
    assert core.reconcile_moot(store, ledger, OWNER, boards=boards, now=T0 + 10) == []
    assert store.get(OWNER, r['requestId'])['state'] == 'open'


def test_requests_hides_withdrawn_rows_from_callers_that_predate_the_state(tmp_path):
    # Reviewer C1: route bodies mounted before this change call requests(), and older renderers reject a
    # whole queue that holds an unknown state.
    store, ledger = env(tmp_path)
    store.create(OWNER, complete_request(title='Live'))
    gone = store.create(OWNER, complete_request(title='Moot'))
    core.withdraw_request(store, ledger, OWNER, gone['requestId'], by='user', reason='not needed')
    assert [r['title'] for r in store.requests(OWNER)] == ['Live']
    assert [r['state'] for r in store.requests(OWNER, include_withdrawn=True)] == ['open', 'withdrawn']


def test_the_reconcile_pass_reads_without_waiting_on_a_writer(tmp_path):
    # Reviewer B8: the pass runs on every queue read and turn; it must not queue behind a held write lock.
    store, ledger = env(tmp_path)
    boards = board(tmp_path, {'id': 't_07070707', 'status': 'blocked', 'events': [('blocked', T0 - 5)]})
    store.create(OWNER, complete_request(title='Approve t_07070707'), created_at=T0)
    writer = sqlite3.connect(tmp_path / 'actions.sqlite3', timeout=1)
    writer.execute('BEGIN IMMEDIATE')
    try:
        started = time.monotonic()
        assert core.reconcile_moot(store, ledger, OWNER, boards=boards, now=T0 + 10) == []
        assert time.monotonic() - started < 1.0
    finally:
        writer.rollback()
        writer.close()


def test_canonical_withdrawal_retires_its_ledger_step_on_the_next_reply(tmp_path):
    store, ledger = env(tmp_path)
    step = extract_incident(store, ledger)
    store.withdraw(OWNER, step['requestId'], by='system', reason='card done')  # store only, no ledger update
    other = {'text': 'Pick a bot name', 'kind': 'input', 'verbatim_span': 'Which bot name do you want?', 'options': []}
    core.process_reply('Which bot name do you want?', 'session-a', ledger=ledger, action_store=store,
                       action_owner=OWNER, llm=lambda *a: json.dumps({'steps': [other], 'resolved': []}))
    _, s = ledger.find(step['id'], 'session-a')
    assert s['status'] == 'resolved'


def test_board_discovery_skips_the_archived_board_area(tmp_path):
    board(tmp_path, {'id': 't_1418b643', 'status': 'done', 'completed_at': int(T0 + 5)})
    for slug in ('hermes', '_archived'):
        (tmp_path / 'kanban' / 'boards' / slug).mkdir(parents=True)
        board(tmp_path / 'kanban' / 'boards' / slug, {'id': f't_{slug[-4:].strip("_")}0000'[:10], 'status': 'todo'})
    assert [slug for slug, _ in core.kanban_boards(tmp_path)] == ['default', 'hermes']


# ---- surfaces: REST queue and the agent's per-turn context -------------------------------------------

def router_client(tmp_path, store, boards):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    api = load_module('withdrawal_api', ROOT / 'dashboard' / 'plugin_api.py')
    cards = load_module('withdrawal_cards', ROOT / 'cards.py')
    app = FastAPI()
    app.include_router(api.create_router(
        ledger_dir=tmp_path / 'ledger', state_db=tmp_path / 'missing-state.db', action_store=store,
        owner_resolver=lambda session: dict(OWNER, storedSessionId=session),
        receiver=cards.DecisionInbox(tmp_path / 'inbox.db'), kanban_boards=boards))
    return TestClient(app)


def test_rest_queue_reconciles_hides_withdrawn_and_lets_the_user_dismiss(tmp_path):
    store, ledger = env(tmp_path)
    boards = board(tmp_path, {'id': 't_1418b643', 'status': 'review', 'events': [('review_requested', time.time() - 200)]})
    step = extract_incident(store, ledger, now=time.time() - 120)
    keep = store.create(OWNER, complete_request(title='Pick a bot name'))
    client = router_client(tmp_path, store, boards)
    assert client.get('/ping').json()['features'] == ['withdraw']
    assert {r['requestId'] for r in client.get('/requests', params={'session': 'session-a'}).json()['requests']} == {
        step['requestId'], keep['requestId']}
    board(tmp_path, {'id': 't_1418b643', 'status': 'done', 'completed_at': int(time.time()),
                     'events': [('completed', int(time.time()))]})
    listed = client.get('/requests', params={'session': 'session-a'}).json()['requests']
    assert [r['requestId'] for r in listed] == [keep['requestId']]
    full = client.get('/requests', params={'session': 'session-a', 'include': 'withdrawn'}).json()['requests']
    gone = next(r for r in full if r['requestId'] == step['requestId'])
    assert gone['state'] == 'withdrawn' and gone['withdrawal']['by'] == 'system'
    body = {'session': 'session-a', 'expectedRevision': 1, 'reason': 'Dismissed in Desktop as not needed'}
    response = client.post(f"/requests/{keep['requestId']}/withdraw", json=body)
    assert response.status_code == 200
    assert response.json()['request']['withdrawal']['by'] == 'user'
    assert client.get('/requests', params={'session': 'session-a'}).json()['requests'] == []
    assert client.post(f"/requests/{keep['requestId']}/withdraw", json=dict(body, extra=1)).status_code == 422
    decided = store.create(OWNER, complete_request())
    store.decide(OWNER, decision(decided, key='api-1'))
    assert client.post(f"/requests/{decided['requestId']}/withdraw", json=body).status_code == 409


def test_agent_context_drops_moot_asks_and_reports_each_withdrawal_once(tmp_path, monkeypatch):
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    monkeypatch.delenv('HERMES_KANBAN_TASK', raising=False)
    plugin = load_module('withdraw_hook_plugin', ROOT / '__init__.py')
    monkeypatch.setattr(plugin.core, 'home', lambda: tmp_path)
    monkeypatch.setattr(plugin.core, 'kanban_root', lambda: tmp_path)
    monkeypatch.setattr(plugin.core, 'server_owner', lambda sid: dict(OWNER, storedSessionId=sid))
    store = plugin.core.ActionStore()
    board(tmp_path, {'id': 't_1418b643', 'status': 'review', 'events': [('review_requested', time.time() - 200)]})
    ask = store.create(OWNER, complete_request(title='Review and approve card t_1418b643'), created_at=time.time() - 60)
    note = plugin.pre_llm_call(session_id='session-a', platform='desktop')['context']
    assert ask['requestId'] in note and 'withdraw' in note
    board(tmp_path, {'id': 't_1418b643', 'status': 'done', 'completed_at': int(time.time()),
                     'events': [('completed', int(time.time()))]})
    note = plugin.pre_llm_call(session_id='session-a', platform='desktop')['context']
    assert 'awaiting a user decision' not in note
    assert 'No longer needed' in note and 't_1418b643' in note
    assert plugin.pre_llm_call(session_id='session-a', platform='desktop') is None


# ---- extraction precision -----------------------------------------------------------------------------

def test_reply_declaring_nothing_for_the_user_yields_no_status_line_ask(tmp_path):
    store, ledger = env(tmp_path)
    out = core.process_reply(CLOSEOUT, 'session-a', ledger=ledger, action_store=store, action_owner=OWNER,
                             llm=lambda *a: json.dumps({'steps': [INCIDENT_STEP], 'resolved': []}))
    assert out['steps'] == []
    assert store.requests(OWNER) == []


def test_direct_asks_survive_a_no_action_declaration(tmp_path):
    store, ledger = env(tmp_path)
    reply = ("Nothing is currently assigned to you.\n\nCan you approve the DNS change for example.com?\n\n"
             "Nothing else for you after that. Say \"card it\" and I'll file the follow-up.")
    steps = [{'text': 'Approve the DNS change', 'kind': 'approve',
              'verbatim_span': 'Can you approve the DNS change for example.com?', 'options': []},
             {'text': 'Say card it', 'kind': 'approve',
              'verbatim_span': "Say \"card it\" and I'll file the follow-up.", 'options': []}]
    out = core.process_reply(reply, 'session-a', ledger=ledger, action_store=store, action_owner=OWNER,
                             llm=lambda *a: json.dumps({'steps': steps, 'resolved': []}))
    assert [s['text'] for s in out['steps']] == ['Approve the DNS change', 'Say card it']


def test_scoped_no_action_lines_do_not_hide_unmarked_asks(tmp_path):
    # Reviewer B9: "nothing for you on t_A" says nothing about the rest of the reply.
    cases = [
        ("Nothing is needed from you for the deploy.\n\nI need the new live key to finish the billing migration.",
         "I need the new live key to finish the billing migration.", "Send the new live key"),
        ("- t_A: nothing for you\n- t_B: blocked until you sign in to Vercel",
         "t_B: blocked until you sign in to Vercel", "Sign in to Vercel"),
        ("No action is required from you on the docs.\n\nThe only thing left is the sign-off on the pricing page.",
         "The only thing left is the sign-off on the pricing page.", "Sign off on the pricing page"),
    ]
    for i, (reply, span, text) in enumerate(cases):
        store, ledger = env(tmp_path / str(i))
        step = {'text': text, 'kind': 'do', 'verbatim_span': span, 'options': []}
        out = core.process_reply(reply, 'session-a', ledger=ledger, action_store=store, action_owner=OWNER,
                                 llm=lambda *a, step=step: json.dumps({'steps': [step], 'resolved': []}))
        assert [s['text'] for s in out['steps']] == [text], reply


def test_quoted_questions_are_not_asks_in_a_reply_that_needs_nothing(tmp_path):
    # Reviewer B10: someone else's quoted question is not a request of the user.
    store, ledger = env(tmp_path)
    span = 'The vendor asked "Can you confirm the region?" and I answered us-east-1.'
    reply = 'Nothing is currently assigned to you. ' + span
    step = {'text': 'Confirm the region', 'kind': 'input', 'verbatim_span': span, 'options': []}
    out = core.process_reply(reply, 'session-a', ledger=ledger, action_store=store, action_owner=OWNER,
                             llm=lambda *a: json.dumps({'steps': [step], 'resolved': []}))
    assert out['steps'] == []


def test_status_lines_still_extract_when_the_reply_makes_no_declaration(tmp_path):
    # Precision is scoped to replies that declare no user action; real asks phrased as statements survive.
    store, ledger = env(tmp_path)
    reply = "**One decision is still yours: whether Sam gets the drafted reply.** I'll send it once you decide."
    step = {'text': 'Decide whether Sam gets the drafted reply', 'kind': 'choose',
            'verbatim_span': 'One decision is still yours: whether Sam gets the drafted reply.', 'options': []}
    out = core.process_reply(reply, 'session-a', ledger=ledger, action_store=store, action_owner=OWNER,
                             llm=lambda *a: json.dumps({'steps': [step], 'resolved': []}))
    assert len(out['steps']) == 1


def test_plan_gate_does_not_hold_a_reply_that_says_nothing_is_needed(tmp_path, monkeypatch):
    # The final-reply plan gate extracts approvals too; a status line must not trigger a hold and nudge.
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    monkeypatch.delenv('HERMES_KANBAN_TASK', raising=False)
    plugin = load_module('withdraw_gate_plugin', ROOT / '__init__.py')
    monkeypatch.setattr(plugin.core, 'home', lambda: tmp_path)
    monkeypatch.setattr(plugin.core, 'server_owner', lambda sid: dict(OWNER, storedSessionId=sid))
    monkeypatch.setattr(plugin, 'aux_llm', lambda *a: json.dumps({'steps': [INCIDENT_STEP], 'resolved': []}))
    assert plugin.publication_buffer(session_id='session-a', platform='desktop', final_response=CLOSEOUT) is None
    asking = SPAN + ' Can you approve it?'
    step = dict(INCIDENT_STEP, verbatim_span='Can you approve it?')
    monkeypatch.setattr(plugin, 'aux_llm', lambda *a: json.dumps({'steps': [step], 'resolved': []}))
    assert plugin.publication_buffer(session_id='session-a', platform='desktop', final_response=asking)['hold'] is True


def test_extractor_prompt_says_status_lines_and_review_lanes_are_not_asks():
    assert 'in review' in core.SYSTEM and 'automated reviewer' in core.SYSTEM
