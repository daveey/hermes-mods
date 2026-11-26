"""Disposable attribution fixture, not a performance acceptance or production patch."""
import json
import sqlite3
import time

import pytest
from test_closeout import plugin, core
from test_remote_completion_receipt_closeout import world, T0
from test_host_idle_skip import Clock


@pytest.mark.parametrize('change', ['warm', 'unrelated_board', 'unrelated_replica', 'lineage', 'lineage_unrelated_board', 'lineage_unrelated_replica', 'selected_settlement'])
def test_actual_waiting_materialization(world, tmp_path, monkeypatch, change):
    from hermes_cli import kanban_session_activity as ksa
    from hermes_cli import kanban_activity_revision as rev
    from hermes_state import SessionDB
    clock = Clock(T0 + 1)
    monkeypatch.setattr(plugin, 'time', clock)
    state = tmp_path / 'state.db'
    db = SessionDB(db_path=state)
    db.create_session('chat', source='cli')
    db.close()
    with sqlite3.connect(world['local']) as conn:
        conn.execute('ALTER TABLE tasks ADD COLUMN block_kind TEXT')
        conn.execute('ALTER TABLE tasks ADD COLUMN completed_at REAL')
        conn.execute("INSERT INTO tasks VALUES ('unrelated', 'other', 'running', NULL, NULL, NULL)")
        conn.commit()
        rev.ensure_revision(conn, rev.LOCAL_TABLES)
    monkeypatch.setattr(plugin, 'state_dbs', lambda: [state])
    monkeypatch.setattr(plugin, '_compression_tip', lambda sid: sid)
    monkeypatch.setattr(plugin, 'session_proof', lambda *a: False)
    monkeypatch.delenv('CHAT_CLOSEOUT_OFF', raising=False)
    rows = plugin.remote_cards()
    assert len(rows) == 1 and rows[0]['stale'] is False
    key = rows[0]['key']
    led = core.Ledger(tmp_path / 'closeout.db')
    led.write('chat', batch=[key], open=[])
    monkeypatch.setattr(plugin, 'ledger', lambda: led)
    plugin._wi_reset()
    metrics = {}
    def spy(owner, name):
        original = getattr(owner, name)
        def call(*args, **kwargs):
            start = time.thread_time()
            try:
                out = original(*args, **kwargs)
                entry = metrics.setdefault(name, {'calls': 0, 'cpu_s': 0.0})
                entry['calls'] += 1
                if name == 'read_cards':
                    entry['readable'] = getattr(out, 'readable', True)
                return out
            finally:
                entry = metrics.setdefault(name, {'calls': 0, 'cpu_s': 0.0})
                entry['cpu_s'] += time.thread_time() - start
        monkeypatch.setattr(owner, name, call)
    for name in ('remote_cards', 'read_cards', '_gate_project', '_gate_hit', '_gate_fast'):
        spy(plugin, name)
    for name in ('_legacy_remote_links', '_project_remote_links'):
        spy(ksa, name)
    try:
        first = plugin.waiting_inputs()
        assert plugin.waiting_source() == {'chat': [key]}
        assert metrics['read_cards']['readable'] is True
        metrics.clear()
        if change in ('unrelated_board', 'lineage_unrelated_board'):
            with sqlite3.connect(world['local']) as conn:
                conn.execute("UPDATE tasks SET title='new unrelated title' WHERE id='unrelated'")
        elif change in ('unrelated_replica', 'lineage_unrelated_replica'):
            with sqlite3.connect(world['replica']) as conn:
                conn.execute('INSERT INTO replica VALUES (?,?,?,?,?,?,?)', ('owner', 'tasks', 'default/other/t_else', 1, 1,
                             json.dumps({'id': 't_else', 'status': 'running', 'title': 'elsewhere'}), int(T0)))
        if change.startswith('lineage'):
            with sqlite3.connect(state) as conn:
                conn.execute("INSERT INTO messages (session_id, role, content, timestamp, active) VALUES ('chat','user','a new message',?,1)", (T0 + 1,))
        elif change == 'selected_settlement':
            with sqlite3.connect(world['replica']) as conn:
                raw = conn.execute("SELECT data FROM replica WHERE gw='owner' AND coll='tasks'").fetchone()[0]
                task = json.loads(raw)
                task['status'] = 'running'
                conn.execute("UPDATE replica SET data=?,version=version+1,seq=seq+1 WHERE gw='owner' AND coll='tasks'", (json.dumps(task),))
        clock.wall += 4
        world['now'][0] = clock.wall
        start = time.thread_time()
        second = plugin.waiting_inputs()
        actual = plugin.waiting_source()
        cpu = time.thread_time() - start
        observed = json.loads(json.dumps(metrics))
        # Independently force the full inventory/read/reconcile path, no memo.
        plugin._wi_reset()
        with plugin._read_memo_lock:
            plugin._read_memo.clear()
        plugin._gate_reset()
        oracle_reader = plugin._pass_reader(light=True, rows=led.pending())
        oracle_cards = oracle_reader([key])
        assert oracle_cards.readable is True
        metadata = {'uncertain': sorted(oracle_cards.uncertain), 'absence_veto': sorted(oracle_cards.absence_veto)}
        oracle = core.pending_closeouts(led, oracle_reader, proof=plugin.session_proof, rows=led.pending(), fields=core.SETTLE_ROW_FIELDS)
        assert actual == oracle
        assert actual == ({} if change == 'selected_settlement' else {'chat': [key]})
        if change in ('warm', 'unrelated_board', 'unrelated_replica'):
            assert first == second
            assert observed.get('remote_cards', {}).get('calls', 0) == 0
        else:
            assert first != second
            assert observed['read_cards']['readable'] is True
        print('MATERIALIZATION ' + json.dumps({'change': change, 'key_moved': first != second, 'metrics': observed,
                                             'cpu_s': cpu, 'answer': actual, 'oracle_metadata': metadata}, sort_keys=True))
    finally:
        plugin._wi_reset()
        with plugin._read_memo_lock:
            plugin._read_memo.clear()
