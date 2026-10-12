"""Stage under core tests/agent so the upstream real-home safety fixtures apply."""
import os
from pathlib import Path
import shutil

import pytest


@pytest.fixture(autouse=True)
def loaded_local_plugin(monkeypatch, tmp_path):
    if os.environ.get('FAST_RATE_LIMIT_TEST_DISABLE') == '1':
        yield None
        return
    from hermes_constants import get_hermes_home
    from hermes_cli.plugins import PluginManager
    source = Path(os.environ['FAST_RATE_LIMIT_PLUGIN_SOURCE'])
    target = get_hermes_home() / 'plugins' / 'fast-rate-limit-fallback'
    shutil.copytree(source, target, ignore=shutil.ignore_patterns('tests', '__pycache__'))
    # Isolated test-home fixture, never the user's config.
    (get_hermes_home() / 'config.yaml').write_text('plugins:\n  enabled: [fast-rate-limit-fallback]\n')
    manager = PluginManager()
    manager.discover_and_load()
    plugin = manager._plugins['fast-rate-limit-fallback']
    assert not plugin.error, plugin.error
    import agent.agent_runtime_helpers as helpers
    assert getattr(helpers._recover_rate_limit, '_fast_rate_limit_fallback', False)
    yield manager
    manager.unload()
