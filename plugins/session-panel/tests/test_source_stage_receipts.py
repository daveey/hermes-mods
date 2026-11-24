"""Real coordinator stage custody; explicit measurement is not activation."""
import threading

import pytest

from test_source_coordinator_core import api, board, kanban_db, readers_for, strict_empty, compose, drain
from test_feed import feedmod


class StageLog:
    def __init__(self, lane):
        self.lane = lane
        self.rows = []
        self.lock = threading.Lock()

    def record(self, event, **data):
        # Encoding/enqueue is not allowed to extend source/store critical sections.
        assert not self.lane.lock._is_owned()
        assert not self.lane.store._lock._is_owned()
        with self.lock:
            self.rows.append(dict(event=event, **data))
        return True

    def events(self, kind):
        return [r for r in self.rows if r['event'] == kind]


def setup(api, board, monkeypatch):
    path, conn = board
    r = readers_for(api, path, monkeypatch)
    monkeypatch.setattr(r, 'external_capture', strict_empty)
    f = feedmod.Feed(r, emit=lambda _: None)
    f.watch(['S'])
    lane = compose(f, r)
    # Only coordinator events; Feed emission custody already has its own tests.
    recorder = StageLog(lane)
    monkeypatch.setattr(lane, '_receipt_log', lambda: recorder, raising=False)
    return conn, r, f, lane, recorder


def test_stage_capture_admission_and_projection_exact_custody(api, board, monkeypatch):
    conn, r, f, lane, log = setup(api, board, monkeypatch)
    task = kanban_db.create_task(conn, title='Scoped work', session_id='S')
    try:
        drain(lane)
        starts, ends = log.events('capture_start'), log.events('capture_end')
        assert starts and {e['capture_attempt_id'] for e in starts} == {e['capture_attempt_id'] for e in ends}
        received = log.events('source_received')
        local = next(e for e in received if e['domain'] == 'local')
        assert local['receipt'].coverage.capture_id
        assert local['receipt'].coverage.captured_demands == local['frozen_demands']
        assert local['current_state']['demands'][local['source']] == local['frozen_demands']
        assert any(k.card_id == task for k in local['changed_keys'])
        assert local['metrics']['sql_calls'] > 0 and local['metrics']['opens'] == 1
        assert local['thread_cpu_ns'] >= 0 and local['elapsed_ns'] > 0
        admissions = log.events('admitted')
        assert admissions and all(e['admission'].first_admitted_mono_ns > 0 for e in admissions)
        accepted = [e for e in admissions if e.get('capture_attempt_id') == local['capture_attempt_id']]
        assert accepted and local['receipt'].coverage.capture_id in accepted[0]['admission'].receipt_ids
        ps, pe = log.events('projection_start'), log.events('projection_end')
        assert ps and {e['projection_attempt_id'] for e in ps} == {e['projection_attempt_id'] for e in pe}
        assert all(e['admission'].id for e in ps)
        assert any(e['disposition'] in {'stored', 'no_change'} for e in pe)
        assert all(e['thread_cpu_ns'] >= 0 for e in pe)
        assert all(e['io_cost'] == dict(available=True, db_opens=0, sql_calls=0, network_calls=0) for e in pe)
        local_end = next(e for e in ends if e['capture_attempt_id'] == local['capture_attempt_id'])
        assert local_end['io_cost']['available'] and local_end['io_cost']['db_opens'] == 1
        assert local_end['io_cost']['sql_calls'] > 0
        # No made-up external SQL/network/no-I/O values: incomplete instrumentation
        # remains explicit rather than a falsely complete zero-cost sample.
        external = next(e for e in received if e['domain'] == 'external')
        assert 'sql_calls' not in external['metrics']
        assert 'network_calls' not in external['metrics']
    finally:
        f.stopped.set(); lane.close(); r.release(); conn.close()


def test_stage_capture_barrier_retains_frozen_demand_and_supersession(api, board, monkeypatch):
    conn, r, f, lane, log = setup(api, board, monkeypatch)
    entered, release = threading.Event(), threading.Event()
    original = r.capture_local
    def blocked(*args, **kwargs):
        entered.set(); assert release.wait(5)
        return original(*args, **kwargs)
    monkeypatch.setattr(r, 'capture_local', blocked)
    try:
        lane.step(); assert entered.wait(2)
        start = next(e for e in log.events('capture_start') if e['domain'] == 'local')
        ref = lane.module.CardKey('remote', 'default', 'other', 'parent')
        lane.require('S', [ref])
        release.set()
        for worker in list(lane.acquiring.values()):
            worker.join(3)
        lane.service()
        end = next(e for e in log.events('capture_end') if e['capture_attempt_id'] == start['capture_attempt_id'])
        assert end['frozen_demands'] == start['frozen_demands']
        assert end['current_state']['demands'][end['source']] != end['frozen_demands']
        assert not log.events('source_error'), 'demand evolution is not a source read failure'
    finally:
        release.set(); f.stopped.set(); lane.close(); r.release(); conn.close()


def test_stage_failed_capture_is_not_a_source_receipt_and_forget_is_exact(api, board, monkeypatch):
    conn, r, f, lane, log = setup(api, board, monkeypatch)
    def unavailable(*args, **kwargs):
        raise RuntimeError('source unavailable')
    monkeypatch.setattr(r, 'capture_local', unavailable)
    try:
        drain(lane)
        failures = [e for e in log.events('source_error') if e['domain'] == 'local']
        assert failures and failures[0]['error'] == 'RuntimeError'
        assert not [e for e in log.events('source_received') if e['domain'] == 'local']
        assert any(e['event'] == 'capture_end' and e['disposition'] == 'error' for e in log.rows)
        watch = lane.store.receipt_state()['watches'][lane.chats['S']]
        with f.lock:
            f._forget('S')
        drain(lane)
        forgotten, = log.events('forgotten')
        assert forgotten['watch'] == watch and forgotten['chat'] == watch.chat
        assert forgotten['current_state']['watches'] == {}
    finally:
        f.stopped.set(); lane.close(); r.release(); conn.close()


def test_receipt_state_is_read_only_even_after_stop(api, board, monkeypatch):
    conn, r, f, lane, log = setup(api, board, monkeypatch)
    try:
        drain(lane)
        state = lane.store.receipt_state()
        again = lane.store.receipt_state()
        assert state == again
        assert state['admissions'] == lane.store._admissions
        lane.store.close(lane.store.epoch)
        stopped = lane.store.receipt_state()
        assert stopped['closed'] and stopped['admissions'] == state['admissions']
    finally:
        f.stopped.set(); lane.close(); r.release(); conn.close()


def test_stage_receipts_encode_real_store_values_and_drain_private_sink(api, board, monkeypatch, tmp_path):
    from test_source_receipts import receipts, identity, read_log
    conn, r, f, lane, recorder = setup(api, board, monkeypatch)
    log = receipts().ReceiptLog(tmp_path / 'real-stages.jsonl', identity=identity())
    monkeypatch.setattr(lane, '_receipt_log', lambda: log)
    try:
        kanban_db.create_task(conn, title='Serialized source evidence', session_id='S')
        drain(lane)
    finally:
        f.stopped.set(); lane.close(); r.release(); conn.close()
        assert log.close(), (log.dropped_records, log.write_errors, log.encoding_errors)
    rows = read_log(log.path)
    assert rows[-1]['event'] == 'window_end' and rows[-1]['dropped_records'] == 0
    assert any(e['event'] == 'source_received' for e in rows)
    assert any(e['event'] == 'admitted' and e['admission']['receipt_ids'] for e in rows)
    assert any(e['event'] == 'projection_end' and e['covered_scopes'] for e in rows)
    assert all(e['coordinator_epoch'] == lane.store.epoch for e in rows[:-1])
    assert receipts().validate_records(rows, require_closed=True)['valid']


def test_projection_no_io_counter_excludes_real_emit_socket_io(api, board, monkeypatch):
    import socket
    conn, r, f, lane, log = setup(api, board, monkeypatch)
    a, b = socket.socketpair()
    def emit(payload):
        assert a.send(b'x') == 1
        assert b.recv(1) == b'x'
    f.emit = emit
    try:
        kanban_db.create_task(conn, title='Projection is pure; emission uses I/O', session_id='S')
        drain(lane)
        ends = log.events('projection_end')
        assert ends and all(e['io_cost']['network_calls'] == 0 for e in ends)
        assert all(e['io_cost']['sql_calls'] == 0 for e in ends)
        assert all(e['total_worker_elapsed_ns'] >= e['elapsed_ns'] for e in ends)
        received = next(e for e in log.events('capture_end') if e['domain'] == 'local')
        assert received['io_cost']['rows_returned'] == received['metrics']['rows_returned']
        assert received['io_cost']['vm_steps'] == received['metrics']['vm_steps']
    finally:
        a.close(); b.close(); f.stopped.set(); lane.close(); r.release(); conn.close()


def test_no_change_is_an_outcome_not_model_inclusion(api, board, monkeypatch):
    conn, r, f, lane, log = setup(api, board, monkeypatch)
    try:
        kanban_db.create_task(conn, title='Same board model', session_id='S')
        drain(lane)
        for _ in range(2):
            lane._admit(['S'], 0)
            drain(lane)
        unchanged = log.events('no_change')
        assert unchanged
        assert all(e['inclusion'] == 'not_established' for e in unchanged)
        assert all(e['admission'].chat == e['chat'] for e in unchanged)
    finally:
        f.stopped.set(); lane.close(); r.release(); conn.close()


def test_projection_superseded_retains_exact_predecessor_and_current_scope(api, board, monkeypatch):
    conn, r, f, lane, log = setup(api, board, monkeypatch)
    try:
        drain(lane)
        log.rows.clear()  # exclude legitimate startup source/projection races
        original = f.publish_projections
        def race(store, projections):
            # Demand evolves after the immutable pin, before the real writer CAS.
            store.require(lane.chats['S'], [lane.module.CardKey('other', 'default', 'default', 'parent')])
            return original(store, projections)
        monkeypatch.setattr(f, 'publish_projections', race)
        lane._admit(['S'], 0)
        lane.service()
        for worker in list(lane.active.values()):
            worker.join(3)
            assert not worker.is_alive()
        superseded = log.events('superseded')
        assert superseded
        old = superseded[0]['admission']
        current = superseded[0]['current_state']['admissions'][old.chat]
        assert old.required_scopes != current.required_scopes
        assert superseded[0]['predecessor_admission_id'] == old.id
        assert superseded[0]['inclusion'] == 'not_established'
    finally:
        f.stopped.set(); lane.close(); r.release(); conn.close()


def test_uninstalled_stage_measurement_adds_no_store_observations(api, board, monkeypatch):
    conn, r, f, lane, log = setup(api, board, monkeypatch)
    monkeypatch.setattr(lane, '_receipt_log', lambda: None)
    def forbidden():
        raise AssertionError('disabled measurement consulted store evidence')
    monkeypatch.setattr(lane.store, 'receipt_state', forbidden)
    try:
        drain(lane)
        assert not log.rows and f.receipt_log is None
        assert lane.store.pin(lane.chats['S'])[1].required_versions
    finally:
        f.stopped.set(); lane.close(); r.release(); conn.close()
