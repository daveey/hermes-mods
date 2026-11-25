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


def test_status_wire_matches_legacy_schema_and_never_reports_a_false_stop(home, monkeypatch):
    api = load('_parity_status_api', 'plugin_api.py')
    legacy = legacy_feed(api)
    provider = provider_for(api)
    monkeypatch.setattr(time, 'time', lambda: WALL)
    monkeypatch.setattr(time, 'monotonic', lambda: 100.0)
    monkeypatch.setattr(time, 'perf_counter', lambda: 100.0)
    try:
        provider.feed.tick()
        legacy.tick()
        raw = provider({'body': {'_verb': 'status', '_wire_identity': identity(api)}, 'accepted': 100.0})
        got = json.loads(raw)
        want = api.json_safe({'feed': api.FEED_ID, 'profile': api.LAUNCH_PROFILE,
                              'running': not legacy.stopped.is_set(), **legacy.status()})
        assert got['running'] is True
        assert raw == json.dumps(want, ensure_ascii=False, separators=(',', ':')).encode()
        provider.feed.stopped.set()
        stopped = json.loads(provider({'body': {'_verb': 'status', '_wire_identity': identity(api)},
                                       'accepted': time.monotonic()}))
        assert stopped['running'] is False
    finally:
        provider.close()
        legacy.stopped.set()


@pytest.mark.parametrize('verb', ['status', 'decision-refresh'])
def test_nonfinite_fields_use_the_same_json_safe_as_legacy(home, monkeypatch, verb):
    api = load('_parity_json_safe_api', 'plugin_api.py')
    provider = provider_for(api)
    try:
        if verb == 'status':
            monkeypatch.setattr(provider.feed, 'status', lambda: {'age_s': float('nan')})
            want = {'feed': api.FEED_ID, 'profile': api.LAUNCH_PROFILE, 'running': True, 'age_s': None}
        else:
            monkeypatch.setattr(provider.feed, 'refresh_decision', lambda *a, **k: {'bundle': {'age_s': float('inf')}})
            monkeypatch.setattr(provider.feed, 'decision_projection', lambda bundle: bundle)
            want = {'feed': api.FEED_ID, 'profile': api.LAUNCH_PROFILE, 'bundle': {'age_s': None}}
        raw = provider({'body': {'_verb': verb, 'session': 'chat', '_wire_identity': identity(api)},
                        'accepted': time.monotonic()})
        assert raw == json.dumps(want, ensure_ascii=False, separators=(',', ':')).encode()
    finally:
        provider.close()


def test_warm_failed_changes_remain_untrusted_byte_parity_then_recover(home, monkeypatch):
    api = load('_parity_failed_warm_api', 'plugin_api.py')
    legacy = legacy_feed(api)
    provider = provider_for(api)
    monkeypatch.setattr(time, 'time', lambda: WALL)
    body = {'sessions': ['chat'], '_wire_identity': identity(api)}
    try:
        for feed in (legacy, provider.feed):
            feed.changes_bytes(['chat'])
            feed._drain()
            feed.tick()
            feed.failures['chat'] = {'error': 'RuntimeError: source failed'}
            feed._publish_wire()
        want = api._WIRE_PREFIX + legacy.prepared_changes_bytes(['chat'])[1:]
        got = provider({'body': body, 'accepted': time.monotonic()})
        assert json.loads(got)['bundles']['chat']['model']['freshness']['trusted'] is False
        assert got == want
        for feed in (legacy, provider.feed):
            feed.failures.clear()
            feed._publish_wire()
        recovered = provider({'body': body, 'accepted': time.monotonic()})
        assert json.loads(recovered)['bundles']['chat']['model']['freshness']['trusted'] is True
        assert recovered == api._WIRE_PREFIX + legacy.prepared_changes_bytes(['chat'])[1:]
    finally:
        provider.close()
        legacy.stopped.set()


@pytest.mark.parametrize('held', [False, True])
def test_adapter_refusal_never_serves_even_held_wire(home, monkeypatch, held):
    api = load('_parity_refused_api', 'plugin_api.py')
    provider = provider_for(api)
    try:
        if held:
            provider.feed.changes_bytes(['chat'])
            provider.feed._drain()
            provider.feed.tick()
        provider.feed.failures['chat'] = {'error': 'source failed'}
        error = api._home_context_mod().UnsupportedHomeAdapter('hub_db')
        monkeypatch.setattr(provider, '_unavailable_error', lambda sessions: error)
        with pytest.raises(type(error)):
            provider({'body': {'sessions': ['chat'], '_wire_identity': identity(api)},
                      'accepted': time.monotonic()})
    finally:
        provider.close()


def test_cold_failed_capture_cannot_publish_empty_success(home):
    api = load('_parity_cold_failed_api', 'plugin_api.py')
    provider = provider_for(api)
    try:
        provider.feed.failures['chat'] = {'error': 'source failed'}
        with pytest.raises(RuntimeError, match='capture unavailable'):
            provider({'body': {'sessions': ['chat'], '_wire_identity': identity(api)},
                      'accepted': time.monotonic()})
    finally:
        provider.close()


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


def test_prepared_wire_read_is_side_effect_free_but_cold_http_wakes_the_lane(home):
    api = load('_parity_cold_wire_api', 'plugin_api.py')
    provider = provider_for(api)
    f = provider.feed
    try:
        f._cold.clear()
        f._wake.clear()
        f.prepared_changes_bytes(['chat'])
        assert not f._cold.is_set() and not f._wake.is_set()
        assert f._registrations.empty() and f._board_demands.empty()
        f.changes_bytes(['chat'])
        assert f._cold.is_set() and f._wake.is_set()
        f._drain()
        f.tick()
        assert 'chat' in f._wire[2]
        f._cold.clear()
        f._wake.clear()
        f.changes_bytes(['chat'])
        assert not f._cold.is_set() and not f._wake.is_set(), 'warm reads do not force cold recompute'
    finally:
        provider.close()


def test_legacy_queue_clock_stays_distinct_from_explicit_acceptance_clock(home):
    api = load('_parity_registration_clock_api', 'plugin_api.py')
    provider = provider_for(api)
    f = provider.feed
    try:
        f.changes_bytes(['legacy'])
        f.changes_bytes(['external'], accepted_at=WALL - 70)
        f.clock = lambda: WALL + 100
        f._drain()
        assert f.interest['legacy'] == WALL + 100 + 900
        assert f.interest['external'] == WALL - 70 + 900
    finally:
        provider.close()


def test_delayed_board_demand_keeps_original_75_second_deadline(home, monkeypatch):
    api = load('_parity_board_delay_api', 'plugin_api.py')
    provider = provider_for(api)
    monkeypatch.setattr(time, 'monotonic', lambda: 170.0)
    try:
        body = {'sessions': ['chat'], '_wire_identity': identity(api)}
        provider({'body': body, 'accepted': 100.0})
        demands = []
        while not provider.feed._board_demands.empty():
            demands.append(provider.feed._board_demands.get_nowait())
        assert demands and all(until == WALL + 5 for _, until in demands), demands
        assert provider.feed.interest['chat'] == WALL + 830
    finally:
        provider.close()


def test_event_reset_does_not_renew_http_interest_or_board_visibility(home):
    api = load('_parity_reset_clock_api', 'plugin_api.py')
    provider = provider_for(api)
    try:
        body = {'sessions': ['chat'], '_wire_identity': identity(api)}
        provider({'body': body, 'accepted': time.monotonic()})
        provider.feed.tick()
        until = provider.feed.interest['chat']
        provider.feed.clock = lambda: WALL + 100
        while not provider.feed._board_demands.empty():
            provider.feed._board_demands.get_nowait()
        batch = provider.events_since(-1)
        assert batch['reset'] is True and 'chat' in batch['snapshot']['bundles']
        assert provider.feed.interest['chat'] == until, 'transport reset is not browser acceptance'
        assert provider.feed._board_demands.empty()
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
