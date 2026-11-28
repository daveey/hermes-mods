"""Synthetic red-chat delivery, custody and settled-owner escalation contracts."""
import copy
import json
import sqlite3
from types import SimpleNamespace

import pytest
from sr_helpers import PLUGIN, load

nudge = load('_sr_red_nudge', PLUGIN / 'nudge.py')
run = load('_sr_red_nudge_run', PLUGIN / 'run.py')


def finding():
    cards = [{'card': f't_example_{i}', 'board': 'default', 'status': 'triage',
              'hold_kind': 'lane_gate' if i else 'safety', 'reason': 'held for review ' * 30,
              'execution_refusal': i == 0} for i in range(5)]
    return {'id': 'BS-red-no-ask', 'fingerprint': 'BS-red-no-ask:chat/example:' +
            ','.join(c['card'] for c in cards), 'chat': 'example', 'card': None,
            'area': 'card', 'fix': {'kind': 'nudge'},
            'evidence': {'red_cards': cards, 'last_message_id': 1,
                         'linked_cards': [('default', c['card']) for c in cards],
                         'card_signatures': {'default/' + c['card']: 'original' for c in cards},
                         'ask_signature': 'none', 'outcome_signature': 'stuck'}}


@pytest.fixture
def harness(tmp_path):
    (tmp_path / 'session-repair').mkdir()
    db = sqlite3.connect(':memory:')
    db.execute('CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT, role TEXT, '
               'content TEXT, timestamp REAL, active INTEGER DEFAULT 1)')
    db.execute("INSERT INTO messages VALUES (1,'example','assistant','old',9000,1)")
    h = SimpleNamespace(current=finding(), live=False, calls=[], deliver=True, now=10000, existing=None)
    src = SimpleNamespace(state_conn=lambda: db, open_fix_card=lambda fp: h.existing)
    scan = SimpleNamespace(Sources=lambda: src, _red_chat_live=lambda *a: h.live,
                           red_no_ask_findings=lambda src, only: [copy.deepcopy(h.current)] if h.current else [])
    def runner(argv, **kwargs):
        h.calls.append(argv)
        if h.deliver:
            db.execute('INSERT INTO messages(session_id,role,content,timestamp) VALUES (?,?,?,?)',
                       ('example', 'user', argv[-1], h.now))
    def tick(now, fs=None, **kwargs):
        h.now = now
        return nudge.process([copy.deepcopy(h.current)] if fs is None else fs,
                             home=tmp_path, now=now, scan=scan, runner=runner, argv=['hermes'],
                             read_json=run._read_json, write_json=run._write_json,
                             append=run._append, run_mode=kwargs.pop('run_mode', 'live'), **kwargs)
    def answer(at):
        db.execute('INSERT INTO messages(session_id,role,content,timestamp) VALUES (?,?,?,?)',
                   ('example', 'assistant', 'Reassessed.', at))
        h.current['evidence']['last_message_id'] = db.execute('SELECT MAX(id) FROM messages').fetchone()[0]
        h.current['evidence']['outcome_signature'] = 'still stuck after reply'
    h.tick, h.answer, h.db, h.scan = tick, answer, db, scan
    h.state = lambda: json.loads((tmp_path / 'session-repair/nudges-live.json').read_text())
    yield h
    db.close()


@pytest.mark.parametrize('key', ['red_cards', 'linked_cards', 'last_message_id', 'card_signatures',
                                 'ask_signature', 'outcome_signature', 'new_future_key'])
def test_every_changed_evidence_field_suppresses_send(harness, key):
    h = harness
    original = copy.deepcopy(h.current)
    h.current['evidence'][key] = 'changed'
    assert h.tick(10000, [original])[0][0]['outcome'] == 'moved_on'
    assert not h.calls


def test_new_covering_ask_suppresses_send(harness):
    original = harness.current
    harness.current = None
    assert harness.tick(10000, [original])[0][0]['outcome'] == 'moved_on'
    assert not harness.calls


def test_full_prompt_preserves_all_records_and_safety(harness):
    h = harness
    h.tick(10000)
    text = h.calls[0][-1]
    assert len(text) > 1000
    for card in h.current['evidence']['red_cards']:
        assert json.dumps(card, ensure_ascii=True) in text
    for phrase in ('no new authority', 'no release', 'otherwise keep the hold', 'supported lanes',
                   'existing mandate', 'ONE real blocked', 'recommendation first', 'reply IDs',
                   'choices and consequences', 'provider access'):
        assert phrase in text


def test_one_send_response_does_not_reset_and_second_miss_uses_settlement(harness):
    h = harness
    h.tick(10000)
    assert not h.tick(20000)[1]  # admission alone is not settlement
    h.answer(20000)
    assert not h.tick(27199)[1]
    escalation = h.tick(27200)[1][0]
    assert escalation['area'] == 'card' and escalation['fix']['kind'] == 'card'
    assert escalation['evidence']['owner_settlement']['at'] == 20000
    h.tick(100000)
    assert len(h.calls) == 1
    history = h.state()['fps'][h.current['fingerprint']]
    assert history['times'] == [10000]


@pytest.mark.parametrize('key', ['red_cards', 'linked_cards', 'card_signatures', 'ask_signature'])
def test_changed_cards_or_ask_suppress_second_miss(harness, key):
    h = harness
    h.tick(10000)
    h.answer(11000)
    h.current['evidence'][key] = 'changed'
    assert not h.tick(30000)[1]
    assert len(h.calls) == 1


def test_live_turn_and_invalid_timestamp_are_not_settlement(harness):
    h = harness
    h.tick(10000)
    h.answer(9999)
    assert not h.tick(30000)[1]
    h.answer(11000)
    h.live = True
    assert not h.tick(30000)[1]
    h.live = False
    assert h.tick(30000)[1]


def test_latest_assistant_timestamp_prevents_early_second_miss(harness):
    h = harness
    h.tick(10000)
    h.answer(11000)
    h.answer(20000)
    assert not h.tick(25000)[1]
    assert h.tick(27200)[1]


def test_unknown_delivery_retains_frozen_identity_and_recovers(harness):
    h = harness
    h.deliver = False
    original = copy.deepcopy(h.current)
    assert h.tick(10000)[0][0]['outcome'] == 'delivery_unknown'
    pending = h.state()['fps'][original['fingerprint']]['pending']
    h.current = None
    assert h.tick(20000, [])[0][0]['outcome'] == 'delivery_unknown'
    assert h.state()['fps'][original['fingerprint']]['pending'] == pending
    h.db.execute('INSERT INTO messages(session_id,role,content,timestamp) VALUES (?,?,?,?)',
                 ('example', 'user', pending['text'], 10000))
    assert h.tick(21000, [])[0][0]['recovered']
    assert len(h.calls) == 1
    assert h.state()['fps'][original['fingerprint']]['times'] == [10000]


def test_existing_fix_card_forwarded_for_normal_dedupe(harness):
    h = harness
    h.tick(10000)
    h.answer(11000)
    h.existing = {'id': 't_existing_fix'}
    assert h.tick(20000)[1][0]['fix']['open_fix_card'] == 't_existing_fix'
    assert len(h.calls) == 1


def test_normal_lane_acknowledgement_stops_escalation(harness, tmp_path):
    h = harness
    h.tick(10000)
    h.answer(11000)
    assert h.tick(20000)[1]
    state = h.state()
    state['fps'][h.current['fingerprint']]['escalated'] = 20000
    (tmp_path / 'session-repair/nudges-live.json').write_text(json.dumps(state))
    assert not h.tick(100000)[1]
    assert len(h.calls) == 1


def test_run_mode_override_and_shadow_no_send(harness):
    assert harness.tick(10000, run_mode='shadow')[2] == 'shadow'
    assert not harness.calls
    assert harness.tick(11000, run_mode='live')[2] == 'live'
    assert len(harness.calls) == 1
