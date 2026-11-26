"""Actual producer counterexample for missing gateway-membership dependency."""
import json
import sqlite3

import pytest
from test_closeout import plugin, core
from test_remote_completion_receipt_closeout import world, T0


def test_removed_owner_membership_changes_waiting_key(world, tmp_path, monkeypatch):
    from test_host_idle_skip import Clock
    from hermes_state import SessionDB
    from hermes_cli import kanban_activity_revision as rev
    clock = Clock(T0 + 1)
    monkeypatch.setattr(plugin, 'time', clock)
    db = SessionDB(db_path=tmp_path / 'state.db')
    db.create_session('chat', source='cli')
    db.close()
    with sqlite3.connect(world['local']) as conn:
        conn.execute('ALTER TABLE tasks ADD COLUMN block_kind TEXT')
        conn.execute('ALTER TABLE tasks ADD COLUMN completed_at REAL')
        conn.commit()
        rev.ensure_revision(conn, rev.LOCAL_TABLES)
    monkeypatch.setattr(plugin, 'state_dbs', lambda: [tmp_path / 'state.db'])
    monkeypatch.setattr(plugin, '_compression_tip', lambda sid: sid)
    monkeypatch.setattr(plugin, 'session_proof', lambda *a: False)
    monkeypatch.delenv('CHAT_CLOSEOUT_OFF', raising=False)
    # The observed completion is durable in origin-ledger; the Board removed owner.
    with sqlite3.connect(world['replica']) as conn:
        conn.execute("DELETE FROM replica WHERE gw='owner'")
        conn.execute('INSERT INTO replica VALUES (?,?,?,?,?,?,?)', ('remaining', 'gateway', 'info', 1, 1,
                     json.dumps({'gateway': 'remaining', 'task_scopes': []}), int(T0)))
    plugin._gate_reset()
    cards = plugin.remote_cards()
    assert len(cards) == 1 and cards[0]['stale'] is False and core.is_settled(cards[0])
    key = cards[0]['key']
    led = core.Ledger(tmp_path / 'closeout.db')
    led.write('chat', batch=[key], open=[])
    monkeypatch.setattr(plugin, 'ledger', lambda: led)
    plugin._wi_reset()
    try:
        before = plugin.waiting_inputs()
        assert plugin.waiting_source() == {'chat': [key]}
        # No selected tail, gateway/info heartbeat, origin receipt or status changed.
        # Owner name is listed again, which withdraws the removed-owner final word.
        with sqlite3.connect(world['replica']) as conn:
            conn.execute('INSERT INTO replica VALUES (?,?,?,?,?,?,?)', ('owner', 'tasks', 'work/build/t_unrelated', 1, 2,
                         json.dumps({'id': 't_unrelated', 'status': 'running'}), int(T0)))
        clock.wall += 4
        world['now'][0] = clock.wall
        after = plugin.waiting_inputs()
        actual = plugin.waiting_source()
        plugin._wi_reset()
        plugin._gate_reset()
        fresh = plugin.remote_cards()
        assert fresh[0]['stale'] is True and not core.is_settled(fresh[0])
        oracle = plugin.waiting_source()
        print('MEMBERSHIP', {'key_moved': before != after, 'actual': actual, 'oracle': oracle})
        assert before != after
        assert actual == oracle == {}
    finally:
        plugin._wi_reset()


@pytest.mark.parametrize('coll,record_key,changes', [
    ('tasks', 'default/other/t_unrelated', True),
    ('gateway', 'info', True),
    ('gateway', 'non-info', True),
    ('other', 'info', False),
])
def test_replica_membership_key_insert_delete_and_reuse(tmp_path, coll, record_key, changes):
    path = tmp_path / 'replica.db'
    with sqlite3.connect(path) as conn:
        conn.executescript('CREATE TABLE replica(gw TEXT,coll TEXT,key TEXT,seq INTEGER,data TEXT,PRIMARY KEY(gw,coll,key));'
                           'CREATE INDEX replica_poll_metadata ON replica(coll,gw,seq);'
                           'CREATE TABLE meta(k TEXT PRIMARY KEY,v TEXT);'
                           "INSERT INTO meta VALUES('last_ok','1000');"
                           "INSERT INTO replica VALUES('existing','tasks','default/board/t_selected',1,'{}');")
    watch = plugin.wi.ReplicaWatch()
    def read(token):
        watch.refresh(path, {'t_selected'}, core.ro_connect, token)
        assert watch.ok
        return watch.keys(1001, tail_sets=[None, {'t_selected'}, set()])
    first = read(1)
    assert watch.listed == frozenset({'existing'})
    with sqlite3.connect(path) as conn:
        # Malformed payload still lists the gateway. Finality tests membership,
        # not whether the task/info can be parsed or has a selected tail.
        conn.execute('INSERT INTO replica VALUES(?,?,?,?,?)', ('new', coll, record_key, 2, 'invalid-json'))
    second = read(2)
    assert (first != second) is changes
    if changes:
        assert watch.listed == frozenset({'existing', 'new'})
        assert all(a[4] != b[4] for a, b in zip(first, second))
        if coll != 'gateway' or record_key != 'info':
            assert all(a[:4] == b[:4] for a, b in zip(first, second))
    assert read(2) == second  # unchanged certified observer token: no DB reread
    with sqlite3.connect(path) as conn:
        conn.execute('DELETE FROM replica WHERE gw=?', ('new',))
    assert read(3) == first
    assert watch.listed == frozenset({'existing'})
    watch.clear()
    assert watch.listed == frozenset()
    assert not watch.ok
