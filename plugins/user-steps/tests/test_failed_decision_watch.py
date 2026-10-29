"""stale_watch: a delivered decision whose admitted turn failed before any tool ran, and that nothing continued,
opens one agent-owned Kanban card (self-heal desktop-3a6213ce74).

The decision tile closes at admission, so a turn a backend restart killed left David with nothing to press.
"""
import importlib.util
import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location('fdw_' + name, ROOT / (name + '.py'))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


core, chat, sw = load('core'), load('chat'), load('stale_watch')
NOW = 1_800_000_000.0
SESSION = 'session'


class Board:
    def __init__(self):
        self.cards = {}

    def __call__(self, c):
        self.cards.setdefault(c['idempotency_key'], c)


def make(tmp_path, rows, *, effect='approve', state='delivered', nested=True):
    st = core.ActionStore(tmp_path / 'actions.sqlite3')
    admission = {'status': 'accepted', 'stored_session_id': SESSION, 'user_row_id': 10, 'operation_id': 'op-1'}
    receipt = {'run': {'admission': admission}} if nested else {'admission': admission}
    body = {'operationId': 'op-1', 'dispatchState': state, 'intent': {'effect': effect, 'requestId': 'req-1'},
            'receipt': receipt, 'owner': {'storedSessionId': SESSION}}
    with st._tx() as c:
        c.execute('INSERT INTO operations (id,namespace,session,request,revision,idem,fingerprint,body) '
                  'VALUES (?,?,?,?,?,?,?,?)', ('op-1', '["g","default"]', SESSION, 'req-1', 1, 'k', 'f',
                                              json.dumps(body)))
    db = tmp_path / 'state.db'
    with sqlite3.connect(db) as c:
        c.execute('CREATE TABLE messages (id INTEGER PRIMARY KEY,session_id TEXT,role TEXT,content TEXT,active INTEGER,'
                  'display_kind TEXT,tool_calls TEXT,timestamp REAL)')
        c.executemany('INSERT INTO messages VALUES (?,?,?,?,1,?,?,?)',
                      [(i, SESSION, role, 'x', kind, calls, ts) for i, role, kind, calls, ts in rows])
    return st, db


ADMITTED = (10, 'user', None, None, NOW - 3600)
FAILED = (11, 'assistant', 'failed_turn', None, NOW - 3590)


def go(st, db, board):
    return sw.run(core, chat, st, db, now=NOW, create=board)


def test_decision_reopens_when_admitted_turn_fails_without_tools(tmp_path):
    st, db = make(tmp_path, [ADMITTED, FAILED])
    board = Board()
    assert 'failed-decision:op-1' in go(st, db, board)
    assert 'failed-decision:op-1' in go(st, db, board)  # rerun resubmits the same key
    card = board.cards['user-steps-failed-decision:op-1']
    assert 'op-1' in card['body'] and 'Session: session' in card['body'] and card['assignee'] == 'default'


def test_flat_receipt_shape_is_read_too(tmp_path):
    st, db = make(tmp_path, [ADMITTED, FAILED], nested=False)
    assert 'failed-decision:op-1' in go(st, db, Board())


def test_no_card_when_the_turn_was_continued_or_ran_tools_or_is_recent(tmp_path):
    cases = {
        'continued': [ADMITTED, FAILED, (12, 'user', 'auto_continue', None, NOW - 3500)],
        'tools': [ADMITTED, (11, 'assistant', None, '[{"id":"c"}]', NOW - 3595), (12, 'tool', None, None, NOW - 3594),
                  (13, 'assistant', 'failed_turn', None, NOW - 3590)],
        'recent': [ADMITTED, (11, 'assistant', 'failed_turn', None, NOW - 60)],
        'succeeded': [ADMITTED, (11, 'assistant', None, None, NOW - 3590)],
    }
    for name, rows in cases.items():
        sub = tmp_path / name
        sub.mkdir()
        st, db = make(sub, rows)
        assert not [i for i in go(st, db, Board()) if i.startswith('failed-decision:')], name


def test_decline_and_undelivered_operations_are_ignored(tmp_path):
    for name, kw in {'decline': {'effect': 'decline'}, 'pending': {'state': 'failed'}}.items():
        sub = tmp_path / name
        sub.mkdir()
        st, db = make(sub, [ADMITTED, FAILED], **kw)
        assert not [i for i in go(st, db, Board()) if i.startswith('failed-decision:')], name
