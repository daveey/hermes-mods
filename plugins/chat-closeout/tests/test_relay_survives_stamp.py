"""A successfully relayed ask stays relayed through the post-turn stamp, even when that stamp reads the (cross-gateway)
card as unknown/stale, so the next wake_tick never relays the same premise again."""
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('closeout_stamp', ROOT / '__init__.py')
plugin = importlib.util.module_from_spec(spec)
plugin.__package__ = ''
spec.loader.exec_module(plugin)
core = plugin.core

KEY = 'macbook/default/default/t_ask'
WORK = 'macbook/default/default/t_work'
HR = {'action': 'Pick the sandbox', 'why_human': 'Only you decide', 'interaction': 'reply',
      'scope': 'sandbox', 'evidence': 'two options'}


def ask_card(remote, stale=False):
    card = {'status': 'blocked', 'block_kind': 'needs_input', 'human_request': dict(HR)}
    if remote:
        card.update(remote=True, stale=stale, unfinished=True, via='origin', binding_id='b1')
    return card


def run(tmp_path, monkeypatch, remote, post_turn_cards, key_card=None):
    led = core.Ledger(tmp_path / 'ledger.db')
    monkeypatch.setattr(plugin, 'ledger', lambda: led)
    monkeypatch.setattr(plugin, '_lineage', lambda sid: [sid])
    monkeypatch.setattr(plugin, '_moved_owner', lambda sid: None, raising=False)
    fresh = {KEY: key_card or ask_card(remote), WORK: {'status': 'running'}}
    led.write('chat', batch=list(fresh), open=list(fresh))
    calls = []

    def wake(*args, **kwargs):
        calls.append(args)
        return 'started'

    def tick(now):
        return plugin.wake_tick(now=now, wake=wake, unseen={}, last_user=lambda _: 0, read=lambda _: fresh)

    tick(1000)
    assert len(calls) == 1
    # the relay turn ends: post-turn stamp reads the cards (possibly stale)
    core.record_turn_end(led, 'chat', [WORK], lambda ids: post_turn_cards, now=1012)
    for now in (1100, 1500, 5000):
        tick(now)
    return led, calls


@pytest.mark.parametrize('remote,stale', [(False, False), (True, False), (True, True)])
def test_relay_then_stamp_then_tick_does_not_relay_again(tmp_path, monkeypatch, remote, stale):
    post = {KEY: ask_card(remote, stale), WORK: {'status': 'running'}}
    led, calls = run(tmp_path, monkeypatch, remote, post)
    assert len(calls) == 1
    assert led.get('chat')['relayed']


def test_stamp_with_card_missing_from_read_keeps_relay(tmp_path, monkeypatch):
    led, calls = run(tmp_path, monkeypatch, True, {WORK: {'status': 'running'}})
    assert len(calls) == 1


def test_card_that_really_went_back_to_work_is_forgotten(tmp_path, monkeypatch):
    led = core.Ledger(tmp_path / 'ledger.db')
    prem = core.ask_premise(KEY, ask_card(True))
    led.write('chat', batch=[KEY, WORK], covered=[KEY], relayed=[prem])
    cards = {KEY: {'status': 'running', 'remote': True, 'stale': False, 'unfinished': True, 'via': 'origin',
                   'binding_id': 'b1'}, WORK: {'status': 'running'}}
    core.record_turn_end(led, 'chat', [KEY, WORK], lambda ids: cards, now=2000)
    row = led.get('chat')
    assert row['relayed'] == [] and row['covered'] == []


def shepherd_ask_card():
    """The live shape of the hub's macbook sandbox card: a FRESH shepherd-projected row, blocked with a complete
    human_request but block_kind None, so is_settled() is False while ask_premise() still names the ask."""
    return {'status': 'blocked', 'block_kind': None, 'human_request': dict(HR), 'remote': True, 'stale': False,
            'via': 'shepherd', 'unfinished': True, 'binding_id': None}


def test_fresh_shepherd_row_with_ask_but_no_block_kind_stays_relayed(tmp_path, monkeypatch):
    post = {KEY: shepherd_ask_card(), WORK: {'status': 'running'}}
    led, calls = run(tmp_path, monkeypatch, True, post, key_card=shepherd_ask_card())
    assert len(calls) == 1
    assert led.get('chat')['relayed']


def test_wake_tick_reconcile_keeps_relay_for_unsettled_ask_card(tmp_path):
    led = core.Ledger(tmp_path / 'ledger.db')
    card = shepherd_ask_card()
    prem = core.ask_premise(KEY, card)
    assert prem and not core.is_settled(card)
    assert core.reconcile({'batch': [KEY, WORK], 'covered': [KEY], 'relayed': [prem]},
                          {KEY: card, WORK: {'status': 'running'}}).get('relayed', {prem}) == {prem}


def test_changed_ask_on_unsettled_card_is_a_new_premise(tmp_path):
    card = shepherd_ask_card()
    prem = core.ask_premise(KEY, card)
    card['human_request']['action'] = 'A different question'
    out = core.reconcile({'batch': [KEY, WORK], 'covered': [KEY], 'relayed': [prem]},
                         {KEY: card, WORK: {'status': 'running'}})
    assert out['relayed'] == set()
