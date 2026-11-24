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
    yield tmp_path
    core = sys.modules.get(FEED_KEY)
    feed = getattr(core, 'PROCESS_FEED', None)
    if feed is not None:
        feed.stopped.set()
        core.PROCESS_FEED = None


def test_unset_env_leaves_serve_untouched(served):
    api = load_api('_mode_unset_api')
    assert api.install_panel_lifecycle({}) is None
    assert not (served / 'serve-producers').exists(), 'no selection state without an explicit stage selector'


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
