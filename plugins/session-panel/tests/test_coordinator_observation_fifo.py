"""Clock-only observation through coordinator scheduling, and bounded FIFO under arrivals.

Both controls use real CoreReaders (replica SQL / kanban SQLite) and the real
SourceCoordinator. Races are positioned with events/conditions and the
existing ``drain``; no sleeps are used as synchronization. The sidebar
population actually observed in production is NOT established by this file:
64 is the design's required saturation point, not a measured live maximum.
"""
import copy
import threading

import pytest
from test_coordinator_observation import observed
from test_core_readers import api, board, kanban_db
from test_reader_batch import graph
from test_source_coordinator_core import compose, drain, readers_for, strict_empty
from test_feed import feedmod

CHATS = 64
HOT = 8


def _remote(f, s, remote):
    return next(row for row in f.bundles[s]['bundle']['tasks'] if row['id'] == remote)


def test_clock_only_pull_through_coordinator_keeps_semantic_and_execution_clocks(observed, monkeypatch):
    """A later composed scheduling pass (not ``_capture_one``) renews only synced_at."""
    import hermes_cli.kanban_execution_observations as observations
    r, f, lane, c, remote, now = observed
    sessions = ('S0', 'S1')
    before = {s: copy.deepcopy(f.bundles[s]['bundle']) for s in sessions}
    dirty = dict(lane.canonical_dirty)
    # Wall clock moves (within the canonical staleness window, so no legitimate
    # semantic change): a renewed computed or
    # execution clock would now differ from the retained one.
    now[0] += 5.
    forbidden_calls = []
    def forbid(name):
        def fail(*a, **kw):
            forbidden_calls.append(name)
            raise AssertionError(f'clock-only pull reached {name}')
        return fail
    add_cards, renewed = observations.add_cards, []
    def spy(rows, *a, **kw):
        # An empty refresh list (nothing re-read) is a no-op; any row is a renewal.
        renewed.extend(row.get('id') for row in rows)
        return add_cards(rows, *a, **kw)
    monkeypatch.setattr(observations, 'add_cards', spy)
    monkeypatch.setattr(r, '_detail', forbid('_detail'))
    monkeypatch.setattr(r, 'capture_external', forbid('capture_external'))
    monkeypatch.setattr(f, 'project_board', forbid('project_board'))
    raw, original = [], r.external_capture
    def counted(*a, **kw):
        raw.append(1)
        return original(*a, **kw)
    monkeypatch.setattr(r, 'external_capture', counted)
    for stamp in (1800000001., 1799999999.):  # advance, then legitimate regression
        c.execute('UPDATE kanban_sync_state SET pulled_at=?', (stamp,)); c.commit()
        lane.step()
        for worker in list(lane.acquiring.values()) + list(lane.active.values()):
            worker.join(5)
            assert not worker.is_alive()
        lane.service()
        for s in sessions:
            assert _remote(f, s, remote)['synced_at'] == stamp
        assert not lane.pending and not lane.active and not lane.acquiring
        assert lane.canonical_dirty == dirty
    assert not forbidden_calls and not renewed, renewed
    assert len(raw) == 2, 'exactly one union strict read per pull; never per chat'
    for s in sessions:
        after = f.bundles[s]['bundle']
        for key in ('computed_at', 'computed_seq', 'canonical_seq', 'cards', 'model', 'requests'):
            assert after[key] == before[s][key], key
        strip = lambda rows: [{k: v for k, v in row.items() if k != 'synced_at'} for row in rows]
        assert strip(after['done']) == strip(before[s]['done'])
        new = _remote(f, s, remote)
        old = next(row for row in before[s]['tasks'] if row['id'] == remote)
        assert new['execution_observations'] == old['execution_observations']
        assert {k: v for k, v in new.items() if k != 'synced_at'} == {k: v for k, v in old.items() if k != 'synced_at'}


class Gate:
    """Per-chat projection barrier: each pure worker blocks until released."""

    def __init__(self, lane, monkeypatch):
        self.lane, self.cond = lane, threading.Condition()
        self.order, self.inside, self.gates, self.peak = [], set(), {}, 0
        self.start_seq = {}
        original = lane._project
        def held(s):
            with self.cond:
                self.start_seq[s] = len(self.order)
                self.order.append(s); self.inside.add(s)
                self.peak = max(self.peak, len(self.inside))
                gate = self.gates.setdefault(s, threading.Event())
                self.cond.notify_all()
            assert gate.wait(10), 'barrier never released'
            try:
                return original(s)
            finally:
                with self.cond:
                    self.inside.discard(s); self.gates.pop(s, None)
                    self.cond.notify_all()
        monkeypatch.setattr(lane, '_project', held)

    def settle(self):
        """Every worker the coordinator admitted has reached its barrier."""
        with self.lane.lock:
            active = set(self.lane.active)
        with self.cond:
            assert self.cond.wait_for(lambda: active <= self.inside, 5), active - self.inside

    def complete_oldest(self):
        with self.lane.lock, self.cond:
            # Admission order among workers actually parked at their barrier.
            # Workers finish in the order their current run started (no worker is starved by the harness).
            s = min((c for c in self.inside if c in self.lane.active), key=self.start_seq.__getitem__)
            worker = self.lane.active[s]
        with self.cond:
            self.gates.setdefault(s, threading.Event()).set()
        worker.join(5)
        assert not worker.is_alive()
        return s


def _setup(api, board, monkeypatch):
    path, conn = board
    r = readers_for(api, path, monkeypatch)
    monkeypatch.setattr(r, 'external_capture', strict_empty)
    sessions = [f'S{i:02}' for i in range(CHATS)]
    tasks = {s: kanban_db.create_task(conn, title=f'{s} v0', session_id=s) for s in sessions}
    f = feedmod.Feed(r, emit=lambda _: None)
    f.watch(sessions)
    lane = compose(f, r)
    # Finish source setup via owned worker joins; the FIFO experiment below
    # measures ordering under arrivals, not the request's existing 3s budget.
    drain(lane)
    f.changes(sessions); drain(lane)
    return conn, r, f, lane, sessions, tasks


def _arrive(lane, conn, tasks, sessions, version):
    """Real source rewrite of these chats' cards, observed by a coordinator step."""
    for s in sessions:
        conn.execute('UPDATE tasks SET title=? WHERE id=?', (f'{s} v{version}', tasks[s]))
    conn.commit()
    lane.step()
    worker = lane.acquiring.get('local')
    if worker:
        worker.join(5)
    lane.service()


EVENT_AT = 3  # completions served before the beat/full/failure interleaving


def _inject(name, monkeypatch, r, lane, hot, sessions):
    """Interleave one finite failure/beat/full pass; returns (action, threads, errors, failures)."""
    threads, errors, failures = [], [], []
    def background(call):
        def run():
            try:
                call()
            except BaseException as exc:  # reported by the test body
                errors.append(exc)
        thread = threading.Thread(target=run, name='fixture-' + name)
        threads.append(thread); thread.start()
    def action():
        if name == 'failure':
            def unavailable(held, *, include_inventory=False):
                # Only failed reads consume the injected fault budget. Later
                # successful captures for real changed stamps/demands are not
                # failures or retries, and must not be capped as if they were.
                if len(failures) < 3:
                    failures.append(1)
                    raise RuntimeError('strict unavailable')
                return strict_empty(held, include_inventory=include_inventory)
            monkeypatch.setattr(r, 'external_capture', unavailable)
        elif name == 'beat':
            background(lane.tick)
        elif name == 'full':
            background(lambda: lane.full(hot[:2] + sessions[:2]))
    return action, threads, errors, failures


def _run(api, board, monkeypatch, event=None):
    conn, r, f, lane, sessions, tasks = _setup(api, board, monkeypatch)
    gate = Gate(lane, monkeypatch)
    action, threads, errors, failures = None, [], [], []
    try:
        lane.capacity = 0
        _arrive(lane, conn, tasks, sessions, 1)
        with lane.lock:
            initial = dict(lane.pending)
        assert set(initial) == set(sessions), 'all 64 chats initially dirty'
        order0 = sorted(initial, key=lambda s: (initial[s], s))
        quiet = order0[CHATS // 3]
        ahead = set(order0[:CHATS // 3])
        hot = order0[-HOT:]
        if event:
            action, threads, errors, failures = _inject(event, monkeypatch, r, lane, hot, sessions)
        with lane.lock:
            lane.capacity = 2
        lane.service()
        gate.settle()
        completions, version, extra = 0, 1, 0
        while quiet not in gate.order:
            done = gate.complete_oldest()
            completions += 1
            version += 1
            # Continuous arrivals: the newest chats and the chat just served are
            # rewritten while the quiet chat still waits.
            _arrive(lane, conn, tasks, hot + [done], version)
            if action and completions == EVENT_AT:
                with lane.lock:
                    # A finite failure/beat/full requeue may re-run, once, work already in
                    # flight (it keeps its first-admitted age); nothing else is excused.
                    extra = len(lane.active)
                action()
                _arrive(lane, conn, tasks, hot, version + 1000)
            lane.service()
            gate.settle()
            with lane.lock:
                for s in hot:
                    if s in lane.pending:
                        assert lane.pending[s] == initial[s], 'rewrite reset a waiting chat age'
                assert len(lane.active) <= 2 and set(lane.acquiring) <= {'local', 'external'}
            started = gate.order[:gate.order.index(quiet)] if quiet in gate.order else gate.order
            assert completions <= len(ahead) + 1 + extra, ('quiet chat starved by continuous arrivals',
                completions, [s for s in started if s not in ahead], sorted({s for s in started if started.count(s) > 1}), ' '.join(gate.order),
                {s: (lane.projection_attempts[s].count, lane.projection_attempts[s].retry_at, lane._active_ages.get(s) == initial[s], s in lane.pending) for s in gate.order[-1:] if s in lane.projection_attempts}, completions)
        before_quiet = gate.order[:gate.order.index(quiet)]
        assert set(before_quiet) <= ahead, 'a later arrival overtook the older quiet chat'
        assert all(before_quiet.count(s) <= 2 for s in before_quiet) and len(before_quiet) - len(set(before_quiet)) <= extra
        assert gate.peak <= 2
        # Stop arrivals; release and drain the finite queue.
        for _ in range(500):
            with lane.lock:
                busy = bool(lane.active)
            if busy:
                gate.complete_oldest()
            lane.step()
            for worker in list(lane.acquiring.values()):
                worker.join(5)
            lane.service()
            gate.settle()
            with lane.lock:
                if not lane.pending and not lane.active and not lane.acquiring:
                    break
        else:
            raise AssertionError('finite queue did not drain')
        for thread in threads:
            thread.join(10)
            assert not thread.is_alive()
        if errors:
            # A saturated real source may exceed tick's unchanged 3s synchronous
            # wait. Keep that canonical failure visible; it is not a successful
            # beat or latency receipt. FIFO and eventual exact content below
            # remain mandatory, followed by a successful quiescent real beat.
            assert event == 'beat'
            assert all(type(exc) is TimeoutError and str(exc) ==
                       'local source acquisition is still pending' for exc in errors), errors
            lane.tick()
            drain(lane)
        if event == 'failure':
            assert 1 <= len(failures) <= 8, 'strict failures must stay finite'
        for s in sessions:
            want = conn.execute('SELECT title FROM tasks WHERE id=?', (tasks[s],)).fetchone()[0]
            assert {row['title'] for row in f.bundles[s]['bundle']['tasks']} == {want}, s
        assert gate.peak <= 2
        return completions, len(ahead) + extra
    finally:
        for g in list(gate.gates.values()):
            g.set()
        f.stopped.set(); lane.close(); r.release(); conn.close()


def test_continuous_arrivals_64_serve_oldest_quiet_chat_within_initial_queue_length(api, board, monkeypatch):
    completions, ahead = _run(api, board, monkeypatch)
    assert completions <= ahead + 1


def test_real_tick_timeout_retains_source_owner_and_recovers_exact_content(api, board, monkeypatch):
    conn, r, f, lane, sessions, tasks = _setup(api, board, monkeypatch)
    entered, release = threading.Event(), threading.Event()
    original = lane._capture_one
    def held(rd, source, stamp):
        if source != lane.external:
            entered.set()
            assert release.wait(30), 'fixture local capture was not released'
        return original(rd, source, stamp)
    monkeypatch.setattr(lane, '_capture_one', held)
    try:
        s = sessions[0]
        before = copy.deepcopy(f.bundles[s]['bundle'])
        source = lane.sources[0]
        version = lane.store.version(source)
        conn.execute('UPDATE tasks SET title=? WHERE id=?', ('After bounded source wait', tasks[s]))
        conn.commit()
        lane.step()
        assert entered.wait(5)
        worker = lane.acquiring['local']
        with pytest.raises(TimeoutError, match='^local source acquisition is still pending$'):
            lane.tick()
        assert worker.is_alive() and lane.acquiring['local'] is worker
        assert lane.store.version(source) == version
        assert f.bundles[s]['bundle'] == before
        assert set(f.interest) == set(sessions)
        release.set()
        worker.join(10)
        assert not worker.is_alive()
        drain(lane)
        lane.tick()
        drain(lane)
        assert {row['title'] for row in f.bundles[s]['bundle']['tasks']} == {'After bounded source wait'}
        assert all(session in f.bundles for session in sessions)
        assert not lane.active and not lane.pending and not lane.acquiring
    finally:
        release.set()
        f.stopped.set(); lane.close(); r.release(); conn.close()


@pytest.mark.parametrize('event', ['failure', 'beat', 'full'])
def test_continuous_arrivals_remain_fifo_across_finite_failure_beat_and_full(api, board, monkeypatch, event):
    completions, ahead = _run(api, board, monkeypatch, event)
    assert completions <= ahead + 1
