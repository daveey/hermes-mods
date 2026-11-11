"""An amended ask already quoted by its chat must not be requested again."""
import sqlite3
from contextlib import closing

import pytest
from test_ask_relay import core, plugin, _waker

ASK = {'action': 'Choose the blue option. Unblock = apply blue; archive = drop it.',
       'why_human': 'Only the owner can choose.', 'interaction': 'reply',
       'scope': 'Fixture choice', 'evidence': 'Two choices remain.'}


@pytest.mark.parametrize('entry', ['tick', 'pre'])
@pytest.mark.parametrize('previously_covered', [False, True])
@pytest.mark.parametrize('working', [False, True], ids=['closeout', 'relay'])
@pytest.mark.parametrize('quote_at,expected', [(901, 0), (None, 1), (899, 1)],
                         ids=['fresh', 'unquoted', 'stale'])
def test_refiled_ask(tmp_path, monkeypatch, working, quote_at, expected, previously_covered, entry):
    led, calls, tick = _waker(tmp_path, monkeypatch)
    db = tmp_path / 'state.db'
    with closing(sqlite3.connect(db)) as con, con:
        con.execute('CREATE TABLE messages (session_id TEXT, role TEXT, content TEXT, timestamp REAL, '
                    'display_kind TEXT, finish_reason TEXT, tool_calls TEXT, active INTEGER)')
        if quote_at is not None:
            con.execute('INSERT INTO messages VALUES (?,?,?,?,NULL,?,NULL,1)',
                        ('chat', 'assistant', ASK['action'], quote_at, 'stop'))
    monkeypatch.setattr(plugin, 'state_dbs', lambda: [db])
    monkeypatch.setattr(plugin, '_session_source', lambda _: 'desktop')
    monkeypatch.setattr(plugin, '_session_gone', lambda _: False)
    monkeypatch.setattr(plugin, '_moved_owner', lambda _: None)
    card = {'status': 'blocked', 'block_kind': 'needs_input', 'human_request': ASK, 'blocked_at': 900}
    old = {**card, 'human_request': {**ASK, 'action': 'Old choice'}}
    cards = {'t_ask': card}
    if working:
        cards['t_work'] = {'status': 'running'}
    led.write('chat', batch=list(cards), covered=['t_ask'] if previously_covered else [],
              relayed=[core.ask_premise('t_ask', old)])
    if entry == 'pre':
        monkeypatch.setattr(plugin, '_eligible', lambda *a, **k: True)
        monkeypatch.setattr(plugin, 'read_cards', lambda _: cards)
        assert bool(plugin.pre_llm_call(session_id='chat')) is bool(expected)
        return
    for now in (1500, 1560, 1800):
        tick(cards, now)
        # Complete any successful injected turn, as production does.
        if calls:
            core.record_turn_end(led, 'chat', list(cards), lambda _: cards, now=now + 1)
    assert len(calls) == expected
    monkeypatch.setattr(plugin, '_eligible', lambda *a, **k: True)
    monkeypatch.setattr(plugin, 'read_cards', lambda _: cards)
    assert plugin.pre_llm_call(session_id='chat') is None


@pytest.mark.parametrize('role,kind,finish,at,session,expected', [
    ('assistant', 'final', 'stop', 901, 'chat', True),
    ('assistant', None, None, 901, 'chat', False),
    ('user', 'kanban_notice', None, 901, 'chat', False),
    ('tool', None, None, 901, 'chat', False),
    ('assistant', 'kanban_notice', 'stop', 901, 'chat', False),
    ('assistant', 'final', 'error', 901, 'chat', False),
    ('assistant', 'final', 'stop', 900, 'chat', False),
    ('assistant', 'final', 'stop', 901, 'other', False),
])
def test_quote_proof_boundaries(tmp_path, role, kind, finish, at, session, expected):
    db = tmp_path / 'transcript.db'
    with closing(sqlite3.connect(db)) as con, con:
        con.execute('CREATE TABLE messages (session_id, role, content, timestamp, display_kind, finish_reason)')
        con.execute('INSERT INTO messages VALUES (?,?,?,?,?,?)',
                    (session, role, 'Already asked: ' + ASK['action'].upper().replace(' ', '\n'), at, kind, finish))
    card = {'status': 'blocked', 'block_kind': 'needs_input', 'human_request': ASK, 'blocked_at': 900}
    assert core.quoted_ask([db], ['chat'], card) is expected
