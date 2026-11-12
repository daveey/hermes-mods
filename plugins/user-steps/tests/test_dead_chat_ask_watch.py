"""stale_watch: a blocked user_terminal ask whose action names a chat that has had no turn since the block opens one
agent-owned Kanban card (self-heal kanban-53e6157b2f; never an Inbox card).

Observed: card t_01a1cbfae450bef1 blocked at 07:09 PT with "From owner chat 20261111_032644_40d5be, run ...". That chat
was reaped at 02:01 PT and got no turn from the notice; the card sat blocked until David asked why at 09:11 PT.
"""
import importlib.util
import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location('dca_' + name, ROOT / (name + '.py'))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


core, sw = load('core'), load('stale_watch')
DEAD = '20261111_032644_40d5be'
LIVE = '20261102_172718_5fb607'
BLOCKED_AT = 1_800_000_000.0
ACTION = f"From owner chat {DEAD}, run the single ssh command through run_in_user_terminal. Unblock = resume."


def board(tmp_path, *, action=ACTION, interaction='user_terminal', status='blocked'):
    db = tmp_path / 'kanban.db'
    with sqlite3.connect(db) as c:
        c.execute('CREATE TABLE tasks (id TEXT, title TEXT, status TEXT, human_request TEXT)')
        c.execute('CREATE TABLE task_events (task_id TEXT, kind TEXT, created_at REAL)')
        c.execute('INSERT INTO tasks VALUES (?,?,?,?)', ('t_01a1cbfae450bef1', 'Ask Eve activation', status,
                                                         json.dumps({'interaction': interaction, 'action': action})))
        c.execute("INSERT INTO task_events VALUES ('t_01a1cbfae450bef1','blocked',?)", (BLOCKED_AT,))
    return [('default', db)]


def turns(**last):
    return lambda sid: (sid in last, last.get(sid))


def test_dead_named_chat_opens_one_card_after_30_minutes(tmp_path):
    found = sw.dead_chat_asks(core, now=BLOCKED_AT + 1801, boards=board(tmp_path),
                              turn=turns(**{DEAD: BLOCKED_AT - 3600}))
    assert [d['session'] for d in found] == [DEAD]
    c = sw.dead_chat_ask_card(found[0])
    assert c['idempotency_key'] == f'user-steps-dead-chat-ask:t_01a1cbfae450bef1:{DEAD}'
    assert 'owner-terminal flow' in c['body'] and 'Inbox' not in c['title']


def test_not_before_the_grace_period(tmp_path):
    assert sw.dead_chat_asks(core, now=BLOCKED_AT + 600, boards=board(tmp_path), turn=turns()) == []


def test_a_chat_that_took_a_turn_after_the_block_is_alive(tmp_path):
    assert sw.dead_chat_asks(core, now=BLOCKED_AT + 7200, boards=board(tmp_path),
                             turn=turns(**{DEAD: BLOCKED_AT + 60})) == []


def test_unknown_chat_counts_as_dead(tmp_path):
    found = sw.dead_chat_asks(core, now=BLOCKED_AT + 7200, boards=board(tmp_path), turn=turns())
    assert found and found[0]['found'] is False


def test_canonical_action_and_other_interactions_are_ignored(tmp_path):
    canon = "From your own Hermes chat open the owner-terminal flow for task t_01a1cbfae450bef1. Unblock = resume."
    assert sw.dead_chat_asks(core, now=BLOCKED_AT + 7200, boards=board(tmp_path, action=canon), turn=turns()) == []
    (tmp_path / 'b').mkdir()
    assert sw.dead_chat_asks(core, now=BLOCKED_AT + 7200, boards=board(tmp_path / 'b', interaction='approval'),
                             turn=turns()) == []
    (tmp_path / 'c').mkdir()
    assert sw.dead_chat_asks(core, now=BLOCKED_AT + 7200, boards=board(tmp_path / 'c', status='done'),
                             turn=turns()) == []


def test_last_turn_reads_profile_stores(tmp_path):
    db = tmp_path / 'state.db'
    with sqlite3.connect(db) as c:
        c.execute('CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT, timestamp REAL)')
        c.execute('INSERT INTO messages (session_id, timestamp) VALUES (?, ?)', (LIVE, 5.0))
    assert sw.last_turn(core, LIVE, dbs=[db]) == (True, 5.0)
    assert sw.last_turn(core, DEAD, dbs=[db]) == (False, None)


def test_run_files_it_only_when_enabled(tmp_path, monkeypatch):
    monkeypatch.setattr(core, 'kanban_boards', lambda root=None: board(tmp_path))
    monkeypatch.setattr(sw, 'last_turn', lambda core, sid, dbs=None: (False, None))

    class Chat:
        answered_open = duplicate_open = cross_session_duplicates = staticmethod(lambda *a, **k: [])

    monkeypatch.setattr(sw, 'failed_decisions', lambda *a, **k: [])
    monkeypatch.setattr(sw, 'undelivered_decisions', lambda *a, **k: [])
    made = []
    assert sw.run(core, Chat, None, now=BLOCKED_AT + 7200, create=made.append) == []
    ids = sw.run(core, Chat, None, now=BLOCKED_AT + 7200, create=made.append, dead_chats=True)
    assert ids == [f'dead-chat-ask:t_01a1cbfae450bef1:{DEAD}'] and len(made) == 1
