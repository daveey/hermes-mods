"""A busy chat that told the user a remote card's ask itself must end the relay, and the failure episode with it.

Live failure: the hub/worker replica rows carry no ``blocked_at`` (no task events), so the transcript proof never
ran for them. A chat that was always mid-turn answered every relay ``busy``, had already quoted the ask, and the
watchdog filed it as undelivered, with an age inherited from an unrelated failure days earlier.
"""
import sqlite3
from contextlib import closing

import pytest
from test_relay_retry import core, plugin

ASK = {'action': 'Choose whether to accept the bounded investigation. Unblock = resume; archive = stop.',
       'why_human': 'Changes frozen acceptance.', 'interaction': 'reply',
       'scope': 'One card', 'evidence': 'History was not recorded.'}


def _remote(**over):
    return {'status': 'blocked', 'block_kind': 'needs_input', 'human_request': ASK, 'remote': True, 'stale': False,
            'via': 'origin', 'park': {'outcome': 'blocked', 'ended_at': 900, 'reason': 'Decide'}, **over}


@pytest.mark.parametrize('card,expected', [
    ({'blocked_at': 900}, 900.0),
    ({'blocked_at': '900'}, 900.0),
    (_remote(), 900.0),
    (_remote(blocked_at=950), 950.0),  # a row that does carry the event time keeps it
    (_remote(park={'outcome': 'scheduled', 'ended_at': 900}), None),  # an older park says nothing about this block
    (_remote(status='running'), None),  # no longer blocked: the park is history
    (_remote(park={'outcome': 'blocked'}), None),
    (_remote(park={'outcome': 'blocked', 'ended_at': 'soon'}), None),
    (_remote(park='blocked'), None),
    ({'status': 'blocked'}, None),
    (None, None),
])
def test_blocked_since(card, expected):
    assert core.blocked_since(card) == expected


def _transcript(tmp_path, at):
    db = tmp_path / 'state.db'
    with closing(sqlite3.connect(db)) as con, con:
        con.execute('CREATE TABLE messages (session_id TEXT, role TEXT, content TEXT, timestamp REAL, '
                    'display_kind TEXT, finish_reason TEXT, tool_calls TEXT, active INTEGER)')
        if at is not None:
            con.execute('INSERT INTO messages VALUES (?,?,?,?,NULL,?,NULL,1)',
                        ('chat', 'assistant', 'One decision for you:\n> ' + ASK['action'], at, 'stop'))
    return db


@pytest.mark.parametrize('quote_at,expected', [(901, True), (899, False), (None, False)],
                         ids=['quoted-after-block', 'quoted-before-block', 'never-quoted'])
def test_remote_ask_quote_is_proof(tmp_path, quote_at, expected):
    assert core.quoted_ask([_transcript(tmp_path, quote_at)], ['chat'], _remote()) is expected


def test_remote_ask_age_is_known():
    assert core.ask_age_s(_remote(), now=4500) == 3600
    assert core.ask_age_s(_remote(park=None, updated_at=1000), now=4500) == 3500
    assert core.ask_age_s(_remote(park=None), now=4500) is None


def _busy_chat(tmp_path, monkeypatch, quote_at):
    led = core.Ledger(tmp_path / 'ledger.db')
    monkeypatch.setattr(plugin, 'ledger', lambda: led)
    monkeypatch.setattr(plugin, '_lineage', lambda sid: [sid])
    monkeypatch.setattr(plugin, '_moved_owner', lambda sid: None, raising=False)
    monkeypatch.setattr(plugin, '_session_source', lambda _: 'desktop')
    monkeypatch.setattr(plugin, '_session_gone', lambda _: False)
    db = _transcript(tmp_path, quote_at)
    monkeypatch.setattr(plugin, 'state_dbs', lambda: [db])
    cards = {'hub/default/default/t_ask': _remote(), 'work': {'status': 'running'}}
    led.write('chat', batch=list(cards), open=list(cards))
    calls = []

    def wake(*args, **kwargs):
        calls.append(args)
        return 'busy'

    def tick(now):
        return plugin.wake_tick(now=now, wake=wake, unseen={}, last_user=lambda _: 0, read=lambda _: cards)
    return led, calls, tick, cards, db


def test_busy_chat_that_quoted_the_remote_ask_stops_relaying_and_ends_the_episode(tmp_path, monkeypatch):
    led, calls, tick, cards, db = _busy_chat(tmp_path, monkeypatch, None)
    tick(1000)
    tick(1030)
    assert len(calls) == 2  # untold: the relay keeps trying a busy chat
    assert led.get('chat')['failure_since'] == 1000
    with closing(sqlite3.connect(db)) as con, con:  # the chat's own turn tells the user, quoting the ask
        con.execute('INSERT INTO messages VALUES (?,?,?,?,NULL,?,NULL,1)',
                    ('chat', 'assistant', '> ' + ASK['action'], 1040, 'stop'))
    for now in (1060, 1090, 5000):
        tick(now)
    assert len(calls) == 2
    row = led.get('chat')
    assert 'hub/default/default/t_ask' in row['covered']
    assert core.ask_relay_key(row, cards) is None
    assert row['failure_since'] is None
    plugin._state['mono_seen'] = {}
    posted = []
    args = dict(unseen={}, force=True, post=lambda *a: posted.append(a), read=lambda _: cards)
    plugin.watchdog_tick(now=5001, mono=1, **args)
    plugin.watchdog_tick(now=9000, mono=4000, **args)
    assert not [p for p in posted if 'ask' in str(p).lower()]


def test_new_ask_after_a_finished_episode_starts_its_own_clock(tmp_path, monkeypatch):
    led, calls, tick, cards, db = _busy_chat(tmp_path, monkeypatch, 950)
    led.write('chat', failure_since=100.0)  # an old episode whose ask the chat has since told
    tick(1000)
    assert calls == [] and led.get('chat')['failure_since'] is None
    cards['worker-4/default/default/t_new'] = _remote(
        human_request={**ASK, 'action': 'Reply A or B. Unblock = continue; archive = drop.'},
        park={'outcome': 'blocked', 'ended_at': 2000}, via='shepherd')
    led.write('chat', batch=list(cards), open=list(cards))
    tick(2100)
    assert len(calls) == 1
    assert led.get('chat')['failure_since'] == 2100  # not 100: the watchdog ages this ask from its own failure


def test_untold_ask_keeps_its_failure_clock(tmp_path, monkeypatch):
    led, calls, tick, _, _ = _busy_chat(tmp_path, monkeypatch, None)
    for now in (1000, 1030, 1060):
        tick(now)
    assert len(calls) == 3
    assert led.get('chat')['failure_since'] == 1000
