"""Close-outs show post-handoff comments of REMOTE cards too (t_01a1d6c01b4c0633; gap in 8e23ae92).

8e23ae92 (self-heal agent-93112d1e96) prints comments posted after a card's handoff under its handoff line, but only
for cards read from a local board. A batch card owned by another gateway arrives as a hub-replica row (`remote`): with
a handoff summary it got no later comments at all, so a decision David recorded as a comment on a card on a worker or
the hub (the usual shepherd case) still never reached the close-out turn or the coach judge. The plugin now takes them
from the replica record's `last_comments` (newest last), after the handoff anchor (`run.ended_at` when `run.summary`
is set, else `completed_at`), ranked and bounded like the local read.
"""
import json
import sqlite3
import time

from test_closeout import plugin, core

KEY = 'hub/default/default/t_9a1c0ffe'
HANDOFF = ("Closed without delivering: the fallback chain never ran live. Delivery needs authorization to narrow the "
           "completion or a direct user waiver.")
DECISION = "David explicitly selected Close scoped: accept the narrowed scope; no new live check unless he asks."
OLD = "Worker note before the run ended: starting fallback chain replay."
RUN_END, COMPLETED = 1_794_400_000, 1_794_400_600


def _replica(path, data):
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE replica (gw TEXT, coll TEXT, key TEXT, version INTEGER, seq INTEGER, data TEXT, '
                   'updated_at INTEGER, PRIMARY KEY (gw, coll, key))')
        db.execute('INSERT INTO replica VALUES (?,?,?,?,?,?,?)',
                   ('hub', 'tasks', 'default/default/t_9a1c0ffe', 1, 1, json.dumps(data), int(time.time() * 1000)))
    return path


def _record(comments, run=True, completed_at=COMPLETED):
    data = {'id': 't_9a1c0ffe', 'status': 'archived', 'completed_at': completed_at, 'last_comments': comments}
    if run:
        data['run'] = {'status': 'done', 'outcome': 'completed', 'started_at': RUN_END - 300, 'ended_at': RUN_END,
                       'summary': HANDOFF}
    return data


def _remote(**kw):
    card = {'id': 't_9a1c0ffe', 'status': 'archived', 'title': 'Model balancing incident', 'summary': HANDOFF,
            'remote': True, 'stale': False, 'via': 'shepherd', 'unfinished': False, 'gateway': 'hub',
            'profile': 'default', 'board': 'default', 'key': KEY}
    card.update(kw)
    return card


def _hub(tmp_path, monkeypatch, comments, **kw):
    hub = _replica(tmp_path / 'hub.db', _record(comments, **kw))
    monkeypatch.setattr(plugin, 'hub_db_path', lambda: hub)
    return hub


def _later(prompt):
    return [ln for ln in prompt.splitlines() if 'later on this card (after its handoff)' in ln]


def _fill_prompt(card=None):
    cards = {KEY: card or _remote()}
    out = plugin._with_latest_comments({'batch': [KEY]}, cards)
    assert 'later_comments' not in cards[KEY]  # a copy: inventory rows are core's cache
    return core.closeout_prompt({'batch': [KEY]}, out, now=1_794_500_000)


def test_remote_card_with_handoff_shows_only_post_handoff_decision(tmp_path, monkeypatch):
    _hub(tmp_path, monkeypatch, [{'author': 'default', 'body': OLD, 'at': RUN_END - 100},
                                 {'author': 'hub:gw:macbook', 'body': DECISION, 'at': COMPLETED + 3600}])
    prompt = _fill_prompt()
    lines = _later(prompt)
    assert len(lines) == 1 and DECISION in lines[0] and 'hub:gw:macbook' in lines[0], prompt
    assert OLD not in prompt  # before the handoff: the handoff already covers it
    assert 'handoff: ' + HANDOFF[:40] in prompt and 'latest comment' not in prompt
    body = prompt.splitlines()  # printed directly under this card's handoff line
    assert body.index(lines[0]) == next(i for i, ln in enumerate(body) if ln.lstrip().startswith('handoff:')) + 1


def test_remote_anchor_is_run_end_when_the_run_has_a_summary(tmp_path, monkeypatch):
    # Posted after the handoff run ended but before completed_at: it postdates the handoff, so it shows.
    _hub(tmp_path, monkeypatch, [{'author': 'hub:gw:macbook', 'body': DECISION, 'at': RUN_END + 60}])
    assert len(_later(_fill_prompt())) == 1


def test_remote_anchor_falls_back_to_completed_at_without_a_run_summary(tmp_path, monkeypatch):
    _hub(tmp_path, monkeypatch, [{'author': 'default', 'body': OLD, 'at': COMPLETED - 10},
                                 {'author': 'hub:gw:macbook', 'body': DECISION, 'at': COMPLETED + 10}], run=False)
    lines = _later(_fill_prompt())
    assert len(lines) == 1 and DECISION in lines[0] and OLD not in lines[0]


def test_remote_card_without_anchor_shows_no_later_lines(tmp_path, monkeypatch):
    _hub(tmp_path, monkeypatch, [{'author': 'hub:gw:macbook', 'body': DECISION, 'at': COMPLETED + 10}],
         run=False, completed_at=None)
    assert _later(_fill_prompt()) == []


def test_remote_later_comments_bounded_decisions_first(tmp_path, monkeypatch):
    chatter = [{'author': 'worker', 'body': f'chatter {i}', 'at': COMPLETED + 100 + i} for i in range(5)]
    _hub(tmp_path, monkeypatch, [{'author': 'hub:gw:macbook', 'body': DECISION, 'at': COMPLETED + 50}] + chatter)
    lines = _later(_fill_prompt())
    assert len(lines) == core.MAX_LATER_COMMENTS, lines
    assert DECISION in lines[0]  # the decision wins over newer chatter; printed oldest first
    assert 'chatter 4' in lines[1]


def test_remote_blank_later_comment_is_dropped(tmp_path, monkeypatch):
    _hub(tmp_path, monkeypatch, [{'author': 'hub:gw:macbook', 'body': DECISION, 'at': COMPLETED + 10},
                                 {'author': 'default', 'body': '  \n ', 'at': COMPLETED + 20}])
    lines = _later(_fill_prompt())
    assert len(lines) == 1 and DECISION in lines[0], lines


def test_remote_later_comments_fail_open(tmp_path, monkeypatch):
    monkeypatch.setattr(plugin, 'hub_db_path', lambda: tmp_path / 'missing' / 'hub.db')
    prompt = _fill_prompt()
    assert _later(prompt) == [] and 'handoff: ' + HANDOFF[:40] in prompt
    bad = tmp_path / 'bad.db'
    with sqlite3.connect(bad) as db:
        db.execute('CREATE TABLE replica (gw TEXT, coll TEXT, key TEXT, data TEXT)')
        db.execute("INSERT INTO replica VALUES ('hub', 'tasks', 'default/default/t_9a1c0ffe', '{not json')")
    monkeypatch.setattr(plugin, 'hub_db_path', lambda: bad)
    assert _later(_fill_prompt()) == []


def test_no_summary_remote_card_keeps_the_latest_comment_fill(tmp_path, monkeypatch):
    _hub(tmp_path, monkeypatch, [{'author': 'hub:gw:macbook', 'body': DECISION, 'at': COMPLETED + 10}], run=False)
    prompt = _fill_prompt(_remote(summary=None))
    assert 'latest comment [' in prompt and DECISION in prompt


# ---- the real prompts: pre_llm_call and the wake tick --------------------------------------------------------------
def _ledger(tmp_path, monkeypatch):
    led = core.Ledger(tmp_path / 'ledger.db')
    monkeypatch.setattr(plugin, 'ledger', lambda: led)
    led.write('chat', batch=[KEY], open=[])
    return led


def test_pre_llm_call_brief_shows_remote_post_handoff_decision(tmp_path, monkeypatch):
    _hub(tmp_path, monkeypatch, [{'author': 'default', 'body': OLD, 'at': RUN_END - 100},
                                 {'author': 'hub:gw:macbook', 'body': DECISION, 'at': COMPLETED + 3600}])
    _ledger(tmp_path, monkeypatch)
    monkeypatch.setattr(plugin, '_eligible', lambda *a, **k: True)
    monkeypatch.setattr(plugin, 'open_card_ids', lambda _: [])
    monkeypatch.setattr(plugin, 'session_proof', lambda *a: False)  # never read the host's real transcripts
    cards = {KEY: _remote()}
    monkeypatch.setattr(plugin, 'read_cards', lambda _: cards)
    out = plugin.pre_llm_call(session_id='chat')
    assert out and len(_later(out['context'])) == 1 and DECISION in out['context'], out
    assert OLD not in out['context']
    assert 'later_comments' not in cards[KEY]


def test_wake_tick_brief_shows_remote_post_handoff_decision(tmp_path, monkeypatch):
    _hub(tmp_path, monkeypatch, [{'author': 'default', 'body': OLD, 'at': RUN_END - 100},
                                 {'author': 'hub:gw:macbook', 'body': DECISION, 'at': COMPLETED + 3600}])
    _ledger(tmp_path, monkeypatch)
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
    assert len(prompts) == 1 and len(_later(prompts[0])) == 1 and DECISION in prompts[0], prompts
    assert OLD not in prompts[0]
