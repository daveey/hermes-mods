"""Dormant external selection must exclude import/reload legacy producers."""
import importlib.util
from pathlib import Path
import sys
import threading
import types

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'dashboard' / 'plugin_api.py')
    api = importlib.util.module_from_spec(spec)
    sys.modules[name] = api
    spec.loader.exec_module(api)
    return api


@pytest.fixture
def isolated(monkeypatch):
    loader = types.ModuleType('_session_panel_feed_loader')
    monkeypatch.setitem(sys.modules, loader.__name__, loader)
    monkeypatch.delitem(sys.modules, '_session_panel_feed_core', raising=False)
    monkeypatch.setattr(sys, 'argv', ['hermes', 'serve'])
    starts = []
    original = threading.Thread.start
    def start(thread):
        if thread.name.startswith('session-panel-'):
            starts.append(thread.name)
        else:
            original(thread)
    monkeypatch.setattr(threading.Thread, 'start', start)
    yield loader, starts
    for name in ('hermes_dashboard_plugin_external_one', 'hermes_dashboard_plugin_external_two'):
        sys.modules.pop(name, None)


def test_preselected_external_excludes_import_reload_and_direct_start(isolated):
    loader, starts = isolated
    loader.external_projection = object()
    one = load('hermes_dashboard_plugin_external_one')
    two = load('hermes_dashboard_plugin_external_two')
    assert not starts
    assert '_session_panel_feed_core' not in sys.modules
    assert one.start_publication() is False
    assert two.start_publication() is False
    with pytest.raises(RuntimeError, match='external'):
        one.feed()
    with pytest.raises(RuntimeError, match='external'):
        two._load_feed()
    assert not starts


def test_external_refuses_existing_legacy_feed_even_when_stopped(isolated, monkeypatch):
    loader, starts = isolated
    loader.external_projection = object()
    legacy = types.ModuleType('_session_panel_feed_core')
    # A stopped flag alone is not proof that blocked producer threads exited.
    legacy.PROCESS_FEED = types.SimpleNamespace(stopped=threading.Event())
    legacy.PROCESS_FEED.stopped.set()
    monkeypatch.setitem(sys.modules, legacy.__name__, legacy)
    with pytest.raises(RuntimeError, match='legacy'):
        load('hermes_dashboard_plugin_external_one')
    assert not starts
    assert sys.modules[legacy.__name__].PROCESS_FEED is legacy.PROCESS_FEED


def test_external_refuses_pending_legacy_start(isolated):
    loader, starts = isolated
    loader.external_projection = object()
    loader.starting = types.SimpleNamespace(is_alive=lambda: True)
    with pytest.raises(RuntimeError, match='legacy'):
        load('hermes_dashboard_plugin_external_one')
    assert not starts


def test_absent_external_keeps_legacy_import_start(isolated):
    loader, starts = isolated
    current = load('hermes_dashboard_plugin_external_one')
    if getattr(loader, 'panel_lifecycle', None) is None:
        # Optional/older core has no lifecycle seam: preserve the original
        # asynchronous import-start contract without pretending it is READY.
        assert starts == ['session-panel-start']
        # Lifecycle probing may import the lazy feed module even on an older
        # core. It must not construct a producer before the deferred starter runs.
        assert getattr(sys.modules.get('_session_panel_feed_core'), 'PROCESS_FEED', None) is None
        loader.starting = None  # recorded Thread.start never launched this thread
        return
    # OFF readiness is synchronous on the lifecycle worker, not a deferred
    # session-panel-start promise. The fixture records (but does not launch) lanes.
    assert starts == ['session-panel-feed', 'session-panel-decisions', 'session-panel-board']
    core = sys.modules['_session_panel_feed_core']
    owner = core.PROCESS_FEED
    assert owner is not None and not owner.stopped.is_set()
    owner.stopped.set()
    for name in ('_feed_thread', '_decision_thread', '_board_thread'):
        setattr(owner, name, None)  # Thread.start was deliberately replaced above
    assert current._retire_legacy_panel()


def test_external_refuses_legacy_facts_registration(isolated):
    loader, starts = isolated
    loader.external_projection = object()
    loader.facts_registration = object()
    with pytest.raises(RuntimeError, match='legacy facts'):
        load('hermes_dashboard_plugin_external_one')
    assert not starts
