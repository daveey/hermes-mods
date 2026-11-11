"""Safety boundaries for reply-card withdrawal, with isolated real stores."""
import json
import sqlite3

import pytest
from test_social_reply_withdrawal import setup, inbox, URI, CARD
from test_withdrawal import core, OWNER, complete_request, decision


@pytest.mark.parametrize('change', ['decided', 'other-session', 'other-profile', 'other-gateway', 'bound-card'])
def test_scope_and_decision_safety(tmp_path, monkeypatch, change):
    store, ledger = setup(tmp_path, monkeypatch)
    inbox(tmp_path)
    owner = dict(OWNER)
    if change == 'other-session':
        owner['storedSessionId'] = 'another-chat'
    elif change == 'other-profile':
        owner['profile'] = 'another-profile'
    elif change == 'other-gateway':
        owner['gatewayId'] = 'another-gateway'
    payload = complete_request(title=f'Press Post on `{URI}`')
    payload['owner'] = owner
    if change == 'bound-card':
        payload['aliases'] = [dict(gatewayId=owner['gatewayId'], profile=owner['profile'],
                                   type='kanban', board='default', id='t_12345678')]
    r = store.create(owner, payload)
    if change == 'decided':
        store.decide(owner, decision(r))
    with sqlite3.connect(store.path) as c:
        before = c.execute('SELECT * FROM operations').fetchall()
    called_owner = owner if change in ('other-profile', 'other-gateway') else OWNER
    assert core.reconcile_moot(store, ledger, called_owner, boards=[]) == []
    assert store.get(owner, r['requestId'])['state'] != 'withdrawn'
    with sqlite3.connect(store.path) as c:
        assert c.execute('SELECT * FROM operations').fetchall() == before


@pytest.mark.parametrize('bad', ['absent', 'broken-db', 'legacy-schema', 'malformed-json', 'non-social', 'open-duplicate'])
def test_missing_or_ambiguous_evidence_keeps_ask(tmp_path, monkeypatch, bad):
    store, ledger = setup(tmp_path, monkeypatch)
    if bad != 'absent':
        path = inbox(tmp_path, kind='bsky-incoming' if bad == 'non-social' else 'bsky-reply-draft')
        if bad == 'broken-db':
            path.write_bytes(b'not a database')
        else:
            with sqlite3.connect(path) as c:
                if bad == 'legacy-schema':
                    c.execute('ALTER TABLE items RENAME COLUMN fields_json TO old_fields')
                elif bad == 'malformed-json':
                    c.execute("UPDATE items SET fields_json='not json'")
                elif bad == 'open-duplicate':
                    c.execute('INSERT INTO items VALUES (?,?,?,?,?)',
                              ('other-card', 'bsky-reply-draft', 'open', None, json.dumps({'uri': URI})))
    r = store.create(OWNER, complete_request(title=f'Press Post on `{URI}`'))
    assert core.reconcile_moot(store, ledger, OWNER, boards=[]) == []
    assert store.get(OWNER, r['requestId'])['state'] == 'open'
    if bad == 'absent':
        assert not (tmp_path / 'inbox').exists()


def test_legacy_step_mirror_is_retired_with_canonical_request(tmp_path, monkeypatch):
    store, ledger = setup(tmp_path, monkeypatch)
    path = inbox(tmp_path, state='open', outcome=None)
    text = f'Press Post on the reply-draft card (session {CARD}) to make the reply go live'
    extracted = {'text': text, 'kind': 'approve', 'verbatim_span': text, 'options': []}
    result = core.process_reply(text, OWNER['storedSessionId'], ledger=ledger, action_store=store,
                                action_owner=OWNER,
                                llm=lambda *a: json.dumps({'steps': [extracted], 'resolved': []}))
    step = result['steps'][0]
    assert core.reconcile_moot(store, ledger, OWNER, boards=[]) == []
    with sqlite3.connect(path) as c:
        c.execute("UPDATE items SET state='accepted', outcome='post'")
    assert core.reconcile_moot(store, ledger, OWNER, boards=[]) == []
    # Actual incident: the producer leaves accepted/post in the sidecar, but
    # archives the card session. Inbox.outcome derives archived from state.db.
    with sqlite3.connect(tmp_path / 'state.db') as c:
        c.execute('CREATE TABLE sessions (id TEXT, archived INTEGER)')
        c.execute('INSERT INTO sessions VALUES (?,1)', (CARD,))
    assert core.reconcile_moot(store, ledger, OWNER, boards=[])
    assert store.view(OWNER, step['requestId'])['state'] == 'withdrawn'
    _, mirror = ledger.find(step['id'], OWNER['storedSessionId'])
    assert mirror['status'] == 'resolved' and mirror['withdrawn'] is True
    assert mirror['resolution'].startswith('No longer needed: ')
