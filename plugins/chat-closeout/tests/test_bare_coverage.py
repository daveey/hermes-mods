"""Bare-id ledger entries need full-board coverage too, not absence from the linked-card projection.

A bare ``t_x`` names no board. When no local card and no projection row resolves it, it is checked against every
board this host knows beyond its local boards (``sync-shadow/*.db`` and link-only ``sync-links/*.db``): the id on
any of them, or any of them lacking a fresh readable shadow, makes the pass uncertain (old missing time is
discarded). Only a card gone from every covered board is observed absent and unlinks after 24 h."""
import sqlite3
import time

import pytest
from test_unlink import world, tick, H
from test_closeout import plugin, core

ID = 't_one'


def shadow(root, board, ids=(), pulled_at=None, board_id=None):
    path = root / 'kanban' / 'sync-shadow' / f'{board}.db'
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE IF NOT EXISTS tasks (id TEXT, status TEXT)')
        db.execute('CREATE TABLE IF NOT EXISTS kanban_sync_state (board_id TEXT, pulled_at REAL)')
        db.execute('DELETE FROM kanban_sync_state')
        db.execute('INSERT INTO kanban_sync_state VALUES (?, ?)',
                   (board_id or board, time.time() if pulled_at is None else pulled_at))
        db.executemany('INSERT INTO tasks VALUES (?, ?)', [(i, 'todo') for i in ids])
    return path


def link_board(root, board):
    path = root / 'kanban' / 'sync-links' / f'{board}.db'
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE IF NOT EXISTS task_chat_links (task_id TEXT, chat_session TEXT)')
    return path


@pytest.fixture
def bare_world(world, monkeypatch):
    led, inventory, boards = world
    root = plugin._kanban_root()  # world's coverage home: holds a fresh, empty `build` shadow
    monkeypatch.setattr(core, 'UNLINK_MAX_GAP_S', 2 * H)
    return led, root


def seed(led, keys):
    # 23 h already observed under the old rule: the first covered pass must discard it.
    led.write('chat', batch=list(keys), open=list(keys), unlinked={k: {
        'since': 1, 'observed': 23 * H, 'at': 1000, 'mono': 1000, 'pid': core.os.getpid()} for k in keys})


def never_unlinks(led, keys, hours=30):
    for h in range(hours):
        assert tick(1000 + h * H) == []
        row = led.get('chat')
        assert sorted(row['batch']) == sorted(keys)
        assert not set(keys) & set(row['unlinked'])


@pytest.mark.parametrize('board', ['runners', 'default'])
@pytest.mark.parametrize('siblings', [False, True])
def test_bare_key_for_card_on_a_covered_board_never_unlinks(bare_world, board, siblings):
    led, root = bare_world
    shadow(root, board, [ID])
    link_board(root, board)
    keys = [ID] + ([f'hub/{board}/{ID}', f'hub/default/{board}/{ID}'] if siblings else [])
    seed(led, keys)
    cards = plugin.read_cards(keys)
    assert ID in cards.uncertain and ID in cards.absence_veto and ID not in cards
    never_unlinks(led, keys)


@pytest.mark.parametrize('light', [False, True])
def test_bare_veto_survives_pass_subset_and_canonical_view(bare_world, light):
    led, root = bare_world
    shadow(root, 'runners', [ID])
    seed(led, [ID])
    cards = core.canonical_view(plugin._pass_reader(light=light)([ID]))
    assert ID in cards.uncertain and ID in cards.absence_veto
    fix = core.track_unlinked(led.get('chat'), cards, healthy=lambda: True, now=1000, mono=1000)
    assert fix['unlinked'] == {}


@pytest.mark.parametrize('failure', ['missing', 'corrupt', 'stale', 'future', 'wrong_board'])
def test_unreadable_coverage_is_uncertain_and_clears_old_time(bare_world, failure):
    led, root = bare_world
    link_board(root, 'runners')  # this host knows the board: its full copy is the coverage
    if failure == 'corrupt':
        (root / 'kanban' / 'sync-shadow' / 'runners.db').write_text('not a database')
    elif failure == 'stale':
        shadow(root, 'runners', pulled_at=1)
    elif failure == 'future':
        shadow(root, 'runners', pulled_at=time.time() + 3600)
    elif failure == 'wrong_board':
        shadow(root, 'runners', board_id='other')
    seed(led, [ID])
    cards = plugin.read_cards([ID])
    assert ID in cards.uncertain and ID in cards.absence_veto
    never_unlinks(led, [ID])


def test_card_present_in_a_stale_shadow_is_not_absent(bare_world):
    led, root = bare_world
    shadow(root, 'runners', [ID], pulled_at=1)
    seed(led, [ID])
    never_unlinks(led, [ID])


@pytest.mark.parametrize('extra', [False, True])
def test_bare_card_gone_from_every_board_still_unlinks_after_24h(bare_world, extra):
    led, root = bare_world
    if extra:  # several fresh boards, a link-only board among them, none with the card
        shadow(root, 'runners', ['t_other'])
        shadow(root, 'default', ['t_other'])
        link_board(root, 'runners')
    led.write('chat', batch=[ID], open=[ID])
    assert ID not in plugin.read_cards([ID]).absence_veto
    for h in range(24):
        tick(1000 + h * H)
    assert led.get('chat')['batch'] == [ID]
    tick(1000 + 24 * H)
    assert led.get('chat')['batch'] == []
    assert 'removed' in led.get('chat')['unlinked'][ID]


def test_host_without_board_sync_keeps_the_local_plus_projection_rule(bare_world, tmp_path, monkeypatch):
    led, _ = bare_world
    monkeypatch.setattr(plugin, '_kanban_root', lambda: tmp_path / 'no-sync-home')
    led.write('chat', batch=[ID], open=[ID])
    for h in range(25):
        tick(1000 + h * H)
    assert led.get('chat')['batch'] == []
