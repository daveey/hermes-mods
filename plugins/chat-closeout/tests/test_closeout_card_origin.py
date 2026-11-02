"""A watchdog card is linked to the chat it closes out, so a card its worker files routes to that chat too.

Without it the only link to the chat was its title, and every child the closeout worker filed got
``creator_chats_unresolved`` (its result reached no chat). Runs against the real core (HERMES_CORE_SOURCE)."""
import importlib.util
import json
import os
import sqlite3
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('closeout_plugin_origin', ROOT / '__init__.py')
plugin = importlib.util.module_from_spec(spec)
plugin.__package__ = ''
spec.loader.exec_module(plugin)
core = plugin.core

core_src = os.environ.get('HERMES_CORE_SOURCE')
pytestmark = pytest.mark.skipif(not core_src, reason='set HERMES_CORE_SOURCE to the fork checkout')
GW = 'gw-test'


@pytest.fixture
def board(tmp_path, monkeypatch):
    import sys
    sys.path.insert(0, core_src)
    for var in ('HERMES_KANBAN_DB', 'HERMES_KANBAN_BOARD', 'HERMES_KANBAN_TASK', 'HERMES_DELEGATED_CHILD_CONTEXT',
                'HERMES_DELEGATED_CHILD_OWNER', 'HERMES_ORIGIN_SESSION'):
        monkeypatch.delenv(var, raising=False)  # a worker's env must never point this test at the live board
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    monkeypatch.setenv('HERMES_KANBAN_HOME', str(tmp_path))
    hub = tmp_path / 'hub-state'
    hub.mkdir()
    c = sqlite3.connect(hub / 'hub.db')
    c.execute('CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT)')
    c.execute('CREATE TABLE replica (gw TEXT)')
    c.execute('INSERT INTO meta VALUES (?, ?)', ('gateway', GW))
    c.commit()
    c.close()
    monkeypatch.setenv('HERMES_HUB_STATE', str(hub))
    (tmp_path / 'plugins').mkdir()
    led = core.Ledger(tmp_path / 'ledger.db')
    monkeypatch.setattr(plugin, 'ledger', lambda: led)
    monkeypatch.setitem(plugin._state, 'mono_seen', {})
    monkeypatch.setitem(plugin._state, 'post_failures', {})
    # A synced board (the MacBook's, where the bug was seen) carries task_chat_links.
    from hermes_cli.kanban_db_connect import connect_closing
    from hermes_cli.kanban_db_sync import SYNC_SCHEMA_SQL
    with connect_closing() as conn:
        conn.executescript(SYNC_SCHEMA_SQL)
    return led


def _links(conn, tid):
    return [tuple(r) for r in conn.execute(
        "SELECT chat_gateway, chat_profile, chat_session FROM task_chat_links WHERE task_id=? AND released=0",
        (tid,))]


def _assert_owned_by(conn, tid, sid):
    assert (GW, 'default', sid) in _links(conn, tid)
    sub = conn.execute("SELECT last_event_id, delivery_metadata FROM kanban_notify_subs "
                       "WHERE task_id=? AND platform='tui' AND chat_id=?", (tid, sid)).fetchone()
    assert sub is not None, 'the chat is not subscribed to its closeout card'
    latest = conn.execute('SELECT COALESCE(MAX(id), 0) FROM task_events WHERE task_id=?', (tid,)).fetchone()[0]
    return sub, latest


def test_watchdog_card_links_its_chat_and_a_worker_child_resolves_to_it(board):
    from hermes_cli import kanban_db as kb
    from hermes_cli.kanban_db_connect import connect_closing
    cards = {'t_one': {'status': 'done'}}
    sig = core.closeout_key({'batch': ['t_one']}, cards)
    board.write('chat_x', batch=['t_one'], open=[], settled_sig=sig, settled_at=1.0, wake_result='busy')
    args = dict(unseen={}, force=True, read=lambda _: cards)  # the real default route (file_card)
    plugin.watchdog_tick(now=2, mono=2, **args)
    key = f'chat-closeout:no-closeout:chat_x:{sig}'
    assert plugin.watchdog_tick(now=1900, mono=1900, **args) == [key]
    with connect_closing() as conn:
        tid = conn.execute('SELECT id FROM tasks WHERE idempotency_key=?', (key,)).fetchone()[0]
        sub, latest = _assert_owned_by(conn, tid, 'chat_x')
        assert json.loads(sub[1]).get('reason') == 'origin'
        assert sub[0] >= 1  # the cursor starts at the card's own history, never 0 (no replay)
        # The closeout worker files a card: core's creator-chat resolver routes it to the same chat.
        child = kb.create_task(conn, title='follow-up', assignee='default', created_by='default',
                               creator_task_id=tid)
        kinds = [r[0] for r in conn.execute('SELECT kind FROM task_events WHERE task_id=?', (child,))]
        assert 'creator_chats_unresolved' not in kinds
        assert (GW, 'default', 'chat_x') in _links(conn, child)
        # The periodic chat-link reconcile keeps the link (it is backed by creator_chats_resolved).
        from hermes_cli import kanban_chat_links
        kanban_chat_links.reconcile(conn, 'default', GW, ledger=False)
        assert (GW, 'default', 'chat_x') in _links(conn, tid)


def test_handoff_incident_links_the_handoff_chat(board):
    from hermes_cli.kanban_db_connect import connect_closing
    conn = plugin.routing._conn(board)
    conn.execute("INSERT INTO handoffs (id, session_id, sig, created_at) VALUES ('closeout-abc', 'chat_h', 's', 1)")
    conn.commit()
    conn.close()
    tid = plugin.file_card('handoff stuck', 'stuck', 'chat-closeout:handoff:closeout-abc')
    with connect_closing() as conn:
        _assert_owned_by(conn, tid, 'chat_h')


def test_relink_backfills_an_open_legacy_card_and_its_unresolved_child(board):
    """A card filed before the fix: no link, and a worker child already marked unresolved. The relink pass binds the
    card to its chat with the cursor at its latest event (no replay) and re-resolves the child; other cards and done
    closeout cards are left alone."""
    from hermes_cli import kanban_db as kb
    from hermes_cli.kanban_db_connect import connect_closing
    key = 'chat-closeout:unseen:chat_old:1700000000'
    with connect_closing() as conn:
        tid = kb.create_task(conn, title='chat-closeout: Kanban results stuck for chat chat_old', assignee='default',
                             created_by='chat-closeout', idempotency_key=key)
        kb.add_comment(conn, tid, 'someone', 'history before the link')
        child = kb.create_task(conn, title='child', assignee='default', created_by='default', creator_task_id=tid)
        assert 'creator_chats_unresolved' in [r[0] for r in conn.execute(
            'SELECT kind FROM task_events WHERE task_id=?', (child,))]
        done = kb.create_task(conn, title='chat-closeout: old', assignee='default', created_by='chat-closeout',
                              idempotency_key='chat-closeout:unseen:chat_done:1')
        kb.complete_task(conn, done, summary='x')
        other = kb.create_task(conn, title='unrelated', assignee='default', created_by='default')
        before = conn.execute('SELECT COALESCE(MAX(id),0) FROM task_events WHERE task_id=?', (tid,)).fetchone()[0]
    assert plugin.relink_open_cards() == [tid]
    assert plugin.relink_open_cards() == []  # idempotent
    with connect_closing() as conn:
        sub, latest = _assert_owned_by(conn, tid, 'chat_old')
        assert sub[0] >= before  # cursor at the card's latest event when linked: no replay of earlier history
        assert (GW, 'default', 'chat_old') in _links(conn, child)
        assert not _links(conn, done) and not _links(conn, other)


def test_no_gateway_name_still_files_the_card(board, monkeypatch, tmp_path):
    from hermes_cli.kanban_db_connect import connect_closing
    monkeypatch.setenv('HERMES_HUB_STATE', str(tmp_path / 'nowhere'))
    monkeypatch.setenv('HERMES_MAIL_CONFIG', str(tmp_path / 'nowhere.json'))
    tid = plugin.file_card('stuck', 'stuck', 'chat-closeout:unseen:chat_y:5')
    with connect_closing() as conn:
        assert conn.execute('SELECT 1 FROM tasks WHERE id=?', (tid,)).fetchone()
