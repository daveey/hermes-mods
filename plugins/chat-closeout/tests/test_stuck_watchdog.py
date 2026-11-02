"""Watchdog: a ledger row pending >= 24 h whose batch has no found, still-working entry, and at least one entry
nothing can resolve (missing or unknown), files ONE Kanban card per row through the existing 15-min filing path;
when the row clears, the existing resolution path closes that card. Never the Inbox. Throwaway SQLite only; the
real-board case uses an isolated temporary board."""
import os
from pathlib import Path
import tempfile

import pytest
from test_closeout import plugin, core

H = 3600.0
DAY = 24 * H


@pytest.fixture
def wd(tmp_path, monkeypatch):
    led = core.Ledger(tmp_path / 'ledger.db')
    monkeypatch.setattr(plugin, 'ledger', lambda: led)
    plugin._state.clear()
    posts, resolved = [], []
    monkeypatch.setattr(plugin, 'resolve_card', lambda key, res: resolved.append((key, res)) or True)
    return led, posts, resolved


def run(cards, now, mono, posts):
    return plugin.watchdog_tick(now=now, mono=mono, unseen={}, force=True,
                                post=lambda title, body, key: posts.append((title, body, key)),
                                read=lambda ids: {k: v for k, v in cards.items() if k in ids})


def test_stuck_row_flagged_once_after_24h(wd):
    led, posts, _ = wd
    led.write('chat', batch=['default/t_gone', 'hub/default/build/t_stale'], open=[])
    cards = {'hub/default/build/t_stale': {'status': 'done', 'remote': True, 'stale': True}}
    assert run(cards, 1000, 0, posts) == []
    assert run(cards, 1000 + DAY - 60, DAY - 60, posts) == []
    keys = run(cards, 1000 + DAY, DAY, posts)
    assert keys == ['chat-closeout:stuck:chat:1000']
    assert 'default/t_gone' in posts[0][1] and '24' in posts[0][1]
    assert run(cards, 1000 + DAY + 900, DAY + 900, posts) == []    # idempotent per row
    plugin._state.clear()                                         # a restart re-observes, but the flag persists
    run(cards, 1000 + 2 * DAY, 0, posts)
    assert run(cards, 1000 + 2 * DAY + H, H, posts) == []
    assert len(posts) == 1


@pytest.mark.parametrize('cards', [
    {'default/t_gone': None, 'default/t_work': {'status': 'running'}},        # found, still working
    {'default/t_done': {'status': 'review'}},                                # resting: all found and settled
    {'hub/default/build/t_x': {'status': 'running', 'remote': True, 'stale': False, 'unfinished': True,
                               'via': 'shepherd'}},
])
def test_rows_with_working_or_only_found_entries_are_not_stuck(wd, cards):
    led, posts, _ = wd
    led.write('chat', batch=list(cards), open=[], covered=[k for k, v in cards.items() if v])
    present = {k: v for k, v in cards.items() if v}
    for n in range(4):
        run(present, 1000 + n * DAY, n * DAY, posts)
    assert posts == []


def test_wall_age_needs_process_observation(wd):
    """A lid sleep: 24 h of wall time, 10 min of this process watching it."""
    led, posts, _ = wd
    led.write('chat', batch=['default/t_gone'], open=[])
    run({}, 1000, 0, posts)
    assert run({}, 1000 + DAY, 600, posts) == []
    assert run({}, 1000 + DAY + core.STALE_S, 600 + core.STALE_S, posts) == ['chat-closeout:stuck:chat:1000']


def test_cleared_row_resolves_its_card(wd):
    led, posts, resolved = wd
    led.write('chat', batch=['default/t_gone'], open=[])
    run({}, 1000, 0, posts)
    key, = run({}, 1000 + DAY, DAY, posts)
    led.write('chat', batch=['default/t_gone', 'default/t_new'])
    run({'default/t_new': {'status': 'running'}}, 1000 + DAY + 60, DAY + 60, posts)
    assert [k for k, _ in resolved] == [key]
    assert 'working' in resolved[0][1]['evidence'] or 'batch' in resolved[0][1]['evidence']
    assert not led.has_flag(key)


def test_incident_key_names_its_chat():
    assert plugin._incident_session('chat-closeout:stuck:chat:1000') == 'chat'
    assert plugin._WATCHDOG_KEY.match('chat-closeout:stuck:chat:1000')


@pytest.fixture
def real_board(monkeypatch):
    with tempfile.TemporaryDirectory(prefix='closeout-stuck-', dir=Path.home()) as tmp:
        home = Path(tmp)
        monkeypatch.setenv('HERMES_HOME', tmp)
        monkeypatch.setenv('HERMES_KANBAN_HOME', tmp)
        monkeypatch.setenv('HERMES_HUB_STATE', str(home / 'hub'))
        for name in ('HERMES_KANBAN_DB', 'HERMES_KANBAN_BOARD', 'HERMES_KANBAN_TASK'):
            monkeypatch.delenv(name, raising=False)
        import hermes_cli
        monkeypatch.syspath_prepend(os.environ.get('HERMES_CORE_SOURCE') or str(Path(hermes_cli.__file__).parents[1]))
        from hermes_cli import kanban_db as kb
        from hermes_cli.kanban_db_connect import connect_closing
        from hermes_cli.kanban_db import kanban_db_path
        assert Path(kanban_db_path()).is_relative_to(home)
        led = core.Ledger(home / 'ledger.db')
        monkeypatch.setattr(plugin, 'ledger', lambda: led)
        plugin._state.clear()
        yield led, kb, connect_closing


def test_real_board_files_and_closes_one_card(real_board):
    led, kb, connect = real_board
    led.write('chat', batch=['default/t_gone'], open=[])
    args = dict(unseen={}, force=True, read=lambda ids: {})
    plugin.watchdog_tick(now=1000, mono=0, **args)
    key, = plugin.watchdog_tick(now=1000 + DAY, mono=DAY, **args)
    plugin.watchdog_tick(now=1000 + DAY + 900, mono=DAY + 900, **args)
    with connect() as conn:
        rows = conn.execute('SELECT id, status FROM tasks WHERE idempotency_key=?', (key,)).fetchall()
    assert len(rows) == 1 and rows[0]['status'] != 'done'
    led.write('chat', batch=[])
    plugin.watchdog_tick(now=1000 + DAY + 1800, mono=DAY + 1800, **args)
    with connect() as conn:
        assert kb.get_task(conn, rows[0]['id']).status == 'done'
    assert not led.has_flag(key)
