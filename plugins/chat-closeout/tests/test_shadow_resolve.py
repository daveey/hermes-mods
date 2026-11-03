"""A ledger key no local board or origin/shepherd projection row resolves, whose card a FRESH full-board shadow holds.

Hub sessions create hub cards that are never origin-bound to the MacBook chat that recorded them, so the projection
never carries them. Before this, ``_board_absence_veto`` found the id in the shadow and left the key uncertain
forever: never aged, ``is_unresolved`` true, the row pending and the watchdog filing "waits on cards nothing can
find" incidents that no automatic path cleared. Now such a key reads as a ``via='shadow'`` remote card: done/archived
settles (close-out), anything else works. Stale, missing or status-less coverage still never resolves or counts as
absence, and bare / three-part spellings collapse onto the resolved four-part key."""
import sqlite3
import time

import pytest
from test_unlink import world, tick, H
from test_closeout import plugin, core

ID = 't_one'
KEY = f'hub/default/runners/{ID}'


def shadow(root, board, rows=(), pulled_at=None, status_column=True, owner='hub'):
    path = root / 'kanban' / 'sync-shadow' / f'{board}.db'
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as db:
        if status_column:
            db.execute('CREATE TABLE IF NOT EXISTS tasks (id TEXT, status TEXT, title TEXT, completed_at INTEGER, '
                       'block_kind TEXT, human_request TEXT, created_on TEXT)')
        else:
            db.execute('CREATE TABLE IF NOT EXISTS tasks (id TEXT)')
        db.execute('CREATE TABLE IF NOT EXISTS kanban_sync_state (board_id TEXT, pulled_at REAL)')
        db.execute('DELETE FROM kanban_sync_state')
        db.execute('INSERT INTO kanban_sync_state VALUES (?, ?)', (board, time.time() if pulled_at is None
                                                                    else pulled_at))
        db.execute('DELETE FROM tasks')
        for tid, status, completed in rows:
            if status_column:
                db.execute('INSERT INTO tasks VALUES (?,?,?,?,NULL,NULL,?)', (tid, status, 'Shipped it', completed, owner))
            else:
                db.execute('INSERT INTO tasks VALUES (?)', (tid,))
    return path


@pytest.fixture
def sw(world, monkeypatch):
    led, inventory, boards = world
    monkeypatch.setattr(core, 'UNLINK_MAX_GAP_S', 2 * H)
    return led, plugin._kanban_root(), inventory


def test_shadow_done_resolves_and_settles(sw):
    led, root, _ = sw
    shadow(root, 'runners', [(ID, 'done', 1500)])
    cards = plugin.read_cards([KEY])
    card = cards[KEY]
    assert card['via'] == 'shadow' and card['remote'] and card['stale'] is False
    assert card['completed_at'] == 1500.0 and (card['gateway'], card['profile'], card['board']) == \
        ('hub', 'default', 'runners')
    assert KEY not in cards.uncertain and KEY not in cards.absence_veto
    assert core.is_settled(card) and not core.is_unresolved(card)
    led.write('chat', batch=[KEY], open=[KEY])
    calls = tick(2000)
    assert len(calls) == 1 and ID in calls[0]  # one close-out turn


@pytest.mark.parametrize('status', ['running', 'todo', 'ready', 'scheduled'])
def test_shadow_running_stays_working(sw, status):
    led, root, _ = sw
    shadow(root, 'runners', [(ID, status, None)])
    card = plugin.read_cards([KEY])[KEY]
    assert core.is_working(card) and not core.is_settled(card) and not core.is_unresolved(card)
    led.write('chat', batch=[KEY], open=[KEY])
    for h in range(30):
        assert tick(1000 + h * H) == []
    row = led.get('chat')
    assert row['batch'] == [KEY] and row['unlinked'] == {}


@pytest.mark.parametrize('failure', ['stale', 'future', 'missing', 'corrupt', 'no_status'])
def test_stale_or_unreadable_shadow_stays_uncertain(sw, failure):
    led, root, _ = sw
    path = root / 'kanban' / 'sync-shadow' / 'runners.db'
    if failure == 'stale':
        shadow(root, 'runners', [(ID, 'done', 1500)], pulled_at=time.time() - 121)
    elif failure == 'future':
        shadow(root, 'runners', [(ID, 'done', 1500)], pulled_at=time.time() + 3600)
    elif failure == 'corrupt':
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('not a database')
    elif failure == 'no_status':
        shadow(root, 'runners', [(ID, None, None)], status_column=False)
    cards = plugin.read_cards([KEY])
    assert KEY not in cards and KEY in cards.uncertain and KEY in cards.absence_veto
    led.write('chat', batch=[KEY], open=[KEY])
    for h in range(30):
        assert tick(1000 + h * H) == []
    assert led.get('chat')['batch'] == [KEY] and led.get('chat')['unlinked'] == {}


@pytest.mark.parametrize('status', ['running', 'done'])
def test_bare_and_three_part_spellings_collapse_onto_the_resolved_key(sw, status):
    led, root, _ = sw
    shadow(root, 'runners', [(ID, status, 1500 if status == 'done' else None)])
    shadow(root, 'default', [('t_other', 'todo', None)])
    keys = [KEY, f'hub/runners/{ID}', f'hub/default/{ID}', ID]
    cards = plugin.read_cards(keys)
    assert set(cards) == set(keys) and not cards.uncertain
    assert all(cards[k].get('canonical_key') == KEY for k in keys[1:])
    led.write('chat', batch=keys, open=keys)
    calls = tick(2000)
    assert led.get('chat')['batch'] in ([KEY], [])
    assert len(calls) == (1 if status == 'done' else 0)
    if status == 'running':
        assert led.get('chat')['batch'] == [KEY]


def test_bare_key_resolves_alone_when_one_fresh_board_holds_it(sw):
    _, root, _ = sw
    shadow(root, 'runners', [(ID, 'archived', 1500)])
    card = plugin.read_cards([ID])[ID]
    assert card['via'] == 'shadow' and card['board'] == 'runners' and core.is_settled(card)
    assert 'canonical_key' not in card


def test_bare_key_on_two_boards_or_beside_a_stale_board_stays_uncertain(sw):
    _, root, _ = sw
    shadow(root, 'runners', [(ID, 'done', 1500)])
    shadow(root, 'default', [(ID, 'todo', None)])
    cards = plugin.read_cards([ID, f'hub/runners/{ID}'])
    assert not set(cards) and {ID, f'hub/runners/{ID}'} <= cards.uncertain
    shadow(root, 'default', [], pulled_at=1)  # the other board no longer holds it, but is stale: a gap
    cards = plugin.read_cards([ID])
    assert ID not in cards and ID in cards.uncertain


def test_projection_failure_never_resolves_from_the_shadow(sw):
    _, root, _ = sw
    shadow(root, 'runners', [(ID, 'done', 1500)])

    def broken():
        raise RuntimeError('projection down')
    cards = plugin.read_cards([KEY], remote_inventory=broken)
    assert KEY not in cards and KEY in cards.uncertain and cards.readable is False


def test_projection_row_wins_over_the_shadow(sw):
    _, root, inventory = sw
    shadow(root, 'runners', [(ID, 'done', 1500)])
    inventory.append({'id': ID, 'gateway': 'hub', 'profile': 'default', 'board': 'runners', 'remote': True,
                      'stale': False, 'via': 'shepherd', 'status': 'running', 'unfinished': True})
    card = plugin.read_cards([KEY])[KEY]
    assert card['via'] == 'shepherd' and core.is_working(card)


@pytest.mark.parametrize('light', [False, True])
def test_pass_reader_reuse_rejudges_shadow_freshness(sw, light, monkeypatch):
    led, root, _ = sw
    shadow(root, 'runners', [(ID, 'done', 1500)], pulled_at=1000)
    led.write('chat', batch=[KEY], open=[KEY])
    monkeypatch.setattr(plugin.time, 'time', lambda: 1010)
    first = plugin._pass_reader(light=light)([KEY])
    assert first[KEY]['via'] == 'shadow'
    first[KEY]['status'] = 'mutated'  # callers get fresh copies
    again = plugin._pass_reader(light=light)([KEY])
    assert again[KEY]['status'] == 'done'
    monkeypatch.setattr(plugin.time, 'time', lambda: 1200)  # the same reused read, now a stale shadow
    late = plugin._pass_reader(light=light)([KEY])
    assert KEY not in late and KEY in late.uncertain


@pytest.mark.parametrize('owner', ['mini', None])
def test_another_gateways_card_of_that_id_vetoes_but_never_settles(sw, owner):
    _, root, _ = sw
    shadow(root, 'runners', [(ID, 'done', 1500)], owner=owner)
    cards = plugin.read_cards([KEY, f'hub/runners/{ID}'])
    assert not set(cards) and {KEY, f'hub/runners/{ID}'} <= cards.uncertain
    assert plugin.read_cards([ID])[ID]['via'] == 'shadow'  # a bare id names no owner
