"""Production contract regressions; fixtures are generic and never sample telemetry."""
import asyncio
import contextvars
import copy
import sys
from types import SimpleNamespace

import pytest

from test_snapshot_and_stream import api, fixture_adapter, run


def test_current_repair_sync_and_auth_enter_machinery_and_clear():
    adapter = fixture_adapter()
    adapter.repair = lambda: {'findings': [
        {'id': 'BS-overdue-wake', 'card': 'gate', 'title': 'Admission', 'summary': 'Wake overdue'},
        {'id': 'BS-quiet-chat', 'chat': 'quiet', 'title': 'Quiet chat'},
        {'id': 'BS-no-progress', 'chat': 'quiet', 'title': 'Quiet chat'},
    ]}
    adapter.sync = lambda: {'last_ok': 100, 'last_error': 'rejected'}
    adapter.auth = lambda: {'anthropic': {'logged_in': False}}
    result = run(adapter)
    assert {r['key'] for r in result['machinery']} == {'gate', 'quiet', 'sync:rejected', 'auth:anthropic'}
    assert result['totals']['machinery'] == 4
    assert next(r for r in result['machinery'] if r['key'] == 'gate')['finding']['id'] == 'BS-overdue-wake'
    assert 'Sync rejected' in result['telemetry']['alert_label']
    assert 'authentication dropped' in result['telemetry']['alert_label']
    assert run()['totals']['machinery'] == 1


@pytest.mark.parametrize('partial', [
    {'flow_complete': False, 'incomplete_status_lanes': ['work']},
    {'summary_complete': False},
    {'governors': {'available': False, 'incomplete_lanes': ['work'],
                   'scope': [{'board': 'sibling', 'available': False, 'flow_available': False}]}},
])
def test_partial_sibling_board_then_healthy_recovery(partial):
    adapter = fixture_adapter()
    healthy = run(adapter)
    lanes = copy.deepcopy(adapter.lanes())
    lanes.update(partial)
    adapter.lanes = lambda: lanes
    missing = run(adapter)
    assert 'machinery' in missing['unknown_counts']
    assert missing['totals']['machinery'] is None
    assert missing['totals']['filtered']['machinery'] is None
    assert missing['lanes'][0]['status'] == 'unknown'
    assert missing['lanes'][0]['unknown']
    if 'governors' not in partial:
        assert missing['totals']['sessions'] is None
        assert missing['lanes'][0]['running'] is None
    assert missing['cursor'] != healthy['cursor']
    recovered = run()
    assert recovered['cursor'] == healthy['cursor']
    assert recovered['unknown_counts'] == []
    assert api.delta(missing, recovered)['changes']['lanes'][0]['status'] == 'healthy'


def test_chain_objects_are_exclusive_and_counts_match_visible_lists():
    adapter = fixture_adapter()
    panel = adapter.sessions()
    panel['bundles']['build'] = {'bundle': {'session': {'title': 'Shared build', 'status': 'running'}}}
    adapter.sessions = lambda: panel
    result = run(adapter)
    objects = result['human'] + result['machinery'] + result['sessions']
    for chain in result['chains']:
        objects += [chain['prerequisite'], *chain['waiters']]
    keys = [r['key'] for r in objects]
    assert len(keys) == len(set(keys))
    for section in ('human', 'machinery'):
        assert result['totals'][section] == len(result[section])
    # Running prerequisites are visible session objects too, counted exactly once.
    assert result['totals']['sessions'] == len(result['sessions']) + len(result['chains'])
    assert result['totals']['running'] == 2
    assert result['totals']['running'] == sum(r['state'] == 'running' for r in result['sessions']) + len(result['chains'])


def test_real_telemetry_capacity_and_missing_forecasts():
    adapter = fixture_adapter()
    lanes = adapter.lanes()
    lanes['fleet_slots'] = 8
    adapter.sync = lambda: {'last_ok': 123, 'last_error': None, 'outbox': 2}
    result = run(adapter)
    telemetry = result['telemetry']
    assert telemetry['hub']['state'] == 'unknown'
    assert 'hub-status' in telemetry['hub']['reason']
    assert telemetry['fleet']['state'] == 'unknown'
    assert 'slots' not in telemetry['fleet']  # lane capacity is not observed occupancy
    assert telemetry['ci']['state'] == 'unknown'
    assert telemetry['subscription']['state'] == 'unknown'


def test_producer_reuses_exact_mounted_identity(monkeypatch):
    for name in ('lanes', 'session-panel'):
        mounted = SimpleNamespace(__file__=str(api.ROOT / name / 'dashboard' / 'plugin_api.py'))
        monkeypatch.setitem(sys.modules, 'hermes_dashboard_plugin_' + name, mounted)
        assert api.producer(name) is mounted


def test_profile_context_is_retained_in_threaded_adapters(monkeypatch, tmp_path):
    selected = contextvars.ContextVar('selected_home', default='default')
    seen = []
    adapter = fixture_adapter()
    original = adapter.sessions
    def sessions():
        seen.append(selected.get())
        return original()
    adapter.sessions = sessions
    async def check():
        token = selected.set(str(tmp_path / 'secondary'))
        try:
            await api.snapshot(adapter=adapter)
        finally:
            selected.reset(token)
    asyncio.run(check())
    assert seen == [str(tmp_path / 'secondary')]


def test_secondary_profile_never_reads_launch_feed(monkeypatch):
    feed = SimpleNamespace(snapshot=lambda **kw: pytest.fail('foreign profile feed read'),
                           stopped=SimpleNamespace(is_set=lambda: False))
    module = SimpleNamespace(_request_profile=lambda: 'secondary', LAUNCH_PROFILE='default',
                             load_snapshot=lambda: pytest.fail('launch cache read'))
    monkeypatch.setattr(api, 'producer', lambda name: module)
    monkeypatch.setitem(sys.modules, '_session_panel_feed_core', SimpleNamespace(PROCESS_FEED=feed))
    with pytest.raises(RuntimeError, match='request profile unavailable'):
        api.read_sessions()


def test_routes_preserve_gateway_and_valid_explicit_sdk_mode():
    row = api.session_row({'session_id': 'chat', 'gateway': 'gateway-a', 'profile': 'secondary',
                           'route': {'connectionId': 'desktop-connection', 'mode': 'remote'}})
    assert row['route'] == {'connectionId': 'desktop-connection', 'mode': 'remote',
                            'gatewayId': 'gateway-a', 'profile': 'secondary', 'targetProfile': 'secondary'}
    assert 'mode' not in api.session_row({'route': {'mode': 'session'}})['route']
