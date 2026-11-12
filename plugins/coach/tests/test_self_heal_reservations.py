"""Admission defaults must not inherit the watchdog's whole-lane fallback."""
from types import SimpleNamespace

import pytest

from test_self_heal_lane import sh, NOW, core, Runner, ev


@pytest.mark.parametrize('key,expected', [
    (sh.inv_key('agent-abc', 1), 8), (sh.inv_key('agent-abc', 3), 8),
    (sh.fix_key('agent-abc', 1), 15), (sh.fix_key('agent-abc', 3), 15),
    ('selfheal:agent-abc:verify', 15), ('unknown', 15), ('', 15),
])
def test_markerless_reservation_admits_fix(monkeypatch, key, expected):
    eb = sh._budget_mod()
    snap = {'lanes': {'selfheal': 0}, 'caps': {'selfheal': 200}, 'total_cap': 20, 'total': 0}
    card = {'id': 't_fixture', 'lane': 'selfheal', 'status': 'ready',
            'body': '', 'idempotency_key': key}
    monkeypatch.setattr(eb, 'snapshot', lambda **kw: snap)
    monkeypatch.setattr(eb, 'engine_cards', lambda *a: [card])
    monkeypatch.setattr(eb, 'card_cost', lambda *a: 0)
    b = sh.budget(now=NOW, new_card_usd=15)
    assert b['available'], b
    assert b['reserved'] == expected + 15
    assert sh._admission_denied(b, NOW) is None
    # The watchdog still permits a marker-less card up to the lane cap.
    monkeypatch.setattr(eb, 'card_cost', lambda *a: 15)
    assert eb.decide(None, None, snap, [card]) == []
    monkeypatch.setattr(eb, 'card_cost', lambda *a: 200)
    assert eb.decide(None, None, snap, [card])[0][2] == 200


def test_starvation_daily_event_and_incident_owner(tmp_path, monkeypatch):
    stamp = [NOW]
    store = core.Store(tmp_path, clock=lambda: stamp[0])
    runner = Runner()
    monkeypatch.setattr(sh, 'card_status', lambda *a: 'ready')
    b = {'available': True, 'ok_usd': False, 'usd': 0, 'cap': 200, 'reserved': 215,
         'holders': [{'card': 't_holder', 'reserved': 200}], 'new_card_usd': 15}
    assert sh._admission_denied(b, NOW, store, runner)['action'] == 'deferred'
    sh.note_reservation_starved(store, b, NOW, runner)
    assert len(ev(store, 'selfheal_reservation_starved')) == 1
    assert len(runner.creates()) == 1
    stamp[0] += 86400
    sh.note_reservation_starved(store, b, stamp[0], runner)
    assert len(ev(store, 'selfheal_reservation_starved')) == 2
    assert len(runner.creates()) == 1
    assert sum('comment' in a for a, _ in runner.calls) == 1
    assert ev(store, 'selfheal_reservation_starved')[0]['holders'] == b['holders']
    assert not any('inbox' in a for a, _ in runner.calls)
    # Closing an incident permits a distinct same-day recurrence, with a new key.
    monkeypatch.setattr(sh, 'card_status', lambda *a: 'done')
    sh.note_reservation_starved(store, b, stamp[0], runner)
    assert len(ev(store, 'selfheal_reservation_starved')) == 3
    assert len(runner.creates()) == 2
    assert runner.creates()[0] != runner.creates()[1]


def test_starvation_failed_delivery_retries_without_relogging(tmp_path, monkeypatch):
    store = core.Store(tmp_path, clock=lambda: NOW)
    b = {'available': True, 'ok_usd': False, 'usd': 0, 'cap': 200, 'reserved': 200}
    sh.note_reservation_starved(store, b, NOW, Runner(fail=True))
    runner = Runner()
    sh.note_reservation_starved(store, b, NOW, runner)
    assert len(ev(store, 'selfheal_reservation_starved')) == 1
    assert len(runner.creates()) == 1


@pytest.mark.parametrize('change', [{'usd': 0.01}, {'reserved': 199}, {'ok_usd': True}, {'available': False}])
def test_starvation_negative_controls(tmp_path, change):
    store = core.Store(tmp_path, clock=lambda: NOW)
    runner = Runner()
    b = {'available': True, 'ok_usd': False, 'usd': 0, 'cap': 200, 'reserved': 215, **change}
    sh.note_reservation_starved(store, b, NOW, runner)
    assert not ev(store, 'selfheal_reservation_starved')
    assert not runner.calls
