"""Cutover retains custody of every predecessor until it has exited."""
import sys
import threading
from pathlib import Path

import pytest
from test_routes import load
from test_feed import Readers


@pytest.fixture
def api(monkeypatch):
    monkeypatch.delitem(sys.modules, '_session_panel_feed_core', raising=False)
    monkeypatch.delitem(sys.modules, '_session_panel_feed_loader', raising=False)
    api = load('_panel_fact_lifecycle', Path(__file__).resolve().parents[1] / 'dashboard/plugin_api.py')
    monkeypatch.setattr(api, 'save_snapshot', lambda *a, **k: True)
    monkeypatch.setattr(api, 'load_snapshot', lambda *a, **k: None)
    core = api._load_feed()
    core.PROCESS_FEED = core.Feed(Readers(), emit=lambda *a: None)
    yield api
    core.PROCESS_FEED.stopped.set()
    core.PROCESS_FEED = None
    monkeypatch.delitem(sys.modules, '_session_panel_feed_core', raising=False)
    monkeypatch.delitem(sys.modules, '_session_panel_feed_loader', raising=False)


def test_quiesce_wakes_idle_lanes_before_join(api):
    f = api._load_feed().PROCESS_FEED
    started = threading.Event()
    def idle():
        started.set()
        f._board.wait(2)
    thread = threading.Thread(target=idle)
    f._board_thread = thread
    thread.start()
    assert started.wait(1)
    try:
        assert api._quiesce(f, timeout=0.1)
    finally:
        f._board.set()
        thread.join(2)


def test_timeout_retains_predecessor_and_blocks_restart(api, monkeypatch):
    core = api._load_feed()
    predecessor = core.PROCESS_FEED
    monkeypatch.setattr(api, '_quiesce', lambda f: (f.stopped.set() or False))
    starts = []
    monkeypatch.setattr(api, 'CoreReaders', lambda: starts.append(True) or Readers())
    with pytest.raises(RuntimeError, match='quiesce'):
        api._facts_mode_changed('facts', 1)
    assert api.feed() is predecessor
    assert core.PROCESS_FEED is predecessor and not starts
    assert predecessor.stopped.is_set()


def test_reload_retains_stuck_predecessor(api, monkeypatch):
    from types import SimpleNamespace as NS
    old = api._load_feed()
    predecessor = old.PROCESS_FEED
    attempted = threading.Event()
    api._LOADER.facts_registration = NS(wake=threading.Event())
    monkeypatch.setattr(old, 'SOURCE_STAMP', ('older',))
    monkeypatch.setattr(old, 'SOURCE_SHA', 'older')
    def quiesce(f):
        attempted.set()
        return False
    monkeypatch.setattr(api, '_quiesce', quiesce)
    monkeypatch.setattr(api, 'CoreReaders', lambda: pytest.fail('reload escaped custody'))
    new = api._load_feed()
    assert new is not old and attempted.wait(2)
    assert api.feed() is predecessor
    assert new.PROCESS_FEED is predecessor
    assert api._LOADER.facts_transition_pending
    assert api._LOADER.facts_registration.wake.wait(2)
    api._LOADER.facts_registration = None


def test_concurrent_start_cannot_escape_mode_transition(api, monkeypatch):
    core = api._load_feed()
    predecessor = core.PROCESS_FEED
    observed = []
    def quiesce(f):
        f.stopped.set()
        observed.append(api.feed())
        return False
    monkeypatch.setattr(api, '_quiesce', quiesce)
    monkeypatch.setattr(api, 'CoreReaders', lambda: pytest.fail('started reader during cutover'))
    with pytest.raises(RuntimeError, match='quiesce'):
        api._facts_mode_changed('facts', 1)
    assert observed == [predecessor]
