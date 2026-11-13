"""A chat that owns an open reply-draft card must not also ask "press Post" (defect kanban-354519516c).

Incident (session 20261110_234556_897cad): after David picked an auto-drafted Bluesky reply, the agent made
the pinned bsky-reply-draft card and ALSO wrote "Press Post on that card", which the extractor turned into
an open step (request 673558f9). The card is the ask; the extra step kept the chat red for a day, through
two "why blocked" asks. The step title named neither the card's session id nor the target URI, so the
title-based retirement (_resolved_reply) never matched it even after the card resolved.

The Inbox now records the posting chat on each card (``items.origin_session``). These tests pin:
  * publication guard: a Post/approval-type ask from the owning chat is not published (extraction and
    the action_request tool), while choices, inputs and unrelated asks still are;
  * retirement by ownership: an older Post step of the owning chat is withdrawn once the card is posted,
    even when its title names no card.
"""
import json
import sqlite3

import pytest

from test_review_regressions import plugin_at, producer_payload
from test_social_reply_withdrawal import setup, URI
from test_withdrawal import core, OWNER, complete_request

SID = OWNER['storedSessionId']
CARD = '20261110_235012_card01'
POST_REPLY = ("Draft card created for your reply to @someone. Press Post on that card to make the reply go "
              "live.")
POST_STEP = {'text': 'Press Post on the Bluesky reply-draft card', 'kind': 'approve',
             'verbatim_span': 'Press Post on that card to make the reply go live.', 'options': []}


def owned_inbox(tmp_path, *, origin=SID, state='open', outcome=None, kind='bsky-reply-draft', with_column=True):
    path = tmp_path / 'inbox' / 'inbox.db'
    path.parent.mkdir(exist_ok=True)
    with sqlite3.connect(path) as c:
        cols = 'session_id TEXT, type TEXT, state TEXT, outcome TEXT, fields_json TEXT'
        c.execute(f"CREATE TABLE items ({cols}{', origin_session TEXT' if with_column else ''})")
        row = (CARD, kind, state, outcome, json.dumps({'uri': URI}))
        c.execute(f"INSERT INTO items VALUES ({','.join('?' * (6 if with_column else 5))})",
                  row + ((origin,) if with_column else ()))
    return path


def llm_for(step):
    return lambda *a: json.dumps({'steps': [step], 'resolved': []})


def reply(store, ledger, text=POST_REPLY, step=POST_STEP):
    return core.process_reply(text, SID, ledger=ledger, action_store=store, action_owner=OWNER,
                              llm=llm_for(step))


def open_requests(store):
    return [r for r in store.requests(OWNER) if r['state'] in ('open', 'needs_context')]


@pytest.mark.parametrize('state,outcome', [('open', None), ('accepted', 'post')])
def test_owning_chat_post_step_is_not_published(tmp_path, monkeypatch, state, outcome):
    store, ledger = setup(tmp_path, monkeypatch)
    owned_inbox(tmp_path, state=state, outcome=outcome)
    out = reply(store, ledger)
    assert out['steps'] == []
    assert open_requests(store) == []
    log = ledger.read(SID).get('reply_draft_asks') or []
    assert [e['card'] for e in log] == [CARD]


@pytest.mark.parametrize('variant', ['other-chat', 'posted', 'no-origin', 'legacy-schema', 'no-inbox'])
def test_post_step_still_published_without_an_open_owned_card(tmp_path, monkeypatch, variant):
    store, ledger = setup(tmp_path, monkeypatch)
    if variant == 'other-chat':
        owned_inbox(tmp_path, origin='another-chat')
    elif variant == 'posted':
        owned_inbox(tmp_path, state='resolved', outcome='posted')
    elif variant == 'no-origin':
        owned_inbox(tmp_path, origin=None)
    elif variant == 'legacy-schema':
        owned_inbox(tmp_path, with_column=False)
    out = reply(store, ledger)
    assert len(out['steps']) == 1
    assert len(open_requests(store)) == 1


@pytest.mark.parametrize('step', [
    {'text': 'Which tone should the next reply take?', 'kind': 'choose',
     'verbatim_span': 'Which tone should the next reply take: dry or warm?', 'options': ['Dry', 'Warm']},
    {'text': 'Send the paper link you mentioned', 'kind': 'input',
     'verbatim_span': 'Send me the paper link you mentioned.', 'options': []},
    {'text': 'Approve the Hetzner spend for the GPU box', 'kind': 'approve',
     'verbatim_span': 'Approve the Hetzner spend for the GPU box?', 'options': []},
])
def test_other_asks_from_owning_chat_still_publish(tmp_path, monkeypatch, step):
    store, ledger = setup(tmp_path, monkeypatch)
    owned_inbox(tmp_path)
    out = reply(store, ledger, text=POST_REPLY + ' ' + step['verbatim_span'], step=step)
    assert len(out['steps']) == 1


def test_action_request_post_approval_refused_with_hint(tmp_path, monkeypatch):
    plugin = plugin_at(tmp_path, monkeypatch)
    owned_inbox(tmp_path)
    payload = producer_payload()
    payload['title'] = 'Approve posting the Bluesky reply draft'
    out = json.loads(plugin.publish_action_request({'request': payload}, session_id=SID))
    assert 'error' in out, out
    assert CARD in out['error'] and 'ready' in out['error']
    assert plugin.core.ActionStore().requests(OWNER) == []
    other = producer_payload()
    other['title'] = 'Approve infrastructure change to example.py'
    assert 'error' not in json.loads(plugin.publish_action_request({'request': other}, session_id=SID))


def test_incident_replay_untitled_post_step_retires_when_owned_card_posts(tmp_path, monkeypatch):
    store, ledger = setup(tmp_path, monkeypatch)
    path = owned_inbox(tmp_path)
    r = store.create(OWNER, complete_request(title='Press Post on that card'))
    assert core.reconcile_moot(store, ledger, OWNER, boards=[]) == []  # card still open: keep
    with sqlite3.connect(path) as c:
        c.execute("UPDATE items SET state='resolved', outcome='posted'")
    out = core.reconcile_moot(store, ledger, OWNER, boards=[])
    assert [v['requestId'] for v in out] == [r['requestId']]
    view = store.view(OWNER, r['requestId'])
    assert view['state'] == 'withdrawn' and CARD in view['withdrawal']['evidence']


@pytest.mark.parametrize('kind,text,held', [
    ('approve', 'Press **Post** on that card to make it live', True),
    ('do', 'Press Post on the reply-draft card', True),
    ('approve', 'Approve posting the reply', True),
    ('approve', 'Confirm the Bluesky reply draft', True),
    ('approve', 'Review and approve the draft card', True),
    ('choose', 'Press Post or Skip on the card?', False),
    ('input', 'Press Post after you paste the link', False),
    ('approve', 'Approve the Hetzner spend for the GPU box', False),
    ('do', 'Post a new blog entry about the release', False),
])
def test_post_ask_classification(tmp_path, monkeypatch, kind, text, held):
    setup(tmp_path, monkeypatch)
    owned_inbox(tmp_path)
    found = core.reply_draft_holds(SID, kind, text)
    assert (found is not None and found['session_id'] == CARD) is held


def test_untitled_post_step_of_other_chat_is_kept(tmp_path, monkeypatch):
    store, ledger = setup(tmp_path, monkeypatch)
    owned_inbox(tmp_path, origin='another-chat', state='resolved', outcome='posted')
    r = store.create(OWNER, complete_request(title='Press Post on that card'))
    assert core.reconcile_moot(store, ledger, OWNER, boards=[]) == []
    assert store.get(OWNER, r['requestId'])['state'] == 'open'
