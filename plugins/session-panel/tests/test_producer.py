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
        while core.PROCESS_FEED is None and time.monotonic() < deadline:
            time.sleep(0.02)
        f = core.PROCESS_FEED
        assert f is not None and not f.stopped.is_set(), 'running with no request made'
        assert any(t.name == 'session-panel-feed' and t.is_alive() for t in threading.enumerate())
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
