import importlib.util
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location('sw_' + name, ROOT / (name + '.py'))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


core, chat, sw = load('core'), load('chat'), load('stale_watch')
OWNER = {'gatewayId': 'test', 'profile': 'default', 'storedSessionId': 'session'}
Q = 'Do you mean open a pull request implementing these strategy changes, or something else?'
NOW = 1_800_000_000.0


def make(tmp_path, reply_age):
    st = core.ActionStore(tmp_path / 'actions.sqlite3')
    r = st.migrate_step(OWNER, dict(id='us_test', text=Q, paragraph=Q, kind='choose', session='session',
                                    options=['open a pull request', 'something else']))
    db = tmp_path / 'state.db'
    with sqlite3.connect(db) as c:
        c.execute('CREATE TABLE messages (id INTEGER PRIMARY KEY,session_id TEXT,role TEXT,content TEXT,active INTEGER,'
                  'display_kind TEXT,tool_calls TEXT,timestamp REAL)')
        c.executemany('INSERT INTO messages VALUES (?,?,?,?,1,NULL,NULL,?)', [
            (1, 'session', 'assistant', core.directive(['us_test'], 'session', Q), NOW - reply_age - 5),
            (2, 'session', 'user', 'make the changes you want', NOW - reply_age),
            (3, 'session', 'assistant', 'Updated PLAN.md locally.', NOW - reply_age + 5)])
    return st, r, db


class Board:
    """Kanban stand-in with the real idempotency contract: one card per key."""
    def __init__(self):
        self.cards = {}

    def __call__(self, c):
        self.cards.setdefault(c['idempotency_key'], c)


def go(st, db, board, **kw):
    return sw.run(core, chat, st, db, now=NOW, create=board, **kw)


def test_two_hour_old_reply_opens_exactly_one_card_and_rerun_adds_none(tmp_path):
    st, r, db = make(tmp_path, 7200)
    board = Board()
    assert go(st, db, board) == [r['requestId']]
    assert go(st, db, board) == [r['requestId']]  # rerun resubmits the same key
    assert list(board.cards) == ['user-steps-stale:' + r['requestId']]
    c = board.cards['user-steps-stale:' + r['requestId']]
    assert r['requestId'] in c['body'] and 'Session: session' in c['body'] and 'make the changes you want' in c['body']
    assert Q in c['body'] and f"/requests/{r['requestId']}/reconcile-chat" in c['body']


def test_recent_reply_opens_no_card(tmp_path):
    st, r, db = make(tmp_path, 600)
    board = Board()
    assert go(st, db, board) == [] and not board.cards


def test_retired_request_opens_no_card(tmp_path):
    st, r, db = make(tmp_path, 7200)
    chat.reconcile(core, st, OWNER, lambda s, u: '{"outcome":"superseded","quote":"make the changes you want"}', db)
    board = Board()
    assert go(st, db, board) == [] and not board.cards


def test_cli_uses_kanban_create_with_idempotency_key(tmp_path, monkeypatch):
    st, r, db = make(tmp_path, 7200)
    seen = []
    monkeypatch.setattr(sw.subprocess, 'run', lambda cmd, **k: seen.append((cmd, k['input'])) or
                        type('P', (), {'returncode': 0, 'stdout': '{}', 'stderr': ''})())
    sw.run(core, chat, st, db, now=NOW)
    cmd, body = seen[0]
    assert cmd[:3] == ['hermes', 'kanban', 'create'] and 'user-steps-stale:' + r['requestId'] in cmd
    assert '--assignee' in cmd and r['requestId'] in body


# ---- duplicate-open detection ----------------------------------------------------------------------------

def _approval(title):
    import uuid
    return dict(schemaVersion=2, owner=OWNER.copy(), requestId=str(uuid.uuid4()), revision=1, kind='approval',
                title=title, scope=dict(markdown='Change example.py.', complete=True, sourceRefs=[]), limits=[],
                exclusions=[], outcomes=dict(approve='Do it', revise='Block', decline='Cancel'),
                choices=[dict(id=e, label=l, effect=e) for e, l in
                         zip(('approve', 'revise', 'decline'), ('Approve', 'Revise', 'Decline'))],
                aliases=[], state='open')


def test_duplicate_pair_opens_one_card_and_rerun_adds_none(tmp_path):
    st = core.ActionStore(tmp_path / 'actions.sqlite3')
    a = st.create(OWNER, _approval('Approve fix A for the build?'))
    b = st.create(OWNER, _approval('Approve fix A for the build?!'))
    board = Board()
    go(st, tmp_path / 'missing.db', board)
    go(st, tmp_path / 'missing.db', board)
    assert len(board.cards) == 1
    key, c = next(iter(board.cards.items()))
    assert key.startswith('user-steps:dup:') and key.endswith(':session')
    assert a['requestId'] in c['body'] and b['requestId'] in c['body']
    assert chat.stale_counts(core, st, tmp_path / 'missing.db')['duplicateOpen'] == 1


def test_distinct_asks_open_no_dup_card(tmp_path):
    st = core.ActionStore(tmp_path / 'actions.sqlite3')
    st.create(OWNER, _approval('Approve fix A for the build?'))
    st.create(OWNER, _approval('Approve fix B for the build?'))
    st.create(OWNER, _approval('Approve?'))
    st.create(OWNER, _approval('Approve?!'))  # too short to identify an ask: kept, as same_open_ask does
    board = Board()
    go(st, tmp_path / 'missing.db', board)
    assert board.cards == {}
    assert chat.stale_counts(core, st, tmp_path / 'missing.db')['duplicateOpen'] == 0


def test_decided_or_withdrawn_twin_is_not_a_duplicate(tmp_path):
    st = core.ActionStore(tmp_path / 'actions.sqlite3')
    a = st.create(OWNER, _approval('Approve fix A for the build?'))
    st.decide(OWNER, dict(requestId=a['requestId'], expectedRevision=1, choiceId='approve',
                          idempotencyKey='k1', revisionText=None))
    st.create(OWNER, _approval('Approve fix A for the build?'))
    assert chat.duplicate_open(core, st) == []
