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


def test_unload_retains_failed_join_until_reregister(api, monkeypatch):
    from types import SimpleNamespace as NS
    plugin = load('_panel_fact_plugin', Path(__file__).resolve().parents[1] / '__init__.py')
    core = api._load_feed()
    predecessor = core.PROCESS_FEED
    core.STOP_FACTS = api.stop_facts
    closed = []
    api._LOADER.facts_registration = NS(close=lambda: closed.append(True))
    api._LOADER.facts_mode = 'facts'
    api._LOADER.facts_v1 = object()
    monkeypatch.setattr(api, '_quiesce', lambda f: (f.stopped.set() or False))
    monkeypatch.setattr(api, 'CoreReaders', lambda: pytest.fail('escaped predecessor custody'))
    with pytest.raises(RuntimeError, match='quiesce'):
        plugin.stop()
    assert api.feed() is predecessor
    assert api._LOADER.facts_mode == 'facts'
    assert api._LOADER.facts_transition_pending
    with pytest.raises(RuntimeError, match='quiesce'):
        plugin.register(NS())  # older core must not discard the facts fence
    assert closed == [True]
    monkeypatch.setattr(api, '_quiesce', lambda f: True)
    monkeypatch.setattr(plugin, 'restart_after_reload', lambda: None)
    plugin.register(NS())
    assert api._LOADER.facts_mode == 'legacy'
    assert not api._LOADER.facts_transition_pending
    assert not hasattr(api._LOADER, 'facts_v1')


def test_reload_preserves_unload_hooks(api, monkeypatch):
    old = api._load_feed()
    old.STOP_FACTS = api.stop_facts
    old.INSTALL_FACTS = api.install_facts
    monkeypatch.setattr(old, 'SOURCE_STAMP', ('older',))
    monkeypatch.setattr(old, 'SOURCE_SHA', 'older')
    # Keep the synthetic publisher fenced; this test exercises hook carriage.
    api._LOADER.facts_transition_pending = True
    new = api._load_feed()
    assert new is not old
    assert new.STOP_FACTS is api.stop_facts
    assert new.INSTALL_FACTS is api.install_facts


def test_closed_binding_callback_is_fenced_after_older_core_register(api, monkeypatch):
    from types import SimpleNamespace as NS
    plugin = load('_panel_fact_plugin_late', Path(__file__).resolve().parents[1] / '__init__.py')
    token = object()
    api._LOADER.facts_binding_token = token
    api._LOADER.facts_mode = 'facts'
    api._LOADER.facts_generation = 7
    api._load_feed().STOP_FACTS = api.stop_facts
    monkeypatch.setattr(api, '_quiesce', lambda f: (f.stopped.set() or True))
    monkeypatch.setattr(plugin, 'restart_after_reload', lambda: None)
    plugin.stop()
    plugin.register(NS())
    monkeypatch.setattr(api, 'CoreReaders', lambda: pytest.fail('late callback restarted lanes'))
    api._facts_mode_changed('facts', 8, token=token)
    assert api._LOADER.facts_mode == 'legacy'
    assert not hasattr(api._LOADER, 'facts_v1')


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
