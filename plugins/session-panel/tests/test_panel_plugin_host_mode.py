"""User-plugin hosts join the same installation mode seam, not an env-only legacy fork."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from test_explicit_home_context import ROOT
import test_explicit_home_acquisition as helpers


@pytest.mark.parametrize('name,role,marker,expected', [
    ('hermes_dashboard_plugin_session-panel', 'proc', '1', True),
    ('hermes_dashboard_plugin_session-panel', 'proc', '0', False),
    ('hermes_dashboard_plugin_session-panel', 'proc', '', False),
    ('_panel_producer_api', 'proc', '1', False),
    ('hermes_dashboard_plugin_session-panel', 'serve', '', True),
    ('hermes_dashboard_plugin_session-panel', 'dashboard', '', True),
])
def test_supported_plugin_host_bootstrap_role(api, monkeypatch, name, role, marker, expected):
    monkeypatch.setenv('HERMES_PLUGIN_HOST_PROCESS', marker)
    assert api._serves_desktop(name, role) is expected


api = helpers.api


def test_real_plugin_host_watches_the_installation_mode_not_only_its_environment(tmp_path, monkeypatch):
    from hermes_cli.plugin_host import PluginHost
    from hermes_cli.serve_producer_mode import read_mode, write_mode
    from tests.waiting import wait_until
    root = tmp_path / '.hermes'
    home = root / 'profiles' / 'named'
    home.mkdir(parents=True)
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.setenv('HERMES_HOME', str(home))
    monkeypatch.delenv('HERMES_SERVE_PANEL_EXTERNAL', raising=False)
    monkeypatch.delenv('HERMES_SERVE_PRODUCERS_EXTERNAL', raising=False)
    write_mode(root, external=False)
    dashboard = tmp_path / 'fixture-dashboard'
    dashboard.mkdir()
    source = ROOT / 'dashboard' / 'plugin_api.py'
    (dashboard / 'plugin_api.py').write_text(f'''
import importlib.util
import sys
spec = importlib.util.spec_from_file_location('hermes_dashboard_plugin_session-panel-real', {str(source)!r})
api = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = api
spec.loader.exec_module(api)
router = api.router
@router.get('/_fixture_mode')
def fixture_mode():
    mode = getattr(api._LOADER, 'panel_lifecycle', None)
    return {{'installed': mode is not None, 'installation': str(mode.installation) if mode else None,
             'revision': mode.revision_applied if mode else None, 'state': mode.state if mode else None}}
@router.post('/_fixture_stop')
def fixture_stop():
    mode = getattr(api._LOADER, 'panel_lifecycle', None)
    if mode is not None:
        mode.stop_watching()
        if mode.relay is not None:
            mode.relay.retire(timeout=30)
    return {{'drained': api._retire_legacy_panel()}}
''')
    host = PluginHost(SimpleNamespace(home_path=home))
    def observe():
        result = host.asgi_request('fixture', str(dashboard), 'plugin_api.py', 'GET', '/_fixture_mode', '', [],
                                   b'', timeout=60, authenticated=True)
        return json.loads(result['body'])
    try:
        first = observe()
        pid = host.pid
        assert first['installed'], first
        assert first['installation'] == str(root)
        assert wait_until(lambda: observe()['revision'] == read_mode(root)['revision'], timeout=30)
        changed = write_mode(root, external=True)
        assert wait_until(lambda: observe()['revision'] == changed['revision'], timeout=30)
        assert host.pid == pid  # live plugin-host process, no restart to follow the setter
        assert not (home / 'serve-producers' / 'panel.mode.json').exists()
    finally:
        try:
            if host.alive:
                host.asgi_request('fixture', str(dashboard), 'plugin_api.py', 'POST', '/_fixture_stop', '', [],
                                  b'', timeout=60, authenticated=True)
        finally:
            host.shutdown()
