"""R through the real plugin seam: legacy -> exec child -> legacy in ONE process, driven by the mode setter."""
import importlib.util
import os
import sqlite3
import sys
import threading
import time
from pathlib import Path

import pytest

lifecycle_mod = pytest.importorskip('hermes_cli.serve_producer_lifecycle')
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hermes_cli.serve_producer_admission import REQUIRED_COLLECTORS, AdmissionCut, CollectorEvidence, LaunchGate
from hermes_cli.serve_producer_mode import read_mode, write_mode

ROOT = Path(__file__).resolve().parents[1]
FEED_KEY = '_session_panel_feed_core'


def wait_until(predicate, timeout=90):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    raise AssertionError('condition was not met')


def load_api(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'dashboard' / 'plugin_api.py')
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def complete_admit(runtime, installation, *, root):
    gate = LaunchGate.acquire(runtime)
    from hermes_cli.serve_producer_admission import read_participants_strict
    from hermes_cli.serve_producer_retirement import request_retirement
    for ticket in read_participants_strict(runtime):
        if ticket.identity.pid == os.getpid():
            request_retirement(runtime, ticket, root, gate)
    collectors = tuple((n, CollectorEvidence((f'{n}:ok',), ())) for n in sorted(REQUIRED_COLLECTORS))
    return gate, AdmissionCut(boot_id='b', generation='a' * 64, installation=str(installation), census=None,
                              collectors=collectors, classifications=(), legacy_candidates=(), retired=(),
                              errors=(), _acquisition=gate._acquisition)


@pytest.fixture
def served(tmp_path, monkeypatch):
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.delenv('HERMES_KANBAN_DB', raising=False)
    monkeypatch.delenv('HERMES_KANBAN_HOME', raising=False)
    monkeypatch.delenv('HERMES_SERVE_PRODUCERS_EXTERNAL', raising=False)
    monkeypatch.setattr(sys, 'argv', ['hermes', 'serve', '--ssh-session-token-file', '/slots/desktop-a/token'])
    with sqlite3.connect(tmp_path / 'state.db') as db:
        db.execute('CREATE TABLE sessions(id TEXT PRIMARY KEY,parent_session_id TEXT,outcome_json TEXT)')
        db.execute("INSERT INTO sessions VALUES ('chat',NULL,'{}')")
    monkeypatch.setattr(lifecycle_mod, 'default_admit', complete_admit)
    from hermes_cli.serve_producer_admission import enroll_launch
    from hermes_cli._startup_fast import project_root_str
    enroll_launch(tmp_path / 'serve-producer-v1', 'serve', installation=Path(project_root_str()))
    import types
    loader = sys.modules.setdefault('_session_panel_feed_loader', types.ModuleType('_session_panel_feed_loader'))
    reset = ('legacy_fenced', 'panel_lifecycle', 'external_projection', 'starting')
    for attr in reset:  # the loader module is shared by every load_api() copy in this process
        loader.__dict__.pop(attr, None)
    yield tmp_path
    for attr in reset:
        loader.__dict__.pop(attr, None)
    core = sys.modules.get(FEED_KEY)
    feed = getattr(core, 'PROCESS_FEED', None)
    if feed is not None:
        feed.stopped.set()
        core.PROCESS_FEED = None


def test_unset_env_serve_watches_read_only_and_follows_the_setter(served, monkeypatch):
    api = load_api('_mode_unset_api')
    starts = []
    monkeypatch.setattr(api, 'start_publication', lambda: (starts.append(1), True)[1])
    lifecycle = api.install_panel_lifecycle({})
    try:
        assert lifecycle is not None and lifecycle.state == 'off' and starts == [1]
        assert not (served / 'serve-producers').exists(), 'watching creates no installation state'
        assert lifecycle._thread is not None and lifecycle._thread.is_alive()
        write_mode(served, external=True)  # the one supported setter; this serve had no env at all
        wait_until(lambda: lifecycle.state == 'external')
        assert api._external_projection() is lifecycle.relay
        write_mode(served, external=False)
        wait_until(lambda: lifecycle.state == 'off')
        assert api._external_projection() is None and starts == [1, 1]
    finally:
        lifecycle.stop_watching()
        if lifecycle.relay is not None:
            api.detach_external(lifecycle.relay)
            lifecycle.relay.close(timeout=10)


def test_unset_env_serve_follows_a_stored_external_mode_at_start(served, monkeypatch):
    api = load_api('_mode_stored_api')
    starts = []
    monkeypatch.setattr(api, 'start_publication', lambda: (starts.append(1), True)[1])
    write_mode(served, external=True)
    lifecycle = api.install_panel_lifecycle({})
    try:
        assert starts == [], 'a stored external mode never starts a legacy owner first'
        assert api._legacy_fenced(), 'the legacy owner is fenced from the first instant'
        wait_until(lambda: lifecycle.state == 'external')
    finally:
        lifecycle.stop_watching()
        if lifecycle.relay is not None:
            api.detach_external(lifecycle.relay)
            lifecycle.relay.close(timeout=10)


def test_retired_legacy_is_fenced_against_every_retained_callback(served, monkeypatch):
    api = load_api('_fence_api')
    started = []
    assert not api._legacy_fenced()
    assert api._retire_legacy_panel() is True
    assert api._legacy_fenced()
    assert api.start_publication() is False
    with pytest.raises(RuntimeError, match='fenced'):
        api.feed()
    api._facts_mode_changed('legacy', 7)  # a delayed core latch callback is a no-op, not a restart
    assert getattr(sys.modules.get(FEED_KEY), 'PROCESS_FEED', None) is None and started == []
    assert api._LOADER.__dict__.get('starting') is None
    monkeypatch.setattr(api, 'start_publication', lambda: started.append('start') or True)
    api._start_legacy_authorized()  # only the lifecycle's own successor step lifts the fence
    assert started == ['start'] and not api._legacy_fenced()


def test_retire_legacy_drains_every_lane_not_just_three_threads(served):
    import types
    api = load_api('_drain_api')
    core = api._load_feed()
    release = threading.Event()
    watcher = threading.Thread(target=release.wait, name='stuck-watch', daemon=True)
    watcher.start()
    fake = types.SimpleNamespace(stopped=threading.Event(), lock=threading.RLock(), _watch_thread=watcher)
    core.PROCESS_FEED = fake
    try:
        assert api._retire_legacy_panel(timeout=0.2) is False, 'a surviving lane keeps custody'
        assert core.PROCESS_FEED is fake and fake.stopped.is_set()
        assert api._legacy_fenced(), 'fence retained: no automatic fallback after a failed drain'
        release.set()
        watcher.join(5)
        assert api._retire_legacy_panel(timeout=5) is True and core.PROCESS_FEED is None
    finally:
        release.set()
        core.PROCESS_FEED = None


def test_attach_refuses_a_withdrawn_generation(served):
    import types
    api = load_api('_attach_api')
    sunk = []
    stale = types.SimpleNamespace(retired=True, set_event_sink=sunk.append)
    with pytest.raises(RuntimeError, match='retired'):
        api.attach_external(stale)
    assert api._external_projection() is None and sunk == []
    live = types.SimpleNamespace(retired=False, set_event_sink=sunk.append)
    api.attach_external(live)
    assert api._external_projection() is live and len(sunk) == 1
    api.detach_external(live)


def test_setter_moves_one_serve_legacy_external_legacy_without_restart(served, monkeypatch):
    api = load_api('_mode_flip_api')
    starts = []
    real_start = api.start_publication
    monkeypatch.setattr(api, 'start_publication', lambda: (starts.append(1), real_start())[1])
    monkeypatch.setattr(api, '_request_profile', lambda: api.LAUNCH_PROFILE)
    pid = os.getpid()
    lifecycle = api.install_panel_lifecycle({'HERMES_SERVE_PANEL_EXTERNAL': 'false'})
    app = FastAPI()
    app.include_router(api.router)
    try:
        assert lifecycle is not None and lifecycle.state == 'off' and starts == [1]
        wait_until(lambda: getattr(sys.modules.get(FEED_KEY), 'PROCESS_FEED', None) is not None)
        legacy_feed = sys.modules[FEED_KEY].PROCESS_FEED
        with TestClient(app) as client:
            write_mode(served, external=True)  # the one supported setter writes this same revisioned mode
            wait_until(lambda: lifecycle.state == 'external')
            assert getattr(sys.modules[FEED_KEY], 'PROCESS_FEED', None) is None, 'legacy owner retired first'
            assert legacy_feed.stopped.is_set()
            assert api._external_projection() is lifecycle.relay
            wait_until(lambda: client.post('/changes', json={'sessions': ['chat']}).status_code == 200)
            assert lifecycle.relay.ping()['child_pid'] != pid
            first = lifecycle.relay
            write_mode(served, external=False)
            wait_until(lambda: lifecycle.state == 'off')
            assert not first.is_alive()
            assert api._external_projection() is None
            wait_until(lambda: getattr(sys.modules.get(FEED_KEY), 'PROCESS_FEED', None) is not None)
            assert starts == [1, 1], 'legacy resumed exactly once on the way back'
            assert client.get('/status').json()['running'] is True
    finally:
        lifecycle.stop_watching()
        api.detach_external(lifecycle.relay) if lifecycle.relay is not None else None
        if lifecycle.relay is not None:
            lifecycle.relay.close(timeout=10)
    assert os.getpid() == pid and read_mode(served)['revision'] == 3
    assert threading.active_count() >= 1


def test_facts_callback_checks_fence_before_any_core_load(served, monkeypatch):
    api = load_api('_facts_fence_entry_api')
    loads = []
    monkeypatch.setattr(api, '_load_feed', lambda: loads.append('load') or (_ for _ in ()).throw(RuntimeError('core entry')))
    monkeypatch.setattr(api, '_legacy_fenced', lambda: True)
    api._facts_mode_changed('legacy', 1)
    assert loads == []
    monkeypatch.setattr(api, '_legacy_fenced', lambda: False)
    with pytest.raises(RuntimeError, match='core entry'):
        api._facts_mode_changed('legacy', 1)
    assert loads == ['load']


def test_publication_external_fence_independent_of_legacy_fence(served, monkeypatch):
    api = load_api('_publication_fence_api')
    monkeypatch.setattr(api, '_legacy_fenced', lambda: False)
    monkeypatch.setattr(api, '_external_projection', lambda: object())
    assert api.start_publication() is False
    assert api._LOADER.__dict__.get('starting') is None


def test_master_only_environment_initializes_same_mode(served, monkeypatch):
    api = load_api('_master_only_api')
    monkeypatch.setattr(api, 'start_publication', lambda: True)
    write_mode(served, external=True)
    lifecycle = api.install_panel_lifecycle({'HERMES_SERVE_PRODUCERS_EXTERNAL': '0'})
    try:
        assert read_mode(served) == {'external': False, 'revision': 2}
        assert lifecycle.state == 'off' and lifecycle.relay is None
    finally:
        lifecycle.stop_watching()
        if lifecycle.relay is not None:
            api.detach_external(lifecycle.relay)
            lifecycle.relay.close(timeout=10)


def test_master_off_start_publishes_off_and_installs_no_child(served, monkeypatch):
    api = load_api('_mode_master_api')
    monkeypatch.setattr(api, 'start_publication', lambda: True)
    write_mode(served, external=True)
    lifecycle = api.install_panel_lifecycle({'HERMES_SERVE_PANEL_EXTERNAL': 'true',
                                             'HERMES_SERVE_PRODUCERS_EXTERNAL': '0'})
    try:
        assert read_mode(served) == {'external': False, 'revision': 2}
        assert lifecycle.state == 'off' and lifecycle.relay is None
    finally:
        lifecycle.stop_watching()
