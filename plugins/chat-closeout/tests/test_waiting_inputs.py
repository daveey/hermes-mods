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


def test_a_waker_bookkeeping_write_recomputes_nothing(world):
    """The waker's claims and retries are no settlement input (``core.SETTLE_ROW_FIELDS``)."""
    assert poll() == {'c1': ['t_1'], 'c2': ['t_2']}
    key = plugin.waiting_inputs()
    reset(world['work'])
    world['led'].write('c1', failure_since=50.0, retry_after=999.0)
    assert plugin.waiting_inputs() == key
    assert poll() == {'c1': ['t_1'], 'c2': ['t_2']}
    assert world['work']['reconcile_done'] == []


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


# ---- review t_01a1d6d33d3cb0d4: inputs settlement reads that the keys must also cover ---------------------------
def _notice(state, sid, ts):
    with sqlite3.connect(state) as conn:
        for role, text, at, finish in (('user', '✔ [default] @default Kanban t_1 done — local', ts, None),
                                       ('assistant', 'Result checked.', ts + 1, 'stop')):
            conn.execute('INSERT INTO messages (session_id, role, content, timestamp, finish_reason, active) '
                         'VALUES (?,?,?,?,?,1)', (sid, role, text, at, finish))


def test_a_timestamp_repair_that_proves_an_ack_clears_the_waiting_row(world):
    """No trigger covers ``messages.timestamp``: the watermark carries the active rows' timestamp sum, as core
    ``transcript_key`` does, so a repair that moves the notice past ``completed_at`` re-proves the chat."""
    _notice(world['state'], 'c1', 50)  # before completed_at=100: no proof yet
    assert poll() == {'c1': ['t_1'], 'c2': ['t_2']}
    key = plugin.waiting_inputs()
    reset(world['work'])
    with sqlite3.connect(world['state']) as conn:
        conn.execute("UPDATE messages SET timestamp = timestamp + 100 WHERE session_id = 'c1'")
    assert plugin.waiting_inputs() != key
    assert poll() == {'c2': ['t_2']}
    assert world['work']['reconcile_done'] == ['c1']


def test_an_active_flip_below_the_max_moves_the_key(world):
    _notice(world['state'], 'c1', 50)
    with sqlite3.connect(world['state']) as conn:
        conn.execute("INSERT INTO messages (session_id, role, content, timestamp, active) "
                     "VALUES ('c1', 'user', 'later', 60, 1)")
    key = plugin.waiting_inputs()
    with sqlite3.connect(world['state']) as conn:
        first = conn.execute("SELECT MIN(id) FROM messages WHERE session_id = 'c1'").fetchone()[0]
        conn.execute('UPDATE messages SET active = 0 WHERE id = ?', (first,))
    assert plugin.waiting_inputs() != key


def test_a_state_db_that_cannot_vouch_for_rewrites_gives_no_key(world):
    """Without core's transcript-revision triggers an in-place rewrite moves nothing: no key (no adoption, no
    per-row reuse), like ``transcript_key`` returning None."""
    with sqlite3.connect(world['state']) as conn:
        conn.execute('DROP TRIGGER messages_transcript_revision_update')
    with pytest.raises(Exception):
        plugin.waiting_inputs()
    assert poll() == {'c1': ['t_1'], 'c2': ['t_2']}
    reset(world['work'])
    assert poll() == {'c1': ['t_1'], 'c2': ['t_2']}
    assert sorted(world['work']['reconcile_done']) == ['c1', 'c2', 'c3']  # a full pass every time


def test_a_floor_rollover_re_reconciles_every_row(world, monkeypatch):
    """The 10-minute floor reaches the per-row answers, not only the whole-pass memo."""
    assert poll() == {'c1': ['t_1'], 'c2': ['t_2']}
    reset(world['work'])
    assert poll() == {'c1': ['t_1'], 'c2': ['t_2']}
    assert world['work']['reconcile_done'] == []
    monkeypatch.setattr(plugin.wi, 'FLOOR_S', 1e-9)  # every call a new floor bucket
    assert poll() == {'c1': ['t_1'], 'c2': ['t_2']}
    assert sorted(world['work']['reconcile_done']) == ['c1', 'c2', 'c3']


def test_a_floor_rollover_re_judges_quiet_host_rows(world, monkeypatch):
    world['led'].write('c1', batch=[], open=[])
    world['led'].write('c2', batch=[], open=[])
    wake = lambda sid, *a, **k: 'busy'  # noqa: E731
    monkeypatch.setattr(plugin, '_unseen_counts', lambda: {})
    plugin.wake_tick(wake=wake, last_user=lambda _: 0)
    plugin._state.pop('host_memo', None)  # past the whole-pass skip: the per-row quiet cache alone
    reset(world['work'])
    plugin.wake_tick(wake=wake, last_user=lambda _: 0)
    assert world['work']['reconcile_done'] == []
    plugin._state.pop('host_memo', None)
    monkeypatch.setattr(plugin.wi, 'FLOOR_S', 1e-9)
    plugin.wake_tick(wake=wake, last_user=lambda _: 0)
    assert world['work']['reconcile_done'] == ['c3']


def test_replica_batch_row_rewritten_at_its_old_seq_moves_the_key(world, tmp_path, monkeypatch):
    hub = tmp_path / 'hub.db'
    _replica(hub, [('default/default/t_1', {'id': 't_1', 'status': 'done'}),
                   ('default/default/t_9', {'id': 't_9', 'status': 'running'})])
    monkeypatch.setattr(plugin, 'hub_db_path', lambda: hub)
    key = plugin.waiting_inputs()
    with sqlite3.connect(hub) as conn:  # a resync rewrites t_1 in place, keeping its seq
        conn.execute("UPDATE replica SET data = ? WHERE key = 'default/default/t_1'",
                     (json.dumps({'id': 't_1', 'status': 'running'}),))
    assert plugin.waiting_inputs() != key


def test_replica_batch_row_with_a_null_seq_moves_the_key(world, tmp_path, monkeypatch):
    hub = tmp_path / 'hub.db'
    _replica(hub, [('default/default/t_9', {'id': 't_9', 'status': 'running'})])
    monkeypatch.setattr(plugin, 'hub_db_path', lambda: hub)
    key = plugin.waiting_inputs()
    with sqlite3.connect(hub) as conn:
        conn.execute("INSERT INTO replica VALUES ('worker-1', 'tasks', 'default/default/t_1', NULL, ?)",
                     (json.dumps({'id': 't_1', 'status': 'done'}),))
    assert plugin.waiting_inputs() != key


def test_replica_batch_row_of_a_gateway_without_info_moves_the_key(world, tmp_path, monkeypatch):
    hub = tmp_path / 'hub.db'
    _replica(hub, [('default/default/t_9', {'id': 't_9', 'status': 'running'})])
    monkeypatch.setattr(plugin, 'hub_db_path', lambda: hub)
    key = plugin.waiting_inputs()
    with sqlite3.connect(hub) as conn:  # a new gateway, no gateway/info row, writes a batch card at an old seq
        conn.execute("INSERT INTO replica VALUES ('worker-9', 'tasks', 'default/default/t_1', 0, ?)",
                     (json.dumps({'id': 't_1', 'status': 'done'}),))
    assert plugin.waiting_inputs() != key


def test_a_fresh_fingerprint_is_never_answered_from_the_older_cache(world):
    """Core takes a fingerprint, then runs the source on the same thread: inputs that moved since the 3 s answer
    cache was filled are answered now, not under the newer key with the older answer."""
    assert poll() == {'c1': ['t_1'], 'c2': ['t_2']}
    _notice(world['state'], 'c1', 101)
    plugin.waiting_inputs()
    assert plugin.waiting_source() == {'c2': ['t_2']}  # no waiting_cache pop: within the 3 s window


def test_another_threads_inputs_are_not_reused(world):
    import threading
    assert poll() == {'c1': ['t_1'], 'c2': ['t_2']}
    t = threading.Thread(target=plugin.waiting_inputs)
    t.start()
    t.join()
    _notice(world['state'], 'c1', 101)
    assert poll() == {'c2': ['t_2']}  # not the other thread's (older) reads


def _acct(la, name):
    """A copy of ``name``'s P1 counters (zeros when the pass has not run yet); ``error_kinds`` only on cores that
    record it (``getattr``-style: an older core's counters have no such key)."""
    got = dict(la.counters().get(name) or {})
    base = {'runs': 0, 'errors': 0, 'fallbacks': 0, 'error_kinds': {}}
    base.update(got)
    base['error_kinds'] = dict(base.get('error_kinds') or {})
    return base


def test_an_unreadable_board_key_counts_as_a_fallback_not_an_error(world, monkeypatch):
    """A zero-budget BoardLifecycleBusy read leaves core's board key ``("stat", stat, object())``: ``portable``
    raises the designed no-adoption signal ``Unportable``. The P1 pass counts it as a fallback, never an error, and
    the caller (core's annotation host) still receives ``Unportable`` so it skips adoption and runs the source."""
    la = pytest.importorskip('hermes_cli.loop_accounting')
    wi = plugin.wi
    from hermes_cli.kanban_generation import BoardLifecycleBusy
    real_ro = ksa.cached_ro

    def busy_ro(path, *, busy_budget=0.5):
        if busy_budget == 0:  # core's zero-budget board-key probe meets a lifecycle hold
            raise BoardLifecycleBusy('board lifecycle busy')
        return real_ro(path, busy_budget=busy_budget)

    monkeypatch.setattr(ksa, 'cached_ro', busy_ro)
    key = ksa._board_content_key(world['board'])
    assert key[0] == 'stat' and len(key) == 3  # the real unreadable-board key ("stat", stat, object())
    before = _acct(la, 'chat-closeout.waiting_inputs')
    with pytest.raises(wi.Unportable):
        plugin.waiting_inputs()
    after = _acct(la, 'chat-closeout.waiting_inputs')
    assert after['runs'] - before['runs'] == 1
    assert after['errors'] - before['errors'] == 0
    assert after['fallbacks'] - before['fallbacks'] == 1
    assert after['error_kinds'] == before['error_kinds']
    # The source still answers through its own (full) pass.
    assert poll() == {'c1': ['t_1'], 'c2': ['t_2']}


def test_a_real_read_failure_still_counts_as_an_error(world, monkeypatch):
    """Any other exception escapes inside the pass: an error (with its class where core records it), not a fallback."""
    la = pytest.importorskip('hermes_cli.loop_accounting')

    def broken():
        raise sqlite3.OperationalError('database is locked')

    monkeypatch.setattr(plugin, '_pending_rows', broken)
    before = _acct(la, 'chat-closeout.waiting_inputs')
    with pytest.raises(sqlite3.OperationalError):
        plugin.waiting_inputs()
    after = _acct(la, 'chat-closeout.waiting_inputs')
    assert after['runs'] - before['runs'] == 1
    assert after['errors'] - before['errors'] == 1
    assert after['fallbacks'] - before['fallbacks'] == 0
    if 'error_kinds' in (la.counters().get('chat-closeout.waiting_inputs') or {}):
        assert after['error_kinds'].get('OperationalError', 0) - before['error_kinds'].get('OperationalError', 0) == 1
