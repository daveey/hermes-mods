"""Exercise the default watchdog sink and resolver against an isolated real board."""
import importlib.util
import os
from pathlib import Path
import tempfile

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('closeout_resolution', ROOT / '__init__.py')
plugin = importlib.util.module_from_spec(spec)
plugin.__package__ = ''
spec.loader.exec_module(plugin)


@pytest.fixture
def incident(monkeypatch):
    # Outside the worker-fenced workspace; retain descendant/owner guards.
    with tempfile.TemporaryDirectory(prefix='closeout-resolution-', dir=Path.home()) as tmp:
        home = Path(tmp)
        monkeypatch.setenv('HERMES_HOME', tmp)
        monkeypatch.setenv('HERMES_KANBAN_HOME', tmp)
        monkeypatch.setenv('HERMES_HUB_STATE', str(home / 'hub'))
        for name in ('HERMES_KANBAN_DB', 'HERMES_KANBAN_BOARD', 'HERMES_KANBAN_TASK'):
            monkeypatch.delenv(name, raising=False)
        import hermes_cli
        # Core's top-level helpers (hermes_state, hermes_dir_acl) live beside the hermes_cli package.
        monkeypatch.syspath_prepend(os.environ.get('HERMES_CORE_SOURCE') or str(Path(hermes_cli.__file__).parents[1]))
        from hermes_cli import kanban_db as kb
        from hermes_cli.kanban_db_connect import connect_closing
        from hermes_cli.kanban_db import kanban_db_path
        assert Path(kanban_db_path()).is_relative_to(home)
        led = plugin.core.Ledger(home / 'ledger.db')
        monkeypatch.setattr(plugin, 'ledger', lambda: led)
        plugin._state.clear()
        with connect_closing() as conn:
            assert Path(conn.execute('PRAGMA database_list').fetchone()[2]).is_relative_to(home)
        yield led, kb, connect_closing


def raise_unseen(incident):
    led, kb, connect = incident
    plugin.watchdog_tick(now=1, mono=1, unseen={'chat': 1}, force=True)
    keys = plugin.watchdog_tick(now=1902, mono=1902, unseen={'chat': 1}, force=True)
    assert len(keys) == 1
    with connect() as conn:
        tid = conn.execute('SELECT id FROM tasks WHERE idempotency_key=?', (keys[0],)).fetchone()[0]
    return keys[0], tid


def test_cleared_unseen_completes_and_clears_after_restart(incident):
    led, kb, connect = incident
    key, tid = raise_unseen(incident)
    plugin._state.clear()  # flags must be enumerated durably, not from mono_seen
    plugin.watchdog_tick(now=2000, mono=2000, unseen={}, force=True)
    with connect() as conn:
        assert kb.get_task(conn, tid).status == 'done'
        summary = kb.list_runs(conn, tid)[-1].summary
    assert key in summary and '2000' in summary and 'unseen count 0' in summary
    assert not led.has_flag(key)


def test_cleared_no_closeout_completes(incident):
    led, kb, connect = incident
    cards = {'work': {'status': 'done'}}
    sig = plugin.core.closeout_key({'batch': ['work']}, cards)
    led.write('chat', batch=['work'], settled_sig=sig, settled_at=1)
    args = dict(unseen={}, force=True, read=lambda _: cards)
    plugin.watchdog_tick(now=2, mono=2, **args)
    key, = plugin.watchdog_tick(now=1902, mono=1902, **args)
    led.write('chat', done_sig=sig, done_at=1950)
    plugin.watchdog_tick(now=2000, mono=2000, **args)
    with connect() as conn:
        task = conn.execute('SELECT id,status FROM tasks WHERE idempotency_key=?', (key,)).fetchone()
        assert task['status'] == 'done'
        assert 'done_sig matched' in kb.list_runs(conn, task['id'])[-1].summary
    assert not led.has_flag(key)


def test_live_run_comment_only_then_later_completion(incident):
    led, kb, connect = incident
    key, tid = raise_unseen(incident)
    with connect() as conn:
        assert kb.claim_task(conn, tid)
    plugin.watchdog_tick(now=2000, mono=2000, unseen={}, force=True)
    with connect() as conn:
        assert kb.get_task(conn, tid).status == 'running'
        comments = kb.list_comments(conn, tid)
        assert len(comments) == 1 and key in comments[0].body
    assert led.has_flag(key)
    plugin.watchdog_tick(now=2010, mono=2010, unseen={}, force=True)
    with connect() as conn:
        assert len(kb.list_comments(conn, tid)) == 1  # no per-tick comment storm
        # Simulate the worker releasing its run to review.
        assert kb.request_review(conn, tid, summary='condition cleared', expected_run_id=kb.live_run_id(conn, tid))
    plugin.watchdog_tick(now=2100, mono=2100, unseen={}, force=True)
    with connect() as conn:
        assert kb.get_task(conn, tid).status == 'done'
        assert '2000' in kb.list_runs(conn, tid)[-1].summary
    assert not led.has_flag(key)


def test_condition_still_holds_leaves_card_untouched(incident):
    led, kb, connect = incident
    key, tid = raise_unseen(incident)
    plugin.watchdog_tick(now=2000, mono=2000, unseen={'chat': 1}, force=True)
    with connect() as conn:
        assert kb.get_task(conn, tid).status == 'ready'
        assert not kb.list_comments(conn, tid)
    assert led.has_flag(key)


def test_recurrence_files_new_card(incident):
    led, kb, connect = incident
    key, tid = raise_unseen(incident)
    plugin.watchdog_tick(now=2000, mono=2000, unseen={}, force=True)
    plugin.watchdog_tick(now=2001, mono=2001, unseen={'chat': 1}, force=True)
    new_key, = plugin.watchdog_tick(now=3902, mono=3902, unseen={'chat': 1}, force=True)
    with connect() as conn:
        new_tid = conn.execute('SELECT id FROM tasks WHERE idempotency_key=?', (new_key,)).fetchone()[0]
        assert new_tid != tid and kb.get_task(conn, tid).status == 'done'
    assert new_key != key and led.has_flag(new_key) and not led.has_flag(key)


def test_same_signature_recurrence_files_new_card(incident):
    led, kb, connect = incident
    key = 'chat-closeout:no-closeout:chat:abc'
    tid = plugin.file_card('stuck', 'stuck', key)
    led.flag_once(key, 1)
    led.write('chat', batch=['work'], settled_sig='abc', done_sig='abc')
    plugin.watchdog_tick(now=2000, mono=2000, unseen={}, force=True)
    new_tid = plugin.file_card('stuck again', 'stuck again', key)
    assert new_tid != tid


def test_blocked_incident_unblocked_then_completed(incident):
    led, kb, connect = incident
    key, tid = raise_unseen(incident)
    with connect() as conn:
        # Historical malformed human block: reproduce the incident without creating a new ask.
        with kb.write_txn(conn):
            conn.execute("UPDATE tasks SET status='blocked',block_kind='needs_input' WHERE id=?", (tid,))
    plugin.watchdog_tick(now=2000, mono=2000, unseen={}, force=True)
    with connect() as conn:
        assert kb.get_task(conn, tid).status == 'done'
        kinds = [r[0] for r in conn.execute('SELECT kind FROM task_events WHERE task_id=? ORDER BY id', (tid,))]
        assert kinds.index('unblocked') < kinds.index('completed')
    assert not led.has_flag(key)


def test_completion_failure_retains_flag_and_first_clear_time(incident, monkeypatch):
    led, kb, connect = incident
    key, tid = raise_unseen(incident)
    with monkeypatch.context() as patch:
        patch.setattr(kb, 'complete_task', lambda *a, **kw: False)
        plugin.watchdog_tick(now=2000, mono=2000, unseen={}, force=True)
    assert led.has_flag(key)
    plugin._state.clear()
    plugin.watchdog_tick(now=2100, mono=2100, unseen={}, force=True)
    with connect() as conn:
        assert kb.get_task(conn, tid).status == 'done'
        assert '2000' in kb.list_runs(conn, tid)[-1].summary
    assert not led.has_flag(key)


def test_claim_race_never_completes_another_run(incident, monkeypatch):
    led, kb, connect = incident
    key, tid = raise_unseen(incident)
    original = kb.live_run_id
    def race(conn, task):
        if kb.get_task(conn, task).status == 'ready':
            assert kb.claim_task(conn, task, claimer='other-worker')
            return None  # stale pre-claim observation
        return original(conn, task)
    monkeypatch.setattr(kb, 'live_run_id', race)
    plugin.watchdog_tick(now=2000, mono=2000, unseen={}, force=True)
    with connect() as conn:
        assert kb.get_task(conn, tid).status == 'running'
    assert led.has_flag(key)


def test_moved_on_batch_clears_no_closeout(incident):
    led, kb, connect = incident
    key = 'chat-closeout:no-closeout:chat:abc'
    tid = plugin.file_card('stuck', 'stuck', key)
    led.flag_once(key, 1)
    led.write('chat', batch=[], settled_sig='abc')
    plugin.watchdog_tick(now=2000, mono=2000, unseen={}, force=True)
    with connect() as conn:
        assert kb.get_task(conn, tid).status == 'done'
    assert not led.has_flag(key)


def test_new_unseen_episode_does_not_keep_old_incident_open(incident, monkeypatch):
    led, kb, connect = incident
    key, tid = raise_unseen(incident)
    with monkeypatch.context() as patch:
        patch.setattr(kb, 'complete_task', lambda *a, **kw: False)
        plugin.watchdog_tick(now=2000, mono=2000, unseen={}, force=True)
    plugin.watchdog_tick(now=2100, mono=2100, unseen={'chat': 1}, force=True)
    with connect() as conn:
        assert kb.get_task(conn, tid).status == 'done'
    assert not led.has_flag(key)


def test_reappeared_condition_resets_clear_evidence(incident, monkeypatch):
    led, kb, connect = incident
    cards = {'work': {'status': 'done'}}
    sig = plugin.core.closeout_key({'batch': ['work']}, cards)
    key = f'chat-closeout:no-closeout:chat:{sig}'
    tid = plugin.file_card('stuck', 'stuck', key)
    led.flag_once(key, 1)
    led.write('chat', batch=['work'], settled_at=1, settled_sig=sig, done_sig=sig)
    args = dict(unseen={}, force=True, read=lambda _: cards)
    with monkeypatch.context() as patch:
        patch.setattr(kb, 'complete_task', lambda *a, **kw: False)
        plugin.watchdog_tick(now=2000, mono=2000, **args)
    led.write('chat', done_sig=None)
    plugin.watchdog_tick(now=2100, mono=2100, **args)
    led.write('chat', done_sig=sig)
    plugin.watchdog_tick(now=2200, mono=2200, **args)
    with connect() as conn:
        assert '2200' in kb.list_runs(conn, tid)[-1].summary


def test_unknown_card_read_does_not_resolve_incident(incident):
    led, kb, connect = incident
    cards = {'work': {'status': 'done'}}
    sig = plugin.core.closeout_key({'batch': ['work']}, cards)
    key = f'chat-closeout:no-closeout:chat:{sig}'
    tid = plugin.file_card('stuck', 'stuck', key)
    led.flag_once(key, 1)
    led.write('chat', batch=['work'], settled_at=1, settled_sig=sig)
    plugin.watchdog_tick(now=2000, mono=2000, unseen={}, force=True, read=lambda _: {})
    with connect() as conn:
        assert kb.get_task(conn, tid).status == 'ready'
    assert led.has_flag(key)


def test_delivery_receipt_resolves_handoff(incident):
    led, kb, connect = incident
    key = 'chat-closeout:handoff:request'
    tid = plugin.file_card('handoff stuck', 'stuck', key)
    led.flag_once(key, 1)
    conn = plugin.routing._conn(led)
    try:
        conn.execute("INSERT INTO handoffs VALUES (?,?,?,?,?)",
                     ('request', 'chat', 'sig', 1, '{"status":"delivered"}'))
    finally:
        conn.close()
    plugin.watchdog_tick(now=2000, mono=2000, unseen={}, force=True)
    with connect() as conn:
        assert kb.get_task(conn, tid).status == 'done'
        assert 'delivered' in kb.list_runs(conn, tid)[-1].summary
    assert not led.has_flag(key)


def test_recovery_body_names_agent_routes(incident):
    _, kb, connect = incident
    _, tid = raise_unseen(incident)
    with connect() as conn:
        body = kb.get_task(conn, tid).body
    assert 'cross_session read' in body and 'close-out waker' in body
    # Core refuses Kanban workers sending into a human chat: the body must never present that send as a
    # delivery route, only as the refusal that arms FINAL SEND.
    assert 'use cross_session send' not in body.lower()
    assert 'never reaches the chat (core refuses it)' in body and 'FINAL SEND for <chat ref>' in body
    assert body.index('cross_session read') < body.index('FINAL SEND')
    assert 'open the chat' not in body.lower()
    assert 'do not block on the user' in body.lower()


def test_foreign_flag_is_never_reconciled(incident):
    led, kb, connect = incident
    led.flag_once('other-plugin:note', 1)
    led.flag_once('chat-closeout:unseen', 1)  # malformed: no chat/episode
    plugin.watchdog_tick(now=2000, mono=2000, unseen={}, force=True)
    assert led.has_flag('other-plugin:note') and led.has_flag('chat-closeout:unseen')
