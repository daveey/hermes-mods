"""Declared waiting-source inputs and incremental ``pending_closeouts`` (WAITING-INPUTS.md; design t_01a1d563 row C).

RED tests (card C): an ack landing in a transcript clears the waiting row; a batch card changing re-reconciles only
that row; unchanged inputs recompute nothing (and the host pass skips). Real core (``HERMES_CORE_SOURCE``), real
state.db (``hermes_state.SessionDB``), real ledger and board files.
"""
import json
import sqlite3
import sys

import pytest
from test_closeout import plugin, core

hermes_state = pytest.importorskip('hermes_state')
ksa = pytest.importorskip('hermes_cli.kanban_session_activity')


@pytest.fixture
def world(tmp_path, monkeypatch):
    """Chats c1, c2, c3 on one local board: t_1, t_2 done (no close-out yet), t_3 running. Counts the work a pass does."""
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    monkeypatch.delenv('CHAT_CLOSEOUT_OFF', raising=False)
    db = hermes_state.SessionDB(db_path=tmp_path / 'state.db')
    for sid in ('c1', 'c2', 'c3', 'other'):  # other: a chat with no ledger row
        db.create_session(sid, source='cli')
    db.close()
    board = tmp_path / 'kanban.db'
    with sqlite3.connect(board) as conn:
        conn.execute('CREATE TABLE tasks (id TEXT, title TEXT, status TEXT, block_kind TEXT, completed_at REAL)')
        conn.executemany('INSERT INTO tasks VALUES (?,?,?,?,?)', [
            ('t_1', 'one', 'done', None, 100.0), ('t_2', 'two', 'done', None, 100.0),
            ('t_3', 'three', 'running', None, None)])
    led = core.Ledger(tmp_path / 'ledger.db')
    led.write('c1', batch=['t_1'], open=[])
    led.write('c2', batch=['t_2'], open=[])
    led.write('c3', batch=['t_3'], open=['t_3'])
    monkeypatch.setattr(plugin, 'ledger', lambda: led)
    monkeypatch.setattr(plugin, 'board_paths', lambda: [board])
    monkeypatch.setattr(plugin, 'state_dbs', lambda: [tmp_path / 'state.db'])
    monkeypatch.setattr(plugin, 'remote_cards', lambda: [])
    monkeypatch.setattr(plugin, 'hub_db_path', lambda: None)
    monkeypatch.setattr(plugin, '_compression_tip', lambda sid: sid)
    getattr(plugin, '_wi_reset', lambda: None)()
    work = {'reconcile_done': [], 'read_cards': 0}
    real_done, real_read = core.reconcile_done, plugin.read_cards

    def reconcile_done(row, cards, proof=None):
        work['reconcile_done'].append(row['session_id'])
        return real_done(row, cards, proof)

    def read_cards(*a, **kw):
        work['read_cards'] += 1
        return real_read(*a, **kw)

    monkeypatch.setattr(core, 'reconcile_done', reconcile_done)
    monkeypatch.setattr(plugin, 'read_cards', read_cards)
    yield {'board': board, 'state': tmp_path / 'state.db', 'work': work, 'led': led}
    getattr(plugin, '_wi_reset', lambda: None)()


def poll():
    """One sidebar poll past the 3 s answer cache."""
    plugin._state.pop('waiting_cache', None)
    return plugin.waiting_source()


def reset(work):
    work['reconcile_done'].clear()
    work['read_cards'] = 0


def test_inputs_are_declared_and_portable(world):
    key = plugin.waiting_inputs()
    assert plugin.waiting_inputs() == key
    json.dumps(key)  # plain JSON: another process can compare it


def test_unchanged_inputs_recompute_nothing(world):
    assert poll() == {'c1': ['t_1'], 'c2': ['t_2']}
    assert sorted(world['work']['reconcile_done']) == ['c1', 'c2', 'c3']
    reset(world['work'])
    for _ in range(3):
        assert poll() == {'c1': ['t_1'], 'c2': ['t_2']}
    assert world['work'] == {'reconcile_done': [], 'read_cards': 0}


def test_an_ack_landing_in_the_transcript_clears_the_waiting_row(world):
    assert poll() == {'c1': ['t_1'], 'c2': ['t_2']}
    key = plugin.waiting_inputs()
    reset(world['work'])
    with sqlite3.connect(world['state']) as conn:
        for role, text, at, finish in (('user', '✔ [default] @default Kanban t_1 done — local', 101, None),
                                       ('assistant', 'Result checked.', 102, 'stop')):
            conn.execute('INSERT INTO messages (session_id, role, content, timestamp, finish_reason, active) '
                         'VALUES (?,?,?,?,?,1)', ('c1', role, text, at, finish))
    assert plugin.waiting_inputs() != key  # the lineage watermark moved
    assert poll() == {'c2': ['t_2']}
    assert world['work']['reconcile_done'] == ['c1']  # only the chat whose transcript moved


def test_a_batch_card_changing_re_reconciles_only_that_row(world):
    assert poll() == {'c1': ['t_1'], 'c2': ['t_2']}
    reset(world['work'])
    with sqlite3.connect(world['board']) as conn:
        conn.execute("UPDATE tasks SET status = 'running', completed_at = NULL WHERE id = 't_2'")
    assert poll() == {'c1': ['t_1']}
    assert world['work']['reconcile_done'] == ['c2']


def test_another_chats_transcript_moving_recomputes_nothing_else(world):
    assert poll() == {'c1': ['t_1'], 'c2': ['t_2']}
    reset(world['work'])
    with sqlite3.connect(world['state']) as conn:
        conn.execute("INSERT INTO messages (session_id, role, content, timestamp, active) "
                     "VALUES ('c3', 'user', 'hello', 200, 1)")
    assert poll() == {'c1': ['t_1'], 'c2': ['t_2']}
    assert world['work']['reconcile_done'] == ['c3']


def test_a_ledger_write_re_reconciles_only_that_row(world):
    assert poll() == {'c1': ['t_1'], 'c2': ['t_2']}
    reset(world['work'])
    world['led'].write('c1', batch=[], open=[])
    assert poll() == {'c2': ['t_2']}
    assert world['work']['reconcile_done'] == []  # c1 left the pending rows; c2 and c3 unchanged


def test_waiting_pass_is_accounted(world, monkeypatch):
    la = pytest.importorskip('hermes_cli.loop_accounting')
    before = dict(la.counters().get('chat-closeout.waiting') or {'runs': 0, 'skips': 0, 'rows': 0})
    poll()
    poll()
    after = la.counters()['chat-closeout.waiting']
    assert after['runs'] - before['runs'] == 2
    assert after['skips'] - before['skips'] == 1
    assert after['rows'] - before['rows'] == 3  # the first (input-moved) pass re-reconciled three rows


def test_host_pass_skips_quiet_rows_and_whole_passes(world, monkeypatch):
    """A working batch is quiet: the next host pass with the same inputs does not judge it again. A card that
    settles re-judges only its own row."""
    world['led'].write('c1', batch=[], open=[])
    world['led'].write('c2', batch=[], open=[])
    woken = []
    wake = lambda sid, *a, **k: woken.append(sid) or 'busy'  # noqa: E731
    monkeypatch.setattr(plugin, '_unseen_counts', lambda: {})
    plugin.wake_tick(wake=wake, last_user=lambda _: 0)
    assert world['work']['reconcile_done'] == ['c3'] and woken == []
    assert plugin._state.get('host_memo') is not None
    reset(world['work'])
    plugin.wake_tick(wake=wake, last_user=lambda _: 0)
    assert world['work'] == {'reconcile_done': [], 'read_cards': 0}  # whole pass skipped
    with sqlite3.connect(world['board']) as conn:
        conn.execute("UPDATE tasks SET status = 'done', completed_at = 300 WHERE id = 't_3'")
    plugin.wake_tick(wake=wake, last_user=lambda _: 0)
    assert world['work']['reconcile_done'] == ['c3'] and woken == ['c3']
    assert plugin._state.get('host_memo') is None  # a row acted: no whole-pass skip next time


def test_a_chat_outside_the_ledger_does_not_move_the_key(world):
    """R11: the watermark is per waiting lineage. A write elsewhere in state.db (the global stat moves) is no input."""
    key = plugin.waiting_inputs()
    with sqlite3.connect(world['state']) as conn:
        conn.execute("INSERT INTO messages (session_id, role, content, timestamp, active) "
                     "VALUES ('other', 'user', 'hello', 200, 1)")
    assert plugin.waiting_inputs() == key


def _replica(path, rows):
    with sqlite3.connect(path) as conn:
        conn.execute('CREATE TABLE IF NOT EXISTS replica (gw TEXT, coll TEXT, key TEXT, seq INTEGER, data TEXT, '
                     'PRIMARY KEY (gw, coll, key))')
        conn.execute('CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT)')
        for seq, (key, data) in enumerate(rows, start=int(conn.execute(
                'SELECT COALESCE(MAX(seq), 0) FROM replica').fetchone()[0]) + 1):
            conn.execute('INSERT OR REPLACE INTO replica VALUES (?,?,?,?,?)',
                         ('worker-1', 'tasks', key, seq, json.dumps(data)))


def test_replica_liveness_only_changes_do_not_move_the_key(world, tmp_path, monkeypatch):
    hub = tmp_path / 'hub.db'
    _replica(hub, [('default/default/t_1', {'id': 't_1', 'status': 'done', 'activity_at': 1.0}),
                   ('default/default/t_other', {'id': 't_other', 'status': 'running'})])
    monkeypatch.setattr(plugin, 'hub_db_path', lambda: hub)
    key = plugin.waiting_inputs()
    _replica(hub, [('default/default/t_1', {'id': 't_1', 'status': 'done', 'activity_at': 2.0,
                                            'activity_kind': 'heartbeat'})])
    assert plugin.waiting_inputs() == key  # liveness stamps only
    _replica(hub, [('default/default/t_other', {'id': 't_other', 'status': 'done'})])
    assert plugin.waiting_inputs() == key  # a card no waiting row holds
    _replica(hub, [('default/default/t_1', {'id': 't_1', 'status': 'archived', 'activity_at': 2.0})])
    assert plugin.waiting_inputs() != key  # a batch card's substantive change


def test_the_source_is_registered_with_its_inputs(monkeypatch):
    import hermes_cli
    seen = {}
    fake = type(sys)('kanban_session_activity')
    fake.register_waiting_source = lambda name, fn, inputs=None: seen.update(name=name, fn=fn, inputs=inputs)
    monkeypatch.setitem(sys.modules, 'hermes_cli.kanban_session_activity', fake)
    monkeypatch.setattr(hermes_cli, 'kanban_session_activity', fake, raising=False)
    plugin._register_waiting_source()
    assert seen == {'name': 'chat-closeout', 'fn': plugin.waiting_source, 'inputs': plugin.waiting_inputs}
