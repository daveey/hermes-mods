"""Regression: the watchdog's DEFAULT alert route must reach a real sink. Every other watchdog test swaps in a fake
``post``, which hid that the old Inbox route was refused ("accept_prompt is required") on every run."""
import importlib.util
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('closeout_plugin_real_card', ROOT / '__init__.py')
plugin = importlib.util.module_from_spec(spec)
plugin.__package__ = ''
spec.loader.exec_module(plugin)
core = plugin.core

core_src = os.environ.get('HERMES_CORE_SOURCE')


@pytest.fixture
def board(tmp_path, monkeypatch):
    import sys
    sys.path.insert(0, core_src)
    for var in ('HERMES_KANBAN_DB', 'HERMES_KANBAN_BOARD', 'HERMES_KANBAN_TASK', 'HERMES_DELEGATED_CHILD_CONTEXT',
                'HERMES_DELEGATED_CHILD_OWNER'):
        monkeypatch.delenv(var, raising=False)  # a worker's env must never point this test at the live board
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    monkeypatch.setenv('HERMES_KANBAN_HOME', str(tmp_path))
    monkeypatch.setenv('HERMES_HUB_STATE', str(tmp_path / 'hub-state'))
    (tmp_path / 'plugins').mkdir()  # the live layout: the inbox plugin is installed next to the board
    (tmp_path / 'plugins' / 'inbox').symlink_to(ROOT.parent / 'inbox')
    led = core.Ledger(tmp_path / 'ledger.db')
    monkeypatch.setattr(plugin, 'ledger', lambda: led)
    monkeypatch.setitem(plugin._state, 'mono_seen', {})
    monkeypatch.setitem(plugin._state, 'post_failures', {})
    return led


@pytest.mark.skipif(not core_src, reason='set HERMES_CORE_SOURCE to the fork checkout')
def test_watchdog_files_real_card(board):
    from hermes_cli import kanban_db as kb
    from hermes_cli.kanban_db_connect import connect_closing
    cards = {'t_one': {'status': 'done'}}
    sig = core.closeout_key({'batch': ['t_one']}, cards)
    board.write('chat_x', batch=['t_one'], open=[], settled_sig=sig, settled_at=1.0, wake_result='busy')
    args = dict(unseen={}, force=True, read=lambda _: cards)  # NO post=: the real default route
    assert not plugin.watchdog_tick(now=2, mono=2, **args)
    key = f'chat-closeout:no-closeout:chat_x:{sig}'
    assert plugin.watchdog_tick(now=1900, mono=1900, **args) == [key]
    with connect_closing() as conn:
        rows = conn.execute('SELECT id, assignee, body FROM tasks WHERE idempotency_key = ?', (key,)).fetchall()
    assert len(rows) == 1
    tid, assignee, body = rows[0]
    assert assignee == 'default'
    assert 'chat_x' in body and 't_one' in body and 'busy' in body
    # A repeat of the same incident reuses the card instead of filing another.
    assert plugin.file_card('again', 'again', key) == tid


@pytest.mark.skipif(not core_src, reason='set HERMES_CORE_SOURCE to the fork checkout')
def test_watchdog_escalates_then_stops_after_three_failed_posts(board, caplog):
    def broken(*a):
        raise OSError('board down')
    args = dict(unseen={'chat': 1}, post=broken, force=True)
    plugin.watchdog_tick(now=1, mono=1, **args)
    calls = []
    def counting(*a):
        calls.append(a)
        raise OSError('board down')
    for t in range(4):
        assert not plugin.watchdog_tick(now=1802 + t, mono=1802 + t, unseen={'chat': 1}, post=counting, force=True)
    assert len(calls) == plugin.MAX_POST_ATTEMPTS == 3
    assert sum(r.levelname == 'ERROR' for r in caplog.records) == 1
