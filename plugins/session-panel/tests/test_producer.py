"""The producer side of the click path: cold reads wake it, it publishes what a reader waits for first, it does not
re-read unchanged decision inputs, and it is started by the backend's import of the plugin API, not by a request."""
import importlib.util
import sys
import threading
import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from test_feed import feedmod, Readers
from test_routes import load

Feed = feedmod.Feed
API = Path(__file__).resolve().parents[1] / 'dashboard' / 'plugin_api.py'


def make(readers=None):
    r = readers or Readers()
    pushed, t = [], {'now': 1000.0}
    return Feed(r, emit=pushed.append, clock=lambda: t['now']), r, pushed, t


def test_a_cold_read_wakes_the_producer_and_a_warm_one_does_not():
    f, *_ = make()
    f.changes(['S1'])
    f.tick()
    assert not f._wake.is_set()
    f.changes_bytes(['S1'])
    assert not f._wake.is_set(), 'a published chat needs no pass'
    assert b'"bundles":{}' in f.changes_bytes(['S7'])
    assert f._wake.is_set()
    f.tick()
    assert not f._wake.is_set() and b'"S7":' in f.changes_bytes(['S7'])


def test_the_chat_a_reader_waits_for_is_published_before_the_sweep():
    f, r, pushed, t = make()
    names = [f'W{i:02d}' for i in range(40)]
    f.changes_bytes(names)          # a Desktop warming its sidebar
    f.changes_bytes(['CLICKED'])    # then a click on a chat outside it
    order = []
    real = f.compute
    f.compute = lambda s, **k: (order.append(s), real(s, **k))[1]
    seen = []
    f.emit = lambda p: seen.append(sorted(p['bundles']))
    assert f.tick() == 41
    batches = [b for b in seen if b]
    assert batches[0] == ['CLICKED'] and order[0] == 'CLICKED', 'the newest request is stored and pushed first, alone'
    assert batches[1] == ['W00'], "then the chat that led the older request (that client's focused chat)"
    assert batches[2] == names[1:1 + feedmod.FIRST_CHUNK]
    assert all(len(b) <= feedmod.CHUNK for b in batches), 'never one push after a whole sweep'
    assert sorted(s for b in batches for s in b) == sorted(names + ['CLICKED'])


def test_a_click_during_a_sweep_is_taken_between_chunks():
    f, r, pushed, t = make()
    names = [f'W{i:02d}' for i in range(40)]
    f.changes(names[:1])
    f.tick()
    for s in names[1:]:
        f.watch([s])                 # watched, no bundle, nobody waiting: plain sweep order
    order = []
    real = f.compute

    def compute(s, **k):
        order.append(s)
        if len(order) == 3:
            f.changes_bytes(['LATE'])  # arrives while the first sweep chunk computes
        return real(s, **k)
    f.compute = compute
    f.tick()
    assert order.index('LATE') == feedmod.CHUNK, 'computed right after the chunk in flight, not after the sweep'
    assert len(order) == 40
    assert b'"LATE":' in f.changes_bytes(['LATE'])


def test_a_cold_chat_whose_compute_fails_is_tried_once_per_pass():
    f, r, pushed, t = make()
    real = r.lineage
    calls = []
    r.lineage = lambda s: (calls.append(s), (_ for _ in ()).throw(RuntimeError('boom')) if s == 'BAD' else real(s))[1]
    f.changes_bytes(['BAD', 'S1'])
    done = []
    worker = threading.Thread(target=lambda: done.append(f.tick()), daemon=True)
    worker.start()
    worker.join(5)
    assert done == [1], 'the pass ends; the failing chat is left for the next one'
    assert calls.count('BAD') == 1 and 'BAD' in f.failures and b'"S1":' in f.changes_bytes(['S1'])


class Stamped(Readers):
    """Readers that can say whether a chat's decision inputs moved (as CoreReaders can)."""
    def __init__(self):
        super().__init__()
        self.files, self.store, self.rows = 1, 1, {}
        self.decisions, self.batches, self.singles = [], 0, 0

    def panel_files_stamp(self):
        return (self.files, self.store)

    def decision_store_stamp(self):
        return self.store

    def panel_stamps(self, ids):
        self.batches += 1
        return {i: self.rows.get(i) for i in ids}

    def panel_stamp(self, s):
        self.singles += 1
        return self.rows.get(s)

    def decision_inputs(self, s, lineage):
        self.decisions.append(s)
        return {'outcome_json': dict(self.rows.get(s) or {}), 'requests': []}

    def panel_inputs(self, s, lineage):
        return {'outcome_json': dict(self.rows.get(s) or {}), 'requests': []}


def test_unchanged_decision_inputs_are_not_re_read_every_pass():
    f, r, pushed, t = make(Stamped())
    f.changes(['S1', 'S2'])
    f.refresh_decisions()
    assert sorted(r.decisions) == ['S1', 'S2']
    r.decisions.clear()
    before = r.batches
    for _ in range(5):
        t['now'] += 1
        assert f.refresh_decisions() == 0
    assert r.decisions == [] and r.batches == before, 'no file moved: nothing read, no database opened'
    # Unrelated traffic in the same file: one batched stamp read, still no per-chat read.
    r.files += 1
    t['now'] += 1
    f.refresh_decisions()
    assert r.decisions == [] and r.batches == before + 1
    # This chat's own inputs moved: it alone is read, and the new decision is published.
    r.files += 1
    r.rows['S2'] = {'kind': 'done'}
    t['now'] += 1
    assert f.refresh_decisions() == 1 and r.decisions == ['S2']
    assert f.bundles['S2']['bundle']['outcome_json'] == {'kind': 'done'}
    # Any write to the request store re-reads every chat (operations and deliveries are not in the row stamp).
    r.decisions.clear()
    r.store += 1
    t['now'] += 1
    f.refresh_decisions()
    assert sorted(r.decisions) == ['S1', 'S2']
    # The safety net: every chat again after DECISION_FULL_EVERY_S with nothing moved.
    r.decisions.clear()
    t['now'] += feedmod.DECISION_FULL_EVERY_S + 1
    f.refresh_decisions()
    assert sorted(r.decisions) == ['S1', 'S2']


def test_a_failed_decision_read_is_retried_even_when_nothing_moved():
    f, r, pushed, t = make(Stamped())
    f.changes(['S1'])
    f.refresh_decisions()
    real = r.decision_inputs
    r.store += 1
    r.decision_inputs = lambda s, lineage: (_ for _ in ()).throw(RuntimeError('busy'))
    f.refresh_decisions()
    assert f._decision_failures == {'S1'}
    r.decision_inputs = real
    r.decisions.clear()
    t['now'] += 1
    f.refresh_decisions()
    assert r.decisions == ['S1'] and not f._decision_failures


def test_readers_without_stamps_keep_reading_every_pass():
    class Plain(Readers):
        def __init__(self):
            super().__init__()
            self.decisions = []

        def decision_inputs(self, s, lineage):
            self.decisions.append(s)
            return {'outcome_json': {}, 'requests': []}
    f, r, pushed, t = make(Plain())
    f.changes(['S1'])
    for _ in range(3):
        f.refresh_decisions()
    assert r.decisions == ['S1'] * 3


def test_panel_stamps_are_read_in_one_batch_and_only_when_a_file_moved():
    f, r, pushed, t = make(Stamped())
    f.changes(['S1', 'S2'])
    f.tick()
    assert r.singles == 0 and r.batches == 1, 'one batched read for every watched chat and its ancestors'
    for _ in range(4):
        t['now'] += 1
        f.tick()
    assert r.batches == 1, 'no file moved'
    r.files += 1
    r.rows['S2'] = {'kind': 'done'}
    t['now'] += 1
    assert f.tick() == 1 and r.batches == 2 and r.singles == 0
    assert f.bundles['S2']['bundle']['outcome_json'] == {'kind': 'done'}


def test_a_slow_pass_rests_as_long_as_it_ran():
    api = load('_spf_pause_api', API)
    assert api._pause(0.1, 1.0) == pytest.approx(0.9)       # inside the tick: the tick period holds
    assert api._pause(0.95, 1.0) == pytest.approx(0.95)
    assert api._pause(3.0, 1.0) == pytest.approx(3.0)       # an overrunning pass: at most half the time busy
    assert api._pause(60.0, 1.0) == api.MAX_PAUSE_S
    assert api._pause(0.0, 0.05) == api.MIN_PAUSE_S


def test_only_the_backend_import_of_the_plugin_api_starts_the_producer():
    api = load('_spf_serves_api', API)
    assert api._serves_desktop('hermes_dashboard_plugin_session-panel', 'serve')
    assert api._serves_desktop('hermes_dashboard_plugin_session-panel', 'serve-always-on')
    assert api._serves_desktop('hermes_dashboard_plugin_session-panel', 'dashboard')
    assert not api._serves_desktop('hermes_dashboard_plugin_session-panel', 'gateway')
    assert not api._serves_desktop('hermes_dashboard_plugin_session-panel', 'proc')
    assert not api._serves_desktop('_spf_serves_api', 'serve'), 'a test or tool loading the file starts nothing'


def test_the_backend_import_starts_the_feed_before_any_request(monkeypatch):
    # Core's app runs a lifespan (router startup hooks never fire) and may mount plugin routes long after startup.
    pytest.importorskip('hermes_cli.kanban_db')  # the import starts the real feed, whose readers are core's
    monkeypatch.setattr(sys, 'argv', ['hermes', 'serve', '--port', '1'])
    loader = sys.modules.get('_session_panel_feed_loader')
    core = sys.modules.get('_session_panel_feed_core')
    if core is not None:
        monkeypatch.setattr(core, 'PROCESS_FEED', None)
    if loader is not None:
        monkeypatch.setattr(loader, 'starting', None, raising=False)
    api = load('hermes_dashboard_plugin_session-panel', API)
    try:
        core = api._load_feed()
        deadline = time.monotonic() + 10
        alive = lambda: any(t.name == 'session-panel-feed' and t.is_alive() for t in threading.enumerate())
        while not (core.PROCESS_FEED is not None and alive()) and time.monotonic() < deadline:
            time.sleep(0.02)
        f = core.PROCESS_FEED
        assert f is not None and not f.stopped.is_set(), 'running with no request made'
        assert alive()
    finally:
        sys.modules.pop('hermes_dashboard_plugin_session-panel', None)
        if core.PROCESS_FEED is not None:
            core.PROCESS_FEED.stopped.set()
        starting = api._LOADER.__dict__.get('starting')
        if starting is not None:
            starting.join(5)
        monkeypatch.setattr(core, 'PROCESS_FEED', None)


def test_a_request_before_the_feed_exists_starts_it_off_the_request_and_says_retry(monkeypatch):
    api = load('_spf_lazy_api', API)
    core = api._load_feed()
    monkeypatch.setattr(core, 'PROCESS_FEED', None)
    monkeypatch.setattr(api._LOADER, 'starting', None, raising=False)
    gate, entered = threading.Event(), threading.Event()
    monkeypatch.setattr(api, 'feed', lambda: (entered.set(), gate.wait(5)))
    app = FastAPI()
    app.include_router(api.router)
    try:
        r = TestClient(app).post('/changes', json={'sessions': ['S1']})
        assert r.status_code == 503 and r.headers['retry-after'] == '1'
        assert entered.wait(2), 'started on its own thread'
        first = api._LOADER.starting
        assert TestClient(app).post('/changes', json={'sessions': ['S1']}).status_code == 503
        assert api._LOADER.starting is first, 'one start in flight per process'
    finally:
        gate.set()
        api._LOADER.starting.join(5)


# --- [daveey 2026-11-22] "no polling. Update only when the data changes." ---

def test_file_watch_reports_writes_to_the_watched_databases_and_nothing_else(tmp_path):
    api = load('_spf_watch_api', API)
    db, other = tmp_path / 'state.db', tmp_path / 'agent.log'
    db.write_bytes(b'x')
    w = api.FileWatch()
    if not w.ok:
        pytest.skip('no kernel file notification on this platform')
    try:
        w.set_paths([db, tmp_path / 'boards' / 'late' / 'kanban.db'])  # the second does not exist yet
        assert w.wait(0.2) is False, 'idle: nothing reported'
        other.write_bytes(b'noise')
        assert w.wait(0.2) is False, 'another file in the same directory is not ours'
        db.write_bytes(b'xy')
        assert w.wait(2) is True
        while w.wait(0.1):
            pass
        (tmp_path / 'state.db-wal').write_bytes(b'frame')
        assert w.wait(2) is True, 'a WAL created after the watch started counts'
        while w.wait(0.1):
            pass
        late = tmp_path / 'boards' / 'late'
        late.mkdir(parents=True)
        w.set_paths([db, late / 'kanban.db'])
        (late / 'kanban.db').write_bytes(b'new board')
        assert w.wait(2) is True
    finally:
        w.close()


class Watched(Stamped):
    def __init__(self, tmp):
        super().__init__()
        self.db = tmp / 'state.db'
        self.db.write_bytes(b'0')
        self.released = 0

    def watch_paths(self):
        return [self.db]

    def release(self):
        self.released += 1


def running(api, f):
    threads = [threading.Thread(target=api._run, args=(f,), daemon=True),
               threading.Thread(target=api._run_decisions, args=(f,), daemon=True)]
    for t in threads:
        t.start()
    return threads


def test_idle_means_idle_and_a_write_is_pushed_within_a_second(tmp_path, monkeypatch):
    api = load('_spf_event_api', API)
    if not api.FileWatch().ok:
        pytest.skip('no kernel file notification on this platform')
    monkeypatch.setattr(api, 'save_snapshot', lambda f: True)
    monkeypatch.setattr(api, 'CoreReaders', lambda: r)
    r = Watched(tmp_path)
    pushed = []
    f = Feed(r, emit=pushed.append)
    ticks, decisions = [], []
    tick, refresh = f.tick, f.refresh_decisions
    f.tick = lambda: (ticks.append(time.monotonic()), tick())[1]
    f.refresh_decisions = lambda **k: (decisions.append(time.monotonic()), refresh(**k))[1]
    f.changes(['S1'])
    threads = running(api, f)
    try:
        deadline = time.monotonic() + 5
        while (not ticks or not decisions) and time.monotonic() < deadline:
            time.sleep(0.01)
        assert len(ticks) == 1 and len(decisions) == 1, 'one pass each at start'
        time.sleep(0.3)
        base = (len(ticks), len(decisions), r.batches, len(r.decisions))
        time.sleep(2.5)  # well past the old 1 s tick
        assert (len(ticks), len(decisions), r.batches, len(r.decisions)) == base, 'no write: no pass, no read'
        # A write: the changed chat's model is pushed inside a second.
        r.files += 1
        r.rows['S1'] = {'kind': 'done'}
        wrote = time.monotonic()
        r.db.write_bytes(b'1')
        while time.monotonic() - wrote < 3 and f.bundles['S1']['bundle']['outcome_json'] != {'kind': 'done'}:
            time.sleep(0.01)
        took = time.monotonic() - wrote
        assert f.bundles['S1']['bundle']['outcome_json'] == {'kind': 'done'} and took < 1.0, took
        assert any('S1' in (p.get('bundles') or {}) for p in pushed)
        # A burst of writes is coalesced, not one pass per write.
        time.sleep(0.8)
        before = len(ticks)
        for i in range(40):
            r.db.write_bytes(b'%d' % i)
            time.sleep(0.005)
        time.sleep(1.2)
        assert 1 <= len(ticks) - before <= 3, len(ticks) - before
        # A cold read needs no write and skips the debounce.
        asked = time.monotonic()
        f.changes_bytes(['COLD'])
        while time.monotonic() - asked < 2 and b'"COLD":' not in f.changes_bytes(['COLD']):
            time.sleep(0.005)
        assert b'"COLD":' in f.changes_bytes(['COLD']) and time.monotonic() - asked < 0.5
    finally:
        f.stopped.set()
        for t in threads:
            t.join(5)
    assert not any(t.is_alive() for t in threads), 'a stop wakes the sleeping producers'


def test_the_idle_heartbeat_reads_no_database(tmp_path):
    class Untouchable(Watched):
        def __getattribute__(self, name):
            if name in ('stamps', 'panel_stamps', 'panel_stamp', 'changed_cards', 'lineage', 'decision_inputs',
                        'panel_inputs', 'panel_files_stamp', 'board_stamps') and object.__getattribute__(self, 'armed'):
                raise AssertionError(f'the heartbeat read {name}')
            return object.__getattribute__(self, name)
    r = Untouchable.__new__(Untouchable)
    object.__setattr__(r, 'armed', False)
    Watched.__init__(r, tmp_path)
    pushed, t = [], {'now': 1000.0}
    f = Feed(r, emit=pushed.append, clock=lambda: t['now'])
    f.changes(['S1'])
    f.tick()
    pushed.clear()
    r.armed = True
    assert f.beat() == 0 and pushed == [], 'not due yet'
    t['now'] += feedmod.HEARTBEAT_S + 1
    f.beat()
    assert len(pushed) == 1 and pushed[0].get('heartbeat') and not pushed[0].get('bundles')
    assert not f._wake.is_set()
    f.watch(['S9'])  # registered, no bundle: the heartbeat hands it to a pass instead of computing it
    f.beat()
    assert f._wake.is_set() and 'S9' not in f.bundles


def test_board_events_are_read_only_when_a_board_file_moved():
    class Boards(Stamped):
        def __init__(self):
            super().__init__()
            self.board, self.state, self.cards = 1, 1, 0

        def stamps(self):
            return (self.board, self.state)

        def board_stamps(self):
            return (self.board,)

        def changed_cards(self, cursor):
            self.cards += 1
            return {}, (cursor or 0) + 1
    f, r, pushed, t = make(Boards())
    f.changes(['S1'])
    f.tick()
    base = r.cards
    for _ in range(5):
        r.state += 1  # a chat wrote a message
        t['now'] += 0.5
        f.tick()
    assert r.cards == base, 'state.db traffic opens no board'
    r.board += 1
    f.tick()
    assert r.cards == base + 1


# --- review 6010587797: what time alone makes due, a refused watch, a directory made again ---

def test_a_quiet_backend_still_ages_open_work_and_retries_and_an_idle_one_does_nothing():
    f, r, pushed, t = make()
    f.changes(['S1'])  # the test readers give every chat a running card
    f.tick()
    assert f.bundles['S1']['bundle'].get('tasks')
    assert f._due(t['now'])[0] == pytest.approx(f.full_every_s), 'due when its run evidence and counted time age'
    assert f.idle_wait() == f.heartbeat_s
    t['now'] += f.full_every_s - 5
    assert f.idle_wait() == pytest.approx(5), 'the loop sleeps to that moment, not to the next heartbeat after it'
    f.beat()
    assert not f._wake.is_set()
    t['now'] += 5
    before = f.bundles['S1']['bundle']['computed_at']
    f.beat()
    assert f._wake.is_set(), 'no write for full_every_s: the safety-net pass is woken'
    f.tick()
    assert f.bundles['S1']['bundle']['computed_at'] > before and not f._wake.is_set()
    assert f._due(t['now'])[0] == pytest.approx(f.full_every_s)


def test_a_chat_with_no_open_work_is_not_due_before_the_quiet_period():
    f, r, pushed, t = make()
    f.changes(['Q1'])  # no card linked to it
    f.tick()
    b = f.bundles['Q1']['bundle']
    assert not any(b.get(k) for k in feedmod.OPEN_KEYS)
    t['now'] += f.full_every_s * 2
    assert f.idle_wait() == f.heartbeat_s
    f.beat()
    assert not f._wake.is_set() and not f._decide.is_set(), 'idle: nothing woken, nothing read'
    t['now'] = b['computed_at'] + feedmod.QUIET_FULL_EVERY_S
    f.beat()
    assert f._wake.is_set()


def test_a_pending_retry_wakes_a_pass_without_a_write():
    f, r, pushed, t = make()
    f.changes(['S1'])
    f.tick()
    f._retry.add('S1')  # a read that raced its change cursor (or a stale source row)
    assert f.idle_wait() == pytest.approx(feedmod.RETRY_EVERY_S)
    f.beat()
    assert not f._wake.is_set(), 'not back to back'
    t['now'] += feedmod.RETRY_EVERY_S
    f.beat()
    assert f._wake.is_set()
    f.tick()
    assert 'S1' not in f._retry


def test_a_failed_decision_read_wakes_the_decision_lane_without_a_write():
    f, r, pushed, t = make(Stamped())
    f.changes(['S1'])
    f.refresh_decisions()
    real = r.decision_inputs
    r.store += 1
    r.decision_inputs = lambda s, lineage: (_ for _ in ()).throw(RuntimeError('database is locked'))
    f.refresh_decisions()
    assert f._decision_failures == {'S1'}
    f.tick()
    assert f.idle_wait() == pytest.approx(feedmod.RETRY_EVERY_S)
    t['now'] += feedmod.RETRY_EVERY_S
    r.decision_inputs = real
    f.beat()
    assert f._decide.is_set()
    f.refresh_decisions()
    assert not f._decision_failures
    f._decide.clear()
    t['now'] += feedmod.RETRY_EVERY_S
    f.beat()
    assert not f._decide.is_set()


def test_the_event_loop_runs_the_due_pass_and_the_retries_with_no_write(tmp_path, monkeypatch):
    api = load('_spf_due_api', API)
    if not api.FileWatch().ok:
        pytest.skip('no kernel file notification on this platform')
    monkeypatch.setattr(api, 'save_snapshot', lambda f: True)
    monkeypatch.setattr(api, 'CoreReaders', lambda: r)
    r = Watched(tmp_path)
    f = Feed(r, emit=lambda p: None, full_every_s=1.0)
    computed = []
    real = f.compute
    f.compute = lambda s, **k: (computed.append(s), real(s, **k))[1]
    f.changes(['S1'])
    computed.clear()
    threads = running(api, f)
    try:
        time.sleep(0.6)
        # A decision read fails once after a store write, then nothing is written again.
        fail = {'left': 1}
        real_inputs = r.decision_inputs

        def inputs(s, lineage):
            if fail['left']:
                fail['left'] -= 1
                raise RuntimeError('database is locked')
            return real_inputs(s, lineage)
        r.decision_inputs = inputs
        r.store += 1
        r.db.write_bytes(b'1')
        deadline = time.monotonic() + 3
        while not f._decision_failures and time.monotonic() < deadline:
            time.sleep(0.01)
        assert f._decision_failures == {'S1'}
        computed.clear()
        time.sleep(4.2)  # no write at all
        assert len(computed) >= 3, f'{len(computed)} recomputes of an open-card chat in 4 s at full_every_s=1'
        assert not f._decision_failures, 'the failed decision read was retried'
    finally:
        f.stopped.set()
        for t in threads:
            t.join(5)
    assert not any(t.is_alive() for t in threads)


def test_a_refused_inotify_watch_raises_and_the_feed_goes_back_to_the_timed_loop(tmp_path, monkeypatch):
    import ctypes
    import errno
    api = load('_spf_refused_api', API)
    w = api.FileWatch()
    if not w.ok or w._libc is None:
        pytest.skip('inotify only')

    class Refusing:
        def __init__(self, libc):
            self._libc = libc

        def inotify_add_watch(self, fd, path, mask):
            ctypes.set_errno(errno.ENOSPC)
            return -1

        def __getattr__(self, name):
            return getattr(self._libc, name)
    db = tmp_path / 'state.db'
    db.write_bytes(b'0')
    w._libc = Refusing(w._libc)
    try:
        with pytest.raises(OSError) as e:
            w.set_paths([db])
        assert e.value.errno == errno.ENOSPC
    finally:
        w.close()
    # The feed: a watch refused on its first listing is not believed; the timed loop takes over and sees the write.
    real_watch = api.FileWatch

    def refusing_watch():
        made = real_watch()
        made._libc = Refusing(made._libc)
        return made
    monkeypatch.setattr(api, 'FileWatch', refusing_watch)
    monkeypatch.setattr(api, 'save_snapshot', lambda f: True)
    monkeypatch.setattr(api, 'CoreReaders', lambda: r)
    monkeypatch.setattr(api, 'TICK_S', 0.2)
    r = Watched(tmp_path)
    f = Feed(r, emit=lambda p: None)
    f.changes(['S1'])
    threads = running(api, f)
    try:
        deadline = time.monotonic() + 3
        while getattr(f, '_watching', None) is not False and time.monotonic() < deadline:
            time.sleep(0.01)
        assert f._watching is False
        r.files += 1
        r.rows['S1'] = {'kind': 'done'}
        r.db.write_bytes(b'1')
        deadline = time.monotonic() + 3
        while f.bundles['S1']['bundle']['outcome_json'] != {'kind': 'done'} and time.monotonic() < deadline:
            time.sleep(0.01)
        assert f.bundles['S1']['bundle']['outcome_json'] == {'kind': 'done'}
    finally:
        f.stopped.set()
        for t in threads:
            t.join(5)


def test_a_watched_directory_removed_and_made_again_is_watched_again(tmp_path):
    import shutil
    api = load('_spf_rewatch_api', API)
    w = api.FileWatch()
    if not w.ok or w._libc is None:
        pytest.skip('inotify only')
    board = tmp_path / 'boards' / 'b1'
    board.mkdir(parents=True)
    db = board / 'kanban.db'
    db.write_bytes(b'0')
    try:
        w.set_paths([db])
        assert w.wait(0.2) is False
        shutil.rmtree(board)
        board.mkdir()
        w.set_paths([db])  # before the kernel's "watch ended" is read
        while w.wait(0.2):
            pass
        db.write_bytes(b'1')
        assert w.wait(2) is True
        # And when the ended watch is read first: it counts as a change and asks for a re-list.
        while w.wait(0.1):
            pass
        shutil.rmtree(board)
        assert w.wait(2) is True and w.stale
        board.mkdir()
        w.set_paths([db])
        assert not w.stale
        while w.wait(0.1):
            pass
        db.write_bytes(b'2')
        assert w.wait(2) is True
    finally:
        w.close()


def test_the_watcher_lists_the_files_on_a_period_not_once_per_write(tmp_path, monkeypatch):
    api = load('_spf_listing_api', API)
    if not api.FileWatch().ok:
        pytest.skip('no kernel file notification on this platform')
    r = Watched(tmp_path)
    f = Feed(r, emit=lambda p: None)
    listings = []
    paths = lambda: (listings.append(1), [r.db])[1]
    watch = api.FileWatch()
    t = threading.Thread(target=api._watch, args=(f, watch, paths), daemon=True)
    t.start()
    try:
        time.sleep(0.2)
        for i in range(60):
            r.db.write_bytes(b'%d' % i)
            time.sleep(0.01)
        time.sleep(0.3)
        assert f._wake.is_set() and f._decide.is_set()
        assert len(listings) == 1, len(listings)
    finally:
        f.stopped.set()
        r.db.write_bytes(b'stop')
        t.join(5)
    assert not t.is_alive()
