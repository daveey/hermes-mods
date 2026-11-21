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
