"""A card created elsewhere that notifies this chat (kanban_notify_subs) backs a "report later" promise.

Regression: a chat waiting on four subscribed cards (a worker's follow-up card plus cards it commented on) was
told "it has no open kanban card", because the backing probe only saw cards the chat created.
"""
import importlib.util
import sqlite3
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('closeout_plugin_backed_subs_tests', ROOT / '__init__.py')
plugin = importlib.util.module_from_spec(spec)
plugin.__package__ = ''
spec.loader.exec_module(plugin)

CHAT = 'chat-closeout-test-chat'
PROMISE = 'The capture is running now.'


def _board(path: Path, cards, subs, with_subs_table: bool = True) -> Path:
    db = sqlite3.connect(path)
    db.execute('CREATE TABLE tasks (id TEXT PRIMARY KEY, status TEXT, session_id TEXT)')
    if with_subs_table:
        db.execute("CREATE TABLE kanban_notify_subs (task_id TEXT NOT NULL, platform TEXT NOT NULL, "
                   "chat_id TEXT NOT NULL, thread_id TEXT NOT NULL DEFAULT '', created_at INTEGER NOT NULL DEFAULT 0, "
                   "delivery_mode TEXT NOT NULL DEFAULT 'notify')")
        db.executemany('INSERT INTO kanban_notify_subs (task_id, platform, chat_id) VALUES (?, ?, ?)', subs)
    db.executemany('INSERT INTO tasks VALUES (?, ?, ?)', cards)
    db.commit()
    db.close()
    return path


@pytest.fixture
def isolated(monkeypatch):
    """The chat created no card itself; only the board under test is read."""
    monkeypatch.setattr(plugin, 'open_card_ids', lambda _: [])
    monkeypatch.setattr(plugin, '_lineage', lambda sid: [sid])
    monkeypatch.setattr(plugin, '_eligible', lambda *args, **kwargs: True)

    def use(board: Path):
        monkeypatch.setattr(plugin, 'board_paths', lambda: [board])
    return use


@pytest.mark.parametrize('status', ['todo', 'ready', 'running', 'scheduled', 'review'])
def test_subscribed_working_card_backs_the_promise(tmp_path, isolated, status):
    isolated(_board(tmp_path / 'kanban.db', [('t_follow', status, 'worker-session')], [('t_follow', 'tui', CHAT)]))
    assert plugin._backed(CHAT) is True
    assert plugin.pre_final_gate(session_id=CHAT, final_response=PROMISE) is None
    assert plugin.subscribed_open_cards(CHAT) == ['t_follow']


@pytest.mark.parametrize('status', ['done', 'archived', 'blocked'])
def test_settled_or_blocked_subscribed_card_does_not(tmp_path, isolated, status):
    isolated(_board(tmp_path / 'kanban.db', [('t_follow', status, 'worker-session')], [('t_follow', 'tui', CHAT)]))
    assert plugin.subscribed_open_cards(CHAT) == []
    assert plugin._backed(CHAT) is False
    assert plugin.pre_final_gate(session_id=CHAT, final_response=PROMISE)['hold'] is True


def test_another_chats_subscription_does_not(tmp_path, isolated):
    isolated(_board(tmp_path / 'kanban.db', [('t_other', 'running', 'worker-session')],
                    [('t_other', 'tui', 'some-other-chat')]))
    assert plugin._backed(CHAT) is False


def test_subscription_under_a_lineage_id_counts(tmp_path, isolated, monkeypatch):
    isolated(_board(tmp_path / 'kanban.db', [('t_follow', 'running', 'worker-session')],
                    [('t_follow', 'tui', CHAT + '-tip')]))
    monkeypatch.setattr(plugin, '_lineage', lambda sid: [sid, sid + '-tip'])
    assert plugin._backed(CHAT) is True


def test_board_without_subs_table_contributes_nothing(tmp_path, isolated):
    isolated(_board(tmp_path / 'kanban.db', [('t_follow', 'running', 'worker-session')], [],
                    with_subs_table=False))
    assert plugin.subscribed_open_cards(CHAT) == []
    assert plugin._backed(CHAT) is False


def test_any_board_can_hold_the_subscription(tmp_path, isolated, monkeypatch):
    empty = _board(tmp_path / 'default.db', [], [])
    other = _board(tmp_path / 'runners.db', [('t_runner', 'ready', 'worker-session')], [('t_runner', 'tui', CHAT)])
    isolated(empty)
    monkeypatch.setattr(plugin, 'board_paths', lambda: [empty, other])
    assert plugin.subscribed_open_cards(CHAT) == ['t_runner']
