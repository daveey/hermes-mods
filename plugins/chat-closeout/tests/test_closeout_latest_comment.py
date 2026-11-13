"""A remote card with no handoff shows its latest comment in the close-out brief (self-heal agent-a679e846ca).

A close-out brief listed an archived remote incident card as `handoff: (no handoff)`. The card's latest comment held
the user's decision that closed it ("Close scoped": the Gmail/Astra proof withdrawn), so the brief gave the close-out
turn nothing to cite, and the coach's report_only judge failed the honest reply twice. Remote rows (origin, shepherd,
shadow) carry no comments; the hub replica's task record does (`last_comments`, newest last).
"""
import json
import sqlite3
import time

from test_closeout import plugin, core

DECISION = ("Close scoped: Gmail delivery and Astra attribution proof is withdrawn; archive the incident without it. "
            + "x" * 400)


def _replica(path, rows):
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE replica (gw TEXT, coll TEXT, key TEXT, version INTEGER, seq INTEGER, data TEXT, '
                   'updated_at INTEGER, PRIMARY KEY (gw, coll, key))')
        for n, (gw, key, data) in enumerate(rows):
            db.execute('INSERT INTO replica VALUES (?,?,?,?,?,?,?)',
                       (gw, 'tasks', key, 1, n + 1, json.dumps(data), int(time.time() * 1000)))
    return path


def _remote(**kw):
    card = {'id': 't_7e2ee45c', 'status': 'archived', 'title': 'BB model-balancing incident', 'summary': None,
            'remote': True, 'stale': False, 'via': 'shepherd', 'unfinished': False, 'gateway': 'hub',
            'profile': 'default', 'board': 'default', 'key': 'hub/default/default/t_7e2ee45c'}
    card.update(kw)
    return card


def test_prompt_renders_latest_comment_for_remote_card_without_handoff():
    key = 'hub/default/default/t_7e2ee45c'
    card = _remote(last_comment={'author': 'hub:gw:macbook', 'at': 1794441600, 'body': DECISION})
    prompt = core.closeout_prompt({'batch': [key]}, {key: card}, now=1794500000)
    assert 'latest comment' in prompt and 'Close scoped' in prompt, prompt
    assert '(no handoff)' not in prompt
    assert 'hub:gw:macbook' in prompt and '2026-11-' in prompt  # who and when: the turn cites it
    line = next(l for l in prompt.splitlines() if 'latest comment' in l)
    assert len(line.split('latest comment', 1)[1]) <= 380  # head only (300 chars of body)


def test_prompt_keeps_no_handoff_without_comments_and_prefers_the_handoff():
    key = 'hub/default/default/t_7e2ee45c'
    assert '(no handoff)' in core.closeout_prompt({'batch': [key]}, {key: _remote()}, now=1794500000)
    done = _remote(summary='Fixed it.', last_comment={'author': 'x', 'at': 1, 'body': 'old note'})
    prompt = core.closeout_prompt({'batch': [key]}, {key: done}, now=1794500000)
    assert 'handoff: Fixed it.' in prompt and 'latest comment' not in prompt


def test_replica_fill_adds_the_newest_comment_to_remote_cards_without_handoff(tmp_path, monkeypatch):
    hub = _replica(tmp_path / 'hub.db', [
        ('hub', 'default/default/t_7e2ee45c', {'id': 't_7e2ee45c', 'status': 'archived', 'last_comments': [
            {'author': 'default', 'body': 'watchdog note', 'at': 1794000000},
            {'author': 'hub:gw:macbook', 'body': DECISION, 'at': 1794441600}]}),
        ('hub', 'default/default/t_empty', {'id': 't_empty', 'status': 'done', 'last_comments': []}),
    ])
    monkeypatch.setattr(plugin, 'hub_db_path', lambda: hub)
    k1, k2, k3 = 'hub/default/default/t_7e2ee45c', 'hub/default/default/t_empty', 'default/t_local'
    cards = {k1: _remote(), k2: _remote(id='t_empty', key=k2),
             k3: {'id': 't_local', 'status': 'done', 'summary': None, 'board': 'default'}}
    out = plugin._with_latest_comments({'batch': [k1, k2, k3]}, cards)
    assert out[k1]['last_comment']['body'] == DECISION and out[k1]['last_comment']['author'] == 'hub:gw:macbook'
    assert 'last_comment' not in out[k2] and 'last_comment' not in out[k3]
    assert 'last_comment' not in cards[k1]  # a copy: inventory rows are core's cache
    prompt = core.closeout_prompt({'batch': [k1, k2, k3]}, out, now=1794500000)
    assert 'latest comment' in prompt and prompt.count('(no handoff)') == 2


def test_replica_fill_fails_open(tmp_path, monkeypatch):
    monkeypatch.setattr(plugin, 'hub_db_path', lambda: tmp_path / 'missing' / 'hub.db')
    key = 'hub/default/default/t_7e2ee45c'
    cards = {key: _remote()}
    assert plugin._with_latest_comments({'batch': [key]}, cards)[key].get('last_comment') is None


# ---- the real prompts: pre_llm_call and the wake tick fill the latest comment (mutants C5-C7 of 2910162d) -------
KEY = 'hub/default/default/t_7e2ee45c'


def _hub(tmp_path, monkeypatch, comments):
    hub = _replica(tmp_path / 'hub.db', [
        ('hub', 'default/default/t_7e2ee45c', {'id': 't_7e2ee45c', 'status': 'archived', 'last_comments': comments})])
    monkeypatch.setattr(plugin, 'hub_db_path', lambda: hub)
    led = core.Ledger(tmp_path / 'ledger.db')
    monkeypatch.setattr(plugin, 'ledger', lambda: led)
    led.write('chat', batch=[KEY], open=[])
    return led


def test_pre_llm_call_brief_shows_the_latest_comment_of_a_remote_card(tmp_path, monkeypatch):
    _hub(tmp_path, monkeypatch, [{'author': 'hub:gw:macbook', 'body': DECISION, 'at': 1794441600}])
    monkeypatch.setattr(plugin, '_eligible', lambda *a, **k: True)
    monkeypatch.setattr(plugin, 'open_card_ids', lambda _: [])
    monkeypatch.setattr(plugin, 'session_proof', lambda *a: False)  # never read the host's real transcripts
    cards = {KEY: _remote()}
    monkeypatch.setattr(plugin, 'read_cards', lambda _: cards)
    out = plugin.pre_llm_call(session_id='chat')
    assert out and 'latest comment [' in out['context'] and 'Close scoped' in out['context'], out
    assert 'last_comment' not in cards[KEY]  # the inventory row read stays unmodified


def test_wake_tick_brief_shows_the_latest_comment_of_a_remote_card(tmp_path, monkeypatch):
    _hub(tmp_path, monkeypatch, [{'author': 'hub:gw:macbook', 'body': DECISION, 'at': 1794441600}])
    monkeypatch.setattr(plugin, '_lineage', lambda sid: [sid])
    monkeypatch.setattr(plugin, '_session_source', lambda sid: 'desktop')
    assert plugin.ensure_upgraded(lambda ids: {}) is True
    prompts = []

    def wake(sid, prompt, **k):
        prompts.append(prompt)
        return 'started'
    cards = {KEY: _remote()}
    assert plugin.wake_tick(now=1000, wake=wake, unseen={}, last_user=lambda _: 0, read=lambda _: cards,
                            proof=lambda *a: False)
    assert len(prompts) == 1 and 'latest comment [' in prompts[0] and 'Close scoped' in prompts[0], prompts


def test_blank_newest_comment_does_not_hide_the_earlier_real_one(tmp_path, monkeypatch):
    _hub(tmp_path, monkeypatch, [{'author': 'hub:gw:macbook', 'body': DECISION, 'at': 1794441600},
                                 {'author': 'default', 'body': '  \n ', 'at': 1794441700}])
    out = plugin._with_latest_comments({'batch': [KEY]}, {KEY: _remote()})
    assert out[KEY]['last_comment']['body'] == DECISION, out[KEY]
    assert 'Close scoped' in core.closeout_prompt({'batch': [KEY]}, out, now=1794500000)
