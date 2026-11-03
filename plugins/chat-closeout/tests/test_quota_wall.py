"""A chat whose exact-pinned subscription model is quota-walled is a clock wait, not a broken relay path."""
import json
import sqlite3
import sys
import types

from test_relay_retry import setup, plugin

ASK = {'ask': {'status': 'blocked', 'block_kind': 'needs_input', 'human_request': {
    'action': 'Choose', 'why_human': 'Only you know', 'interaction': 'reply',
    'scope': 'account', 'evidence': 'Two accounts'}}, 'work': {'status': 'running'}}


def test_quota_walled_relay_backs_off_to_reset_and_is_not_flagged(tmp_path, monkeypatch):
    led, calls, tick = setup(tmp_path, monkeypatch, 'error')
    monkeypatch.setattr(plugin, '_quota_wall', lambda sid, now=None: 1000 + 1800)
    tick(1000)
    row = led.get('chat')
    assert row['wake_result'] == 'quota' and row['retry_after'] == 2800  # reset sooner than the hourly cap
    tick(1700)  # past the plain 10-min error backoff: still waiting for the reset
    assert len(calls) == 1
    plugin._state['mono_seen'] = {}
    filed = []
    args = dict(unseen={}, force=True, post=lambda *a: filed.append(a), read=lambda _: ASK)
    plugin.watchdog_tick(now=1001, mono=1, **args)
    assert not plugin.watchdog_tick(now=2902, mono=1902, **args)
    assert filed == []


def test_far_reset_is_rechecked_hourly(tmp_path, monkeypatch):
    led, calls, tick = setup(tmp_path, monkeypatch, 'error')
    monkeypatch.setattr(plugin, '_quota_wall', lambda sid, now=None: 1000 + 86400)
    tick(1000)
    assert led.get('chat')['retry_after'] == 1000 + plugin.QUOTA_RECHECK_S
    tick(1000 + plugin.QUOTA_RECHECK_S)
    assert len(calls) == 2


def test_error_without_wall_still_flags(tmp_path, monkeypatch):
    led, _, tick = setup(tmp_path, monkeypatch, 'error')
    monkeypatch.setattr(plugin, '_quota_wall', lambda sid, now=None: None)
    tick(1000)
    assert led.get('chat')['wake_result'] == 'error'
    plugin._state['mono_seen'] = {}
    filed = []
    args = dict(unseen={}, force=True, post=lambda *a: filed.append(a), read=lambda _: ASK)
    plugin.watchdog_tick(now=1001, mono=1, **args)
    assert plugin.watchdog_tick(now=2902, mono=1902, **args)
    assert len(filed) == 1


def test_wall_lifted_while_quota_recorded_flags_again(tmp_path, monkeypatch):
    """A stale ``quota`` result never hides a failure once the wall is gone (e.g. the user switched model)."""
    led, _, tick = setup(tmp_path, monkeypatch, 'error')
    monkeypatch.setattr(plugin, '_quota_wall', lambda sid, now=None: now + 100)
    tick(1000)
    monkeypatch.setattr(plugin, '_quota_wall', lambda sid, now=None: None)
    plugin._state['mono_seen'] = {}
    filed = []
    args = dict(unseen={}, force=True, post=lambda *a: filed.append(a), read=lambda _: ASK)
    plugin.watchdog_tick(now=1001, mono=1, **args)
    assert plugin.watchdog_tick(now=2902, mono=1902, **args)


def _session_db(tmp_path, routing):
    db = tmp_path / 'state.db'
    c = sqlite3.connect(db)
    c.execute('CREATE TABLE sessions (id TEXT, model_config TEXT, source TEXT)')
    c.execute('INSERT INTO sessions VALUES (?,?,?)', ('chat', json.dumps({'model_routing': routing}), 'desktop'))
    c.commit()
    c.close()
    return db


def _fake_core(monkeypatch, tmp_path, resets_at):
    seen = {}
    hc = types.ModuleType('hermes_constants')
    hc.get_hermes_home = lambda: tmp_path
    cp = types.ModuleType('agent.credential_pool')
    cp.load_pool = lambda provider, allowed_auth_types=None: seen.setdefault('pool', (provider, allowed_auth_types))
    mru = types.ModuleType('agent.model_routing_unavailable')
    mru.quota_unavailable = lambda pool, model: (seen.setdefault('model', model) and
                                                  (types.SimpleNamespace(resets_at=resets_at) if resets_at else None))
    agent = sys.modules.get('agent') or types.ModuleType('agent')
    for name, mod in {'hermes_constants': hc, 'agent': agent, 'agent.credential_pool': cp,
                      'agent.model_routing_unavailable': mru}.items():
        monkeypatch.setitem(sys.modules, name, mod)
    return seen


PIN = {'exact_pin': True, 'billing_group': 'subscription', 'selected_provider': 'openai-codex',
       'selected_model': 'gpt-x'}


def test_probe_reads_exact_pin_and_oauth_pool(tmp_path, monkeypatch):
    db = _session_db(tmp_path, PIN)
    monkeypatch.setattr(plugin, 'state_dbs', lambda: [db])
    seen = _fake_core(monkeypatch, tmp_path, 5000.0)
    assert plugin._quota_wall('chat', now=1000) == 5000.0
    assert seen['pool'] == ('openai-codex', {'oauth'}) and seen['model'] == 'gpt-x'
    assert plugin._quota_wall('chat', now=6000) is None  # a past reset is no wall


def test_probe_ignores_class_routes_and_missing_cooldown(tmp_path, monkeypatch):
    db = _session_db(tmp_path, {**PIN, 'exact_pin': False})
    monkeypatch.setattr(plugin, 'state_dbs', lambda: [db])
    _fake_core(monkeypatch, tmp_path, 5000.0)
    assert plugin._quota_wall('chat', now=1000) is None  # a class route fails over by itself
    db2 = _session_db(tmp_path / 'x' if (tmp_path / 'x').mkdir() is None else tmp_path, PIN)
    monkeypatch.setattr(plugin, 'state_dbs', lambda: [db2])
    _fake_core(monkeypatch, tmp_path / 'x', None)
    assert plugin._quota_wall('chat', now=1000) is None  # no known cooldown = not quota evidence


def test_probe_skips_other_profile_home(tmp_path, monkeypatch):
    db = _session_db(tmp_path, PIN)
    monkeypatch.setattr(plugin, 'state_dbs', lambda: [db])
    _fake_core(monkeypatch, tmp_path / 'elsewhere', 5000.0)
    assert plugin._quota_wall('chat', now=1000) is None
