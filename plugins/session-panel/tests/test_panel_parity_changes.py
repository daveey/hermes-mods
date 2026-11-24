"""P parity: the exec provider's complete /changes, /status and websocket bytes equal the legacy Feed route's
under equal sources, serving identity, epoch and injected clock (no field is dropped to manufacture equality)."""
import importlib.util
import json
import sqlite3
import sys
import time
from pathlib import Path

import pytest

core_runtime = pytest.importorskip('hermes_cli.serve_producer_runtime')

ROOT = Path(__file__).resolve().parents[1]
WALL = 1_000_000.0


def load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'dashboard' / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def home(tmp_path, monkeypatch):
    from hermes_cli.kanban_db_connect import connect
    from hermes_cli import kanban_db

    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.delenv('HERMES_KANBAN_DB', raising=False)
    monkeypatch.delenv('HERMES_KANBAN_HOME', raising=False)
    monkeypatch.setattr(sys, 'argv', ['hermes', 'serve', '--ssh-session-token-file', '/slots/desktop-a/token'])
    with sqlite3.connect(tmp_path / 'state.db') as db:
        db.execute('CREATE TABLE sessions(id TEXT PRIMARY KEY,parent_session_id TEXT,outcome_json TEXT)')
        db.execute("INSERT INTO sessions VALUES ('chat',NULL,'{}')")
    with connect(tmp_path / 'kanban.db') as db:
        kanban_db.create_task(db, title='Parity work', session_id='chat')
    return tmp_path


def settled(feed):
    feed.epoch = 'parity-epoch'
    feed.clock = lambda: WALL
    return feed


def legacy_feed(api):
    feed = settled(api._load_feed().Feed(api.CoreReaders(), emit=lambda payload: None))
    return feed


def provider_for(api):
    prov_mod = load('_parity_provider', 'producer_provider.py')
    provider = prov_mod.PanelProducer(api, start_lanes=False)
    settled(provider.feed)
    return provider


def identity(api):
    return {'feed': api.FEED_ID, 'profile': api.LAUNCH_PROFILE}


def test_changes_wire_bytes_equal_legacy_route(home, monkeypatch):
    api = load('_parity_changes_api', 'plugin_api.py')
    legacy = legacy_feed(api)
    provider = provider_for(api)
    monkeypatch.setattr(time, 'time', lambda: WALL)  # freshness stamps read the wall clock directly
    try:
        # legacy route: register, take in, one source pass, serve
        legacy.changes_bytes(['chat'])
        legacy._drain()
        legacy.tick()
        want = api._WIRE_PREFIX + legacy.changes_bytes(['chat'], since=0, epoch=None)[1:]

        body = {'sessions': ['chat'], 'since': 0, '_wire_identity': identity(api)}
        provider({'body': body, 'accepted': time.monotonic()})
        provider.feed.tick()
        got = provider({'body': body, 'accepted': time.monotonic()})
        parsed = json.loads(got)
        assert parsed['bundles']['chat']['tasks'][0]['title'] == 'Parity work'
        assert got == want, (got[820:900], want[820:900])
        # unchanged entries are omitted identically on the next poll
        rev = parsed['rev']
        again = {'sessions': ['chat'], 'since': rev, 'epoch': 'parity-epoch', '_wire_identity': identity(api)}
        assert provider({'body': again, 'accepted': time.monotonic()}) == (
            api._WIRE_PREFIX + legacy.changes_bytes(['chat'], since=rev, epoch='parity-epoch')[1:])
        # an unknown epoch resets identically
        other = {'sessions': ['chat'], 'since': rev, 'epoch': 'other-epoch', '_wire_identity': identity(api)}
        reset = provider({'body': other, 'accepted': time.monotonic()})
        assert json.loads(reset)['reset'] is True
        assert reset == api._WIRE_PREFIX + legacy.changes_bytes(['chat'], since=rev, epoch='other-epoch')[1:]
    finally:
        provider.close()
        legacy.stopped.set()


def test_status_wire_matches_legacy_schema_and_never_reports_a_false_stop(home):
    api = load('_parity_status_api', 'plugin_api.py')
    legacy = legacy_feed(api)
    provider = provider_for(api)
    try:
        provider.feed.tick()
        legacy.tick()
        got = json.loads(provider({'body': {'_verb': 'status', '_wire_identity': identity(api)},
                                   'accepted': time.monotonic()}))
        want = api.json_safe({'feed': api.FEED_ID, 'profile': api.LAUNCH_PROFILE,
                              'running': not legacy.stopped.is_set(), **legacy.status()})
        assert got['running'] is True and set(got) == set(want)
        for key in want:
            if key not in ('epoch', 'timing', 'ages', 'computed_at'):
                assert got[key] == want[key], key
        provider.feed.stopped.set()
        stopped = json.loads(provider({'body': {'_verb': 'status', '_wire_identity': identity(api)},
                                       'accepted': time.monotonic()}))
        assert stopped['running'] is False
    finally:
        provider.close()
        legacy.stopped.set()


def test_decision_conflict_is_a_publication_not_a_transport_failure(home):
    api = load('_parity_decision_api', 'plugin_api.py')
    provider = provider_for(api)
    try:
        wire = provider({'body': {'_verb': 'decision-refresh', 'session': 'never-watched',
                                  '_wire_identity': identity(api)}, 'accepted': time.monotonic()})
        assert json.loads(wire) == {'_conflict': 'Session panel is not hydrated'}
    finally:
        provider.close()


@pytest.mark.parametrize('age,expect', [(0, 'ok'), (900, 'expired')])
def test_acceptance_clock_75_900_runs_from_original_http_acceptance(home, age, expect):
    api = load('_parity_clock_api', 'plugin_api.py')
    provider = provider_for(api)
    try:
        body = {'sessions': ['chat'], '_wire_identity': identity(api), 'subscriber': 's', 'visible_session': 'chat'}
        payload = {'body': body, 'accepted': time.monotonic() - age}
        if expect == 'expired':
            with pytest.raises(RuntimeError, match='expired browser interest'):
                provider(payload)
            return
        provider(payload)
        assert provider.feed.interest['chat'] - WALL <= 900 and provider._leases['s'][1] - WALL <= 75
    finally:
        provider.close()


def test_delayed_acceptance_shortens_the_lease_by_the_queue_delay(home):
    api = load('_parity_delay_api', 'plugin_api.py')
    provider = provider_for(api)
    try:
        body = {'sessions': ['chat'], '_wire_identity': identity(api), 'subscriber': 's', 'visible_session': 'chat'}
        provider({'body': body, 'accepted': time.monotonic() - 70})
        remaining = provider._leases['s'][1] - WALL
        assert 0 < remaining <= 5.5, remaining  # 75s counted from the browser request, not child dequeue
        interest = provider.feed.interest['chat'] - WALL
        assert 820 < interest <= 830.5, interest
    finally:
        provider.close()
