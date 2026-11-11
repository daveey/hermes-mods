"""Reply-card reconciliation never publishes, decides, or mutates the Inbox."""
import json
import sqlite3

import pytest

from test_withdrawal import core, env, OWNER, complete_request

URI = 'at://did:plc:example/app.bsky.feed.post/abc123'
URL = 'https://x.com/example/status/123456789'
CARD = '20261111_120000_example'


def inbox(tmp_path, *, kind='bsky-reply-draft', state='resolved', outcome='posted', fields=None):
    path = tmp_path / 'inbox' / 'inbox.db'
    path.parent.mkdir(exist_ok=True)
    with sqlite3.connect(path) as c:
        c.execute('CREATE TABLE items (session_id TEXT, type TEXT, state TEXT, outcome TEXT, fields_json TEXT)')
        c.execute('INSERT INTO items VALUES (?,?,?,?,?)',
                  (CARD, kind, state, outcome, json.dumps(fields if fields is not None else {'uri': URI})))
    return path


def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(core, 'home', lambda: tmp_path)
    monkeypatch.setattr(core, 'server_owner', lambda sid: dict(OWNER, storedSessionId=sid))
    monkeypatch.setattr(core, '_owner_chat_absent', lambda owner: False)
    monkeypatch.setattr(core, '_absent_owner_sessions', lambda *a: set())
    return env(tmp_path)


@pytest.mark.parametrize('kind,fields,target', [
    ('bsky-reply-draft', {'uri': URI}, URI),
    ('x-reply-draft', {'reply_url': URL, 'reply_id': '123456789'}, URL),
    ('bsky-reply-draft', {'uri': URI}, CARD),
])
@pytest.mark.parametrize('state,outcome', [('resolved', 'posted'), ('archived', 'archived'),
                                          ('resolved', 'skip'), ('resolved', 'skipped')])
def test_terminal_reply_retires_duplicate_without_deciding(tmp_path, monkeypatch, kind, fields, target, state, outcome):
    store, ledger = setup(tmp_path, monkeypatch)
    path = inbox(tmp_path, kind=kind, fields=fields, state=state, outcome=outcome)
    before = path.read_bytes()
    r = store.create(OWNER, complete_request(title=f'Press Post on reply draft `{target}`'))
    out = core.reconcile_moot(store, ledger, OWNER, boards=[])
    assert [v['requestId'] for v in out] == [r['requestId']]
    view = store.view(OWNER, r['requestId'])
    assert view['state'] == 'withdrawn'
    assert outcome in view['withdrawal']['reason']
    assert CARD in view['withdrawal']['evidence']
    assert 'resolution' not in view
    assert core.reconcile_moot(store, ledger, OWNER, boards=[]) == []
    assert path.read_bytes() == before
    with sqlite3.connect(store.path) as c:
        assert c.execute('SELECT count(*) FROM operations').fetchone()[0] == 0


@pytest.mark.parametrize('state,outcome', [('open', None), ('accepted', 'post'), ('accepted', 'edit'),
                                          ('resolved', 'failed'), ('resolved', 'post')])
def test_not_terminal_publication_evidence(tmp_path, monkeypatch, state, outcome):
    store, ledger = setup(tmp_path, monkeypatch)
    inbox(tmp_path, state=state, outcome=outcome)
    r = store.create(OWNER, complete_request(title=f'Press Post on `{URI}`'))
    assert core.reconcile_moot(store, ledger, OWNER, boards=[]) == []
    assert store.get(OWNER, r['requestId'])['state'] == 'open'


@pytest.mark.parametrize('title', [f'Press Post on `{URI}more`', f'Press Post on `{CARD}-more`',
                                    'Press Post on the reply draft', f'Investigate failures for `{URI}`',
                                    f'Review evidence of publication failure for `{URI}`',
                                    f'Approve infrastructure changes for `{URI}`'])
def test_unidentified_or_non_duplicate_asks_stay(tmp_path, monkeypatch, title):
    store, ledger = setup(tmp_path, monkeypatch)
    inbox(tmp_path)
    r = store.create(OWNER, complete_request(title=title))
    assert core.reconcile_moot(store, ledger, OWNER, boards=[]) == []
    assert store.get(OWNER, r['requestId'])['state'] == 'open'
