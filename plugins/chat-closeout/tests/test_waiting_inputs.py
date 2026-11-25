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
        # Production boards certify per-task marks; an unvouched fixture now deliberately falls back fresh.
        from hermes_cli import kanban_activity_revision as rev
        conn.commit()
        rev.ensure_revision(conn, rev.LOCAL_TABLES)
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


def test_host_replica_scope_digest_built_once_per_snapshot(world, monkeypatch):
    watch = plugin.wi.ReplicaWatch()
    watch.ok = True
    watch.path = 'fixture-replica'
    watch.gateways = {'gw': [('default', 1000.0, 300.0)]}
    watch.meta = {'last_ok': 1000.0}
    monkeypatch.setattr(watch, 'refresh', lambda *a, **kw: None)
    plugin._wi['replica'] = watch
    calls = []
    digest = plugin.wi.digest

    def counted(value):
        if value == [['gw', 'default', True]]:
            calls.append(value)
        return digest(value)

    monkeypatch.setattr(plugin.wi, 'digest', counted)
    parts = plugin._input_parts(world['led'].pending(), 1100.0, host=True)
    assert len(parts['row_inputs']) == 3
    assert len(calls) == 1  # shared scope freshness is identical for every row in this snapshot


@pytest.mark.parametrize('now', [999.0, 1000.0, 1300.0, 1901.0])
def test_replica_grouped_keys_equal_existing_values_and_clock_boundaries(now):
    wi = plugin.wi
    watch = wi.ReplicaWatch()
    watch.ok, watch.path = True, 'fixture-replica'
    watch.rows = {('gw', 'a'): ('t_1', 'one', 1000.0, 'raw'),
                  ('gw', 'b'): ('t_2', 'two', None, 'raw')}
    watch.gateways = {'gw': [('default', 1000.0, 300.0)]}
    watch.meta = {'last_ok': '1000', 'offline_since': ''}
    groups = [None, {'t_1'}, {'t_2'}, set()]
    expected = []
    # Independent pre-change oracle: preserve the exact portable key shape and freshness boundaries.
    for tails in groups:
        own = {k: v for k, v in watch.rows.items() if tails is None or v[0] in tails}
        expected.append([
            wi.digest(sorted([gw, key, d] for (gw, key), (_t, d, _o, _r) in own.items())),
            wi.digest(sorted([gw, key, wi._fresh(o, now, wi.REPLICA_FRESH_S)]
                             for (gw, key), (_t, _d, o, _r) in own.items() if o is not None)),
            wi.digest([['gw', 'default', wi._fresh(1000.0, now, 300.0)]]),
            [None, False, 0 <= now - 1000.0, now - 1000.0 <= wi.REPLICA_FRESH_S,
             now - 1000.0 > wi.HUB_STALE_AFTER_S],
        ])
    actual = watch.keys(now, tail_sets=groups)
    assert actual == expected
    watch.rows[('gw', 'a')] = ('t_1', 'changed', 1000.0, 'raw')
    assert actual == expected  # published snapshot is not a view of mutable watch state
    changed = watch.keys(now, tail_sets=groups)
    assert changed[0] != actual[0] and changed[1] != actual[1]
    assert changed[2:] == actual[2:]
    actual[0][3][0] = 'caller mutation'
    assert actual[1][3][0] is None  # sibling verdict lists are not shared
    watch.path = None
    assert watch.keys(now, tail_sets=groups) == [['no-replica']] * len(groups)
    watch.ok = False
    with pytest.raises(LookupError):
        watch.keys(now, tail_sets=groups)


def poll():
    """One sidebar poll past the 3 s answer cache."""
    plugin._state.pop('waiting_cache', None)
    return plugin.waiting_source()


def reset(work):
    work['reconcile_done'].clear()
    work['read_cards'] = 0


def test_interleaved_scope_memos_skip_card_reads(world, monkeypatch):
    scope = ['a']
    monkeypatch.setattr(ksa, '_origin_scope', lambda: ('example', scope[0]))
    for name in ('a', 'b'):
        scope[0] = name
        plugin.waiting_inputs()
        assert poll() == {'c1': ['t_1'], 'c2': ['t_2']}
    reset(world['work'])
    for name in ('a', 'b'):
        scope[0] = name
        plugin.waiting_inputs()
        assert poll() == {'c1': ['t_1'], 'c2': ['t_2']}
    assert world['work'] == {'reconcile_done': [], 'read_cards': 0}


def test_other_scope_does_not_adopt_three_second_answer(world, monkeypatch):
    scope = ['a']
    monkeypatch.setattr(ksa, '_origin_scope', lambda: ('example', scope[0]))
    plugin.waiting_inputs()
    assert plugin.waiting_source() == {'c1': ['t_1'], 'c2': ['t_2']}
    with sqlite3.connect(world['board']) as conn:
        conn.execute("UPDATE tasks SET status = 'running' WHERE id = 't_2'")
    scope[0] = 'b'
    plugin._inputs_local.hit = None
    assert plugin.waiting_source() == {'c1': ['t_1']}


def test_scope_switch_discards_same_thread_inputs(world, monkeypatch):
    scope = ['a']
    monkeypatch.setattr(ksa, '_origin_scope', lambda: ('example', scope[0]))
    plugin.waiting_inputs()
    scope[0] = 'b'
    hit = plugin._own_inputs_hit(pop=True)
    assert hit is None


def test_waiting_partitions_are_bounded_lru_and_reset(world, monkeypatch):
    scope = ['0']
    monkeypatch.setattr(ksa, '_origin_scope', lambda: ('example', scope[0]))
    for index in range(33):
        scope[0] = str(index)
        plugin.waiting_inputs()
        plugin.waiting_source()
    for name in ('waiting_cache', 'waiting_memo', 'waiting_rows'):
        assert len(plugin._state[name]) == 32
        assert ('example', '0') not in plugin._state[name]
    scope[0] = '1'
    plugin.waiting_inputs()
    plugin.waiting_source()
    scope[0] = '33'
    plugin.waiting_inputs()
    plugin.waiting_source()
    for name in ('waiting_cache', 'waiting_memo', 'waiting_rows'):
        assert ('example', '1') in plugin._state[name]
        assert ('example', '2') not in plugin._state[name]
    plugin._wi_reset()
    assert all(name not in plugin._state for name in ('waiting_cache', 'waiting_memo', 'waiting_rows'))


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


@pytest.mark.parametrize('error_type', [
    plugin.wi.Unvouched,
    LookupError,
    sqlite3.OperationalError,
], ids=['Unvouched', 'LookupError', 'OperationalError'])
def test_a_real_read_failure_still_counts_as_an_error(world, monkeypatch, error_type):
    """Only Unportable is a fallback; other input failures escape through accounting unchanged."""
    la = pytest.importorskip('hermes_cli.loop_accounting')
    failure = error_type('waiting input cannot be read')

    def broken(rows, now):
        assert {row['session_id'] for row in rows} == {'c1', 'c2', 'c3'}
        raise failure

    monkeypatch.setattr(plugin, '_input_parts', broken)
    before = _acct(la, 'chat-closeout.waiting_inputs')
    with pytest.raises(error_type) as caught:
        plugin.waiting_inputs()
    assert caught.value is failure
    after = _acct(la, 'chat-closeout.waiting_inputs')
    assert after['runs'] - before['runs'] == 1
    assert after['errors'] - before['errors'] == 1
    assert after['fallbacks'] - before['fallbacks'] == 0
    if 'error_kinds' in (la.counters().get('chat-closeout.waiting_inputs') or {}):
        kind = error_type.__name__
        assert after['error_kinds'].get(kind, 0) - before['error_kinds'].get(kind, 0) == 1


def _remote(**over):
    return {'id': 't_r', 'key': 'owner/work/build/t_r', 'gateway': 'owner', 'profile': 'work', 'board': 'build',
            'remote': True, 'via': 'origin', 'binding_id': 'b1', 'status': 'done', 'unfinished': False, 'stale': False, 'synced_at': 100.0, **over}


def _nudge(world, at):
    """Move one declared input (c3's transcript) so the next poll really runs a pass."""
    with sqlite3.connect(world['state']) as conn:
        conn.execute("INSERT INTO messages (session_id, role, content, timestamp, active) "
                     "VALUES ('c3', 'user', 'hello', ?, 1)", (at,))


def test_a_remote_cards_sync_stamp_alone_re_reconciles_nothing(world, monkeypatch):
    """``synced_at`` follows every hub pull; settlement reads the ``stale`` verdict, never the stamp (hub,
    2026-11-22: the stamp re-reconciled about 10 rows on every pass)."""
    remote = _remote()
    monkeypatch.setattr(plugin, 'remote_cards', lambda: [dict(remote)])
    world['led'].write('other', batch=[remote['key']], open=[])
    first = poll()
    assert first == {'c1': ['t_1'], 'c2': ['t_2'], 'other': [remote['key']]}
    reset(world['work'])
    remote['synced_at'] = 250.0
    _nudge(world, 200)
    assert poll() == first  # the same answer
    assert world['work']['reconcile_done'] == ['c3']  # the pass ran (a card read), and only c3's row moved
    assert world['work']['read_cards'] > 0
    for field, moved in (('unfinished', True), ('status', 'blocked')):  # fields settlement reads stay in the key
        kept = remote[field]
        for at, value in ((210, moved), (211, kept)):
            reset(world['work'])
            remote[field] = value
            _nudge(world, at if field == 'unfinished' else at + 10)
            poll()
            assert 'other' in world['work']['reconcile_done'], field
    assert poll() == first
    reset(world['work'])
    remote['stale'] = True  # a verdict settlement does read
    _nudge(world, 201)
    assert poll() == {'c1': ['t_1'], 'c2': ['t_2']}
    assert sorted(world['work']['reconcile_done']) == ['c3', 'other']


def test_an_unlink_observation_clock_re_reconciles_nothing_and_a_removal_does(world):
    """The waker advances an observed entry's clocks on every healthy pass (``core.track_unlinked``); settlement
    reads only the removed entries (``core.unlinked_removed``)."""
    entry = {'at': 1.0, 'mono': 1.0, 'pid': 1, 'clocks': {}, 'observed': 5.0, 'since': 1.0, 'found': True}
    world['led'].write('c1', unlinked={'owner/work/build/t_x': entry})
    first = poll()
    assert first == {'c1': ['t_1'], 'c2': ['t_2']}
    key = plugin.waiting_inputs()
    reset(world['work'])
    world['led'].write('c1', unlinked={'owner/work/build/t_x': {**entry, 'at': 9.0, 'mono': 9.0, 'observed': 13.0}})
    assert plugin.waiting_inputs() == key
    assert poll() == first
    assert world['work'] == {'reconcile_done': [], 'read_cards': 0}
    _nudge(world, 200)  # a pass that does run still keeps c1's row
    assert poll() == first
    assert world['work']['reconcile_done'] == ['c3']
    reset(world['work'])
    world['led'].write('c1', unlinked={'owner/work/build/t_x': {'removed': 50.0, 'since': 1.0, 'found': True}})
    assert plugin.waiting_inputs() != key
    assert poll() == first
    assert world['work']['reconcile_done'] == ['c1']


def test_settlement_reads_nothing_the_waiting_row_key_leaves_out(world, monkeypatch):
    """Parity: the answer computed from full rows and cards equals the one computed with the left-out stamps and
    clocks changed, for every row at once (no cache)."""
    remote = _remote()
    monkeypatch.setattr(plugin, 'remote_cards', lambda: [dict(remote)])
    entry = {'at': 1.0, 'mono': 1.0, 'pid': 1, 'clocks': {}, 'observed': 5.0, 'since': 1.0, 'found': True}
    gone = {'removed': 50.0, 'since': 1.0, 'found': True}
    world['led'].write('other', batch=[remote['key']], open=[], unlinked={'owner/work/build/t_x': entry})
    world['led'].write('c2', unlinked={'owner/work/build/t_y': gone, 'owner/work/build/t_z': entry})

    def full():
        reader = plugin._pass_reader(light=True)
        return core.pending_closeouts(world['led'], reader, proof=plugin.session_proof,
                                      fields=core.SETTLE_ROW_FIELDS)

    before = full()
    remote['synced_at'] = 999.0
    moved = {**entry, 'at': 9.0, 'mono': 9.0, 'pid': 2, 'clocks': {'2': {'at': 9.0}}, 'observed': 13.0}
    world['led'].write('other', unlinked={'owner/work/build/t_x': moved})
    world['led'].write('c2', unlinked={'owner/work/build/t_y': gone, 'owner/work/build/t_z': moved})
    assert full() == before
    assert before == {'c1': ['t_1'], 'c2': ['t_2'], 'other': [remote['key']]}
