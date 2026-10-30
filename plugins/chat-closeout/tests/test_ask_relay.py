"""Ask relay (self-heal agent-8cecfe01cb): a batch card that newly needs the user gets ONE plain-words turn in its
chat even while another batch card still works (typically a todo card waiting on it), once per card per block
premise."""
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('closeout_plugin_ask_relay', ROOT / '__init__.py')
plugin = importlib.util.module_from_spec(spec)
plugin.__package__ = ''
spec.loader.exec_module(plugin)
core = plugin.core

ASK = {"action": "Confirm GitHub access for askevepodcast in Chrome (passkey or the emailed code)",
       "why_human": "only the account owner can pass GitHub's 2FA", "interaction": "external",
       "scope": "github.com login for askevepodcast", "evidence": "sudo-mode prompt on github.com/settings"}


def _blocked(ask=ASK, kind='needs_input'):
    return {'status': 'blocked', 'block_kind': kind, 'title': 'Set up askevepodcast GitHub',
            'human_request': json.dumps(ask)}


def _waker(tmp_path, monkeypatch):
    led = core.Ledger(tmp_path / 'ledger.db')
    monkeypatch.setattr(plugin, 'ledger', lambda: led)
    monkeypatch.setattr(plugin, '_lineage', lambda sid: [sid])
    calls = []

    def wake(sid, prompt, **kw):
        calls.append((sid, prompt, kw))
        return 'started'

    def tick(cards, now, last_user=0):
        return plugin.wake_tick(now=now, wake=wake, unseen={}, last_user=lambda _: last_user, read=lambda _: cards)
    return led, calls, tick


def test_ask_relay_fires_while_dependent_waits(tmp_path, monkeypatch):
    """The live failure: parent blocked on David, child todo waiting on it. Before the fix the batch never settled,
    so the chat said nothing for 18 h."""
    led, calls, tick = _waker(tmp_path, monkeypatch)
    cards = {'t_parent': _blocked(), 't_child': {'status': 'todo', 'title': 'Wire Alpha to Ask Eve'}}
    led.write('chat', batch=['t_parent', 't_child'], open=['t_parent', 't_child'])
    assert core.settled_sig(['t_parent', 't_child'], cards) is None  # the close-out alone never fires here
    out = tick(cards, 1000)
    assert [c[0] for c in calls] == ['chat'], out
    prompt = calls[0][1]
    assert ASK['action'] in prompt
    assert 'Wire Alpha to Ask Eve' in prompt  # what waits on it, by name
    assert 'card ids are not the explanation' in prompt.lower()
    row = led.get('chat')
    assert 't_parent' in row['covered'] and 't_child' not in row['covered']
    assert row['closing'] is None  # not a close-out: the child's result still gets one later


def test_ask_relay_respects_quiet_window(tmp_path, monkeypatch):
    led, calls, tick = _waker(tmp_path, monkeypatch)
    cards = {'t_parent': _blocked(), 't_child': {'status': 'todo'}}
    led.write('chat', batch=['t_parent', 't_child'], open=[])
    assert not tick(cards, 1000, last_user=900)  # the user typed 100 s ago
    assert calls == []
    assert tick(cards, 1300, last_user=900)
    assert len(calls) == 1


def test_ask_relay_once_per_premise(tmp_path, monkeypatch):
    led, calls, tick = _waker(tmp_path, monkeypatch)
    cards = {'t_parent': _blocked(), 't_child': {'status': 'todo'}}
    led.write('chat', batch=['t_parent', 't_child'], open=[])
    key1 = core.ask_relay_key(led.get('chat'), cards)
    assert key1
    assert tick(cards, 1000)
    assert core.ask_relay_key(led.get('chat'), cards) is None  # second tick: nothing new
    for now in (1030, 1060, 1500):
        assert not tick(cards, now)
    assert len(calls) == 1
    # re-blocked for a different reason (no running state seen in between): a new premise, a new key, one more turn
    cards['t_parent'] = _blocked({**ASK, 'action': 'Approve the Alpha Slack app install in Slack admin'})
    key2 = core.ask_relay_key(led.get('chat'), cards)
    assert key2 and key2 != key1
    assert tick(cards, 1600)
    assert len(calls) == 2 and 'Slack admin' in calls[1][1]
    assert not tick(cards, 1700)


def test_ask_relay_ignores_cards_without_a_validated_ask(tmp_path, monkeypatch):
    """Agent-owned blocks (no human_request, dependency/transient kinds) are owner-wake's job, not a user ask."""
    led, calls, tick = _waker(tmp_path, monkeypatch)
    cards = {'t_a': {'status': 'blocked', 'block_kind': 'needs_input', 'human_request': None},
             't_b': {'status': 'blocked', 'block_kind': 'dependency', 'human_request': json.dumps(ASK)},
             't_c': {'status': 'running'}}
    led.write('chat', batch=['t_a', 't_b', 't_c'], open=[])
    assert core.ask_relay_key(led.get('chat'), cards) is None
    assert not tick(cards, 1000)


def test_closeout_covered_ask_is_not_relayed_again(tmp_path, monkeypatch):
    """A close-out that already reported the ask records its premise: a later working card in the batch must not
    relay the same ask a second time."""
    led, calls, tick = _waker(tmp_path, monkeypatch)
    cards = {'t_parent': _blocked()}
    led.write('chat', batch=['t_parent'], open=['t_parent'])
    assert tick(cards, 1000)  # the whole batch settled: an ordinary close-out
    cards['t_new'] = {'status': 'running'}
    core.record_turn_end(led, 'chat', ['t_parent', 't_new'], lambda _: cards, now=1010)
    for now in (1400, 1800):
        assert not tick(cards, now)
    assert len(calls) == 1


def test_legacy_covered_ask_is_not_relayed_on_upgrade(tmp_path, monkeypatch):
    """Rows written before the relay existed have covered asks with no premise: no burst of wakes on deploy."""
    led, calls, tick = _waker(tmp_path, monkeypatch)
    cards = {'t_parent': _blocked(), 't_child': {'status': 'todo'}}
    led.write('chat', batch=['t_parent', 't_child'], open=[], covered=['t_parent'])
    assert not tick(cards, 1000)
    assert calls == []


def test_failed_relay_wake_undoes_coverage(tmp_path, monkeypatch):
    led = core.Ledger(tmp_path / 'ledger.db')
    monkeypatch.setattr(plugin, 'ledger', lambda: led)
    monkeypatch.setattr(plugin, '_lineage', lambda sid: [sid])
    cards = {'t_parent': _blocked(), 't_child': {'status': 'todo'}}
    led.write('chat', batch=['t_parent', 't_child'], open=[])
    plugin.wake_tick(now=1000, wake=lambda *a, **k: 'busy', unseen={}, last_user=lambda _: 0, read=lambda _: cards)
    row = led.get('chat')
    assert row['covered'] == [] and row['wake_count'] == 0 and not row.get('relayed')
    assert plugin.wake_tick(now=1030, wake=lambda *a, **k: 'started', unseen={}, last_user=lambda _: 0,
                            read=lambda _: cards)


def test_closeout_prompt_explains_a_user_ask_in_plain_words():
    cards = {'t_parent': _blocked()}
    prompt = core.closeout_prompt({'batch': ['t_parent']}, cards)
    assert ASK['action'] in prompt
    assert 'card ids are not the explanation' in prompt.lower()


def test_read_cards_returns_human_request(tmp_path):
    import sqlite3
    db = tmp_path / 'kanban.db'
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE tasks (id TEXT, status TEXT, title TEXT, block_kind TEXT, completed_at REAL, "
              "result TEXT, human_request TEXT)")
    c.execute("INSERT INTO tasks VALUES ('t_p', 'blocked', 'x', 'needs_input', NULL, NULL, ?)", (json.dumps(ASK),))
    c.commit()
    c.close()
    got = core.read_cards([db], ['t_p'])
    assert core.ask_of(got['t_p'])['action'] == ASK['action']


def test_user_turn_gets_the_ask_brief_and_covers_it(tmp_path, monkeypatch):
    """The incident turn ("i dont understand what this is blocked on"): a user turn while the ask is untold gets it
    in context, and its end records it, so the waker doesn't relay it again."""
    monkeypatch.setattr(plugin, '_eligible', lambda *args, **kwargs: True)
    led = core.Ledger(tmp_path / 'ledger.db')
    monkeypatch.setattr(plugin, 'ledger', lambda: led)
    monkeypatch.setattr(plugin, '_lineage', lambda sid: [sid])
    cards = {'t_parent': _blocked(), 't_child': {'status': 'todo', 'title': 'Wire Alpha'}}
    monkeypatch.setattr(plugin, 'read_cards', lambda _: cards)
    monkeypatch.setattr(plugin, 'open_card_ids', lambda _: ['t_parent', 't_child'])
    led.write('chat', batch=['t_parent', 't_child'], open=[])
    ctx = plugin.pre_llm_call(session_id='chat')['context']
    assert ASK['action'] in ctx
    plugin.post_llm_call(session_id='chat')
    assert plugin.pre_llm_call(session_id='chat') is None
    calls = []
    assert not plugin.wake_tick(now=5000, wake=lambda *a, **k: calls.append(a) or 'started', unseen={},
                                last_user=lambda _: 0, read=lambda _: cards)
    assert calls == []


def test_unblocked_then_same_block_again_is_a_new_ask(tmp_path, monkeypatch):
    led, calls, tick = _waker(tmp_path, monkeypatch)
    cards = {'t_parent': _blocked(), 't_child': {'status': 'todo'}}
    led.write('chat', batch=['t_parent', 't_child'], open=[])
    assert tick(cards, 1000)
    cards['t_parent'] = {'status': 'running'}
    assert not tick(cards, 1100)
    assert led.get('chat')['relayed'] == []
    cards['t_parent'] = _blocked()
    assert tick(cards, 1200)
    assert len(calls) == 2


# ---- self-heal agent-792a49aa96: a stored ask is not true forever ------------------------------------------------
NOW = 1_800_000_000.0


def _aged(seconds):
    return {**_blocked(), 'blocked_at': NOW - seconds}


def test_old_ask_carries_its_age_and_a_reverify_line():
    """The live failure: a 22-33 h old ask naming a suspended GitHub account was relayed verbatim, twice."""
    cards = {'t_parent': _aged(30 * 3600), 't_child': {'status': 'todo', 'title': 'Wire Alpha'}}
    prompt = core.ask_relay_prompt({'batch': ['t_parent', 't_child']}, cards, ['t_parent'], now=NOW)
    assert ASK['action'] in prompt  # still verbatim
    assert 'asked 30 h ago' in prompt
    low = prompt.lower()
    assert 'old ask' in low and 're-verify' in low
    assert 'still holds' in low  # the premise rule
    assert 'unblock' in low and 'comment' in low  # what to do when it is stale


def test_fresh_ask_gets_the_premise_rule_but_no_old_line():
    cards = {'t_parent': _aged(600), 't_child': {'status': 'todo'}}
    prompt = core.ask_relay_prompt({'batch': ['t_parent', 't_child']}, cards, ['t_parent'], now=NOW)
    assert 'asked 10 min ago' in prompt
    low = prompt.lower()
    assert 'still holds' in low
    assert 'old ask' not in low


def test_closeout_prompt_carries_the_age_too():
    prompt = core.closeout_prompt({'batch': ['t_parent']}, {'t_parent': _aged(31 * 3600)}, now=NOW)
    assert 'asked 31 h ago' in prompt and 'old ask' in prompt.lower()


def test_read_cards_returns_latest_block_time(tmp_path):
    import sqlite3
    db = tmp_path / 'kanban.db'
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE tasks (id TEXT, status TEXT, title TEXT, block_kind TEXT, completed_at REAL, "
              "result TEXT, human_request TEXT)")
    c.execute("CREATE TABLE task_events (id INTEGER PRIMARY KEY, task_id TEXT, run_id INTEGER, kind TEXT, "
              "payload TEXT, created_at INTEGER)")
    c.execute("INSERT INTO tasks VALUES ('t_p', 'blocked', 'x', 'needs_input', NULL, NULL, ?)", (json.dumps(ASK),))
    c.executemany("INSERT INTO task_events (task_id, kind, created_at) VALUES ('t_p', ?, ?)",
                  [('blocked', 100), ('unblocked', 200), ('blocked', 300), ('commented', 400)])
    c.commit()
    c.close()
    assert core.read_cards([db], ['t_p'])['t_p']['blocked_at'] == 300


def test_relay_logs_ask_age(tmp_path, monkeypatch, caplog):
    """Detection: each relayed ask logs ``ask_relay aged=<h>`` so the coach can count stale relays."""
    import logging
    led, calls, tick = _waker(tmp_path, monkeypatch)
    cards = {'t_parent': _aged(30 * 3600), 't_child': {'status': 'todo'}}
    led.write('chat', batch=['t_parent', 't_child'], open=[])
    with caplog.at_level(logging.INFO, logger='chat_closeout'):
        assert tick(cards, NOW)
    assert 'asked 30 h ago' in calls[0][1]
    assert any('ask_relay aged=30.0 ' in r.getMessage() and 't_parent' in r.getMessage() for r in caplog.records)
