"""post_api_request -> `plugin.account-usage.changed` push: throttled per provider, only where the gateway server runs."""
import importlib.util
import os
import sys
import types
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

PLUGIN = Path(__file__).parents[1]


def load_plugin():
    spec = importlib.util.spec_from_file_location('_account_usage_push_test', PLUGIN / '__init__.py')
    plugin = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(plugin)
    return plugin


def fake_events(sent):
    mod = types.ModuleType('hermes_cli.plugin_events')
    mod.broadcast_plugin_event = lambda pid, ev, payload: sent.append((pid, ev, payload))
    pkg = sys.modules.get('hermes_cli') or types.ModuleType('hermes_cli')
    return {'hermes_cli': pkg, 'hermes_cli.plugin_events': mod, 'tui_gateway.server': types.ModuleType('tui_gateway.server')}


def test_push_is_throttled_per_provider():
    plugin = load_plugin()
    sent = []
    clock = [1000.0]
    with patch.dict(sys.modules, fake_events(sent)), patch.object(plugin.time, 'monotonic', lambda: clock[0]):
        assert plugin._push_changed('anthropic') is True
        assert plugin._push_changed('anthropic') is False
        assert plugin._push_changed('openai-codex') is True
        clock[0] += plugin.PUSH_MIN_GAP_S
        assert plugin._push_changed('anthropic') is True
    assert [(p, e, d['provider']) for p, e, d in sent] == [
        ('account-usage', 'changed', 'anthropic'), ('account-usage', 'changed', 'openai-codex'),
        ('account-usage', 'changed', 'anthropic')]
    assert all(set(d) == {'feed', 'provider', 'at'} for _, _, d in sent)


def test_no_push_without_a_gateway_server_in_process():
    plugin = load_plugin()
    sent = []
    mods = fake_events(sent)
    del mods['tui_gateway.server']
    with patch.dict(sys.modules, mods):
        sys.modules.pop('tui_gateway.server', None)
        assert plugin._push_changed('anthropic') is False
    assert sent == []


def test_post_api_request_hook_pushes():
    plugin = load_plugin()
    sent = []
    entry = SimpleNamespace(id='e1', label='A')
    pool = SimpleNamespace(entries=lambda: [entry], current=lambda: entry)
    agent = SimpleNamespace(_credential_pool=pool, _credential_pool_entry_id='e1', provider='anthropic', session_id='s1')
    with patch.dict(sys.modules, fake_events(sent)), patch.object(plugin, '_find_agent', lambda sid: agent), \
            patch.object(plugin, '_state_path', lambda: str(Path(os.environ['HERMES_HOME']) / 'last.json')), \
            patch.object(plugin, '_wire_account_id', lambda p, e: None):
        plugin._on_api(session_id='s1', provider='anthropic')
    assert [d['provider'] for _, _, d in sent] == ['anthropic']


def test_push_comes_after_the_last_used_write(tmp_path):
    plugin = load_plugin()
    seen = []
    state = tmp_path / 'last.json'
    entry = SimpleNamespace(id='e1', label='A')
    pool = SimpleNamespace(entries=lambda: [entry], current=lambda: entry)
    agent = SimpleNamespace(_credential_pool=pool, _credential_pool_entry_id='e1', provider='anthropic', session_id='s1')
    with patch.object(plugin, '_find_agent', lambda sid: agent), patch.object(plugin, '_state_path', lambda: str(state)), \
            patch.object(plugin, '_wire_account_id', lambda p, e: None), \
            patch.object(plugin, '_push_changed', lambda p: seen.append((p, state.exists()))):
        plugin._on_api(session_id='s1', provider='anthropic')
    assert seen == [('anthropic', True)]


def test_provider_without_a_pool_still_pushes():
    plugin = load_plugin()
    seen = []
    agent = SimpleNamespace(_credential_pool=None, provider='openrouter', session_id='s1')
    with patch.object(plugin, '_find_agent', lambda sid: agent), patch.object(plugin, '_push_changed', seen.append):
        plugin._on_api(session_id='s1', provider='OpenRouter')
    assert seen == ['openrouter']


def test_request_local_observer_pushes_and_the_hook_does_not_push_twice(tmp_path):
    plugin = load_plugin()
    seen = []
    entry = SimpleNamespace(id='e2', label='B')
    pool = SimpleNamespace(entries=lambda: [entry], current=lambda: entry)
    agent = SimpleNamespace(_credential_pool=pool, _account_usage_request_id='r1', provider='anthropic', session_id='s1')
    snapshot = SimpleNamespace(entry_id='e2', account_id='acct', provider='anthropic')
    with patch.object(plugin, '_state_path', lambda: str(tmp_path / 'last.json')), \
            patch.object(plugin, '_push_changed', seen.append), patch.object(plugin, '_find_agent', lambda sid: agent):
        plugin._observe_selected(agent, snapshot)
        assert seen == ['anthropic'] and (tmp_path / 'last.json').exists()
        plugin._on_api(session_id='s1', provider='anthropic', api_request_id='r1')
    assert seen == ['anthropic']
