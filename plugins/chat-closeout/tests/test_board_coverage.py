"""Full-board absence evidence is separate from the origin-linked projection."""
import sqlite3
import time

import pytest
from test_unlink import world, tick, H
from test_closeout import plugin, core

KEY = 'hub/default/build/t_one'


@pytest.fixture
def board_world(world, tmp_path, monkeypatch):
    led, inventory, _ = world
    root = tmp_path / 'home'
    monkeypatch.setattr(plugin, '_kanban_root', lambda: root)
    path = root / 'kanban' / 'sync-shadow' / 'build.db'
    path.parent.mkdir(parents=True)
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE tasks (id TEXT, status TEXT, created_on TEXT)')
        db.execute('CREATE TABLE task_chat_links (task_id TEXT, chat_session TEXT)')
        db.execute('CREATE TABLE kanban_sync_state (board_id TEXT, pulled_at REAL)')
        db.execute('INSERT INTO kanban_sync_state VALUES (?, ?)', ('build', time.time()))
    monkeypatch.setattr(core, 'UNLINK_MAX_GAP_S', 2 * H)
    led.write('chat', batch=[KEY], open=[], unlinked={KEY: {
        'since': 1, 'observed': 23 * H, 'at': 1000, 'mono': 1000, 'pid': core.os.getpid()}})
    return led, path


def test_linked_remote_board_card_never_unlinks(board_world):
    led, path = board_world
    with sqlite3.connect(path) as db:
        db.execute('INSERT INTO tasks VALUES (?, ?, ?)', ('t_one', 'todo', 'hub'))
        db.execute('INSERT INTO task_chat_links VALUES (?, ?)', ('t_one', 'chat'))
    for h in range(30):
        assert tick(1000 + h * H) == []
        assert led.get('chat')['batch'] == [KEY]
        assert KEY not in led.get('chat')['unlinked']


@pytest.mark.parametrize('failure', ['missing', 'corrupt', 'stale', 'future', 'wrong_board'])
def test_unreadable_board_is_uncertain_and_clears_old_time(board_world, failure):
    led, path = board_world
    if failure == 'missing':
        path.unlink()
    elif failure == 'corrupt':
        path.write_text('not a database')
    else:
        with sqlite3.connect(path) as db:
            if failure == 'stale':
                db.execute('UPDATE kanban_sync_state SET pulled_at=1')
            elif failure == 'future':
                db.execute('UPDATE kanban_sync_state SET pulled_at=?', (time.time() + 3600,))
            else:
                db.execute("UPDATE kanban_sync_state SET board_id='other'")
    cards = plugin.read_cards([KEY])
    assert KEY in cards.uncertain
    for h in range(30):
        tick(1000 + h * H)
        assert led.get('chat')['batch'] == [KEY]
        assert KEY not in led.get('chat')['unlinked']


def test_really_absent_remote_key_still_unlinks_after_24h(board_world):
    led, _ = board_world
    led.write('chat', unlinked={})
    for h in range(24):
        tick(1000 + h * H)
    assert led.get('chat')['batch'] == [KEY]
    tick(1000 + 24 * H)
    assert led.get('chat')['batch'] == []
    assert 'removed' in led.get('chat')['unlinked'][KEY]


@pytest.mark.parametrize('light', [False, True])
def test_veto_survives_pass_subset_and_canonical_view(board_world, light):
    led, path = board_world
    with sqlite3.connect(path) as db:
        # Shared board IDs are not profile-scoped. Even another owner is evidence
        # against "gone everywhere"; it is NOT permission to settle this entry.
        db.execute('INSERT INTO tasks VALUES (?, ?, ?)', ('t_one', 'done', 'other'))
    read = plugin._pass_reader(light=light)
    cards = core.canonical_view(read([KEY]))
    assert KEY in cards.uncertain and KEY in cards.absence_veto
    assert KEY not in cards
    fix = core.track_unlinked(led.get('chat'), cards, healthy=lambda: True, now=1000, mono=1000)
    assert fix['unlinked'] == {}


def test_projection_failure_still_discards_invalid_board_timer(board_world, monkeypatch):
    led, path = board_world
    path.unlink()
    def broken():
        raise OSError('projection unavailable')
    monkeypatch.setattr(plugin, 'remote_cards', broken)
    tick(1000)
    assert led.get('chat')['batch'] == [KEY]
    assert not led.get('chat')['unlinked']


@pytest.mark.parametrize('key', ['hub/build/t_one', 'hub/default/t_one'])
@pytest.mark.parametrize('available', [True, False])
def test_legacy_board_or_profile_key_cannot_claim_absence(board_world, key, available):
    led, path = board_world
    if available:
        with sqlite3.connect(path) as db:
            db.execute('INSERT INTO tasks VALUES (?, ?, ?)', ('t_one', 'todo', 'hub'))
    else:
        path.unlink()
    led.write('chat', batch=[key], unlinked={key: {
        'since': 1, 'observed': 23 * H, 'at': 1000, 'mono': 1000, 'pid': core.os.getpid()}})
    for h in range(30):
        assert tick(1000 + h * H) == []
        assert led.get('chat')['batch'] == [key]
        assert not led.get('chat')['unlinked']
