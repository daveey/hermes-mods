"""Plugin load cost and timed-out-load behavior (no credentials, no provider traffic)."""
import importlib.util
import json
import logging
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import unittest

sys.path.insert(0, str(Path(__file__).parent))
import test_selection_backend as backend  # fixtures only; its TestCase is not re-collected here
from test_request_selection import entry

PLUGIN = Path(__file__).parents[1]


def load_plugin(name):
    spec = importlib.util.spec_from_file_location(name, PLUGIN / '__init__.py')
    plugin = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(plugin)
    return plugin


class Ctx(SimpleNamespace):
    def __init__(self, abandoned=False):
        super().__init__(hooks={}, middleware=[], cleanup=[], _load_abandoned=abandoned)

    def register_hook(self, name, cb):
        self.hooks[name] = cb

    def register_middleware(self, kind, cb):
        self.middleware.append(cb)

    def on_unload(self, cb):
        self.cleanup.append(cb)


class Load(unittest.TestCase):
    def setUp(self):
        import gc
        gc.collect()
        backend.Backend.setUp(self)
        from hermes_logging import _reset_queued_handlers
        self.addCleanup(_reset_queued_handlers)
    credentials = backend.Backend.credentials

    def register(self, plugin, ctx):
        self.addCleanup(lambda: [fn() for fn in reversed(ctx.cleanup)])
        plugin.register(ctx)
        return ctx

    def integration(self):
        integration = self.s.ExecutionIntegration(observe=lambda *args: None)
        self.addCleanup(integration.unload)
        return integration

    def test_register_does_not_import_dashboard_api(self):
        # fastapi + pydantic cost ~4 s cold under load; register() must not pay for the REST module.
        loaded = []
        real = importlib.util.spec_from_file_location
        def spy(name, location, *args, **kwargs):
            loaded.append(Path(location).name)
            return real(name, location, *args, **kwargs)
        plugin = load_plugin('account_usage_load_noapi')
        with patch('importlib.util.spec_from_file_location', spy):
            ctx = self.register(plugin, Ctx())
        self.assertNotIn('plugin_api.py', loaded)
        self.assertIs(sys.modules['account_usage_selection'], self.s)
        self.assertTrue(ctx.middleware[0].__self__.active)

    def test_warm_fingerprint_cache_installs_without_compiling(self):
        first = self.integration()
        first.install()
        first.unload()
        cache = self.s._fingerprint_cache_path(first.home)
        self.assertTrue(cache.exists())
        with patch.object(self.s, 'compile', side_effect=AssertionError('compiled on warm cache'), create=True):
            second = self.integration()
            second.install()
        self.assertTrue(second.active)

    def test_stale_cache_falls_back_to_compile_and_heals(self):
        first = self.integration()
        first.install()
        first.unload()
        cache = self.s._fingerprint_cache_path(first.home)
        data = json.loads(cache.read_text())
        for key in data['files']:
            data['files'][key] = []
        cache.write_text(json.dumps(data))
        second = self.integration()
        second.install()
        self.assertTrue(second.active)
        self.assertTrue(all(json.loads(cache.read_text())['files'].values()))

    def test_corrupt_cache_is_ignored(self):
        integration = self.integration()
        cache = self.s._fingerprint_cache_path(integration.home)
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text('{not json')
        integration.install()
        self.assertTrue(integration.active)

    def test_warm_cache_still_refuses_customized_seam(self):
        from agent import client_lifecycle
        from agent.client_lifecycle import ClientLifecycleMixin
        warm = self.integration()
        warm.install()
        warm.unload()
        # Same file name as the audited module, different body: only the code check can tell.
        scope = {}
        exec(compile('def _create_request_openai_client(self, *a, **k):\n    return None\n',
                     client_lifecycle.__file__, 'exec'), scope)
        original = ClientLifecycleMixin._create_request_anthropic_client
        integration = self.integration()
        with patch.object(ClientLifecycleMixin, '_create_request_openai_client',
                          scope['_create_request_openai_client']):
            with self.assertRaises(self.s.SelectionRejected):
                integration.install()
        self.assertIs(ClientLifecycleMixin._create_request_anthropic_client, original)
        self.assertFalse(integration.patches)

    def test_abandoned_load_restores_core_and_refuses_selection_visibly(self):
        from agent.client_lifecycle import ClientLifecycleMixin
        from agent.account_usage_cache import _identity_id_for
        original = ClientLifecycleMixin._create_request_anthropic_client
        self.credentials([entry('B', source='manual').to_dict()])
        service = self.s.SelectionService(self.home, server=self.server)
        account = _identity_id_for('anthropic', entry('B'))
        queued = service.put('runtime-A', provider='anthropic', account_id=account, expected_revision=0)
        plugin = load_plugin('account_usage_load_abandoned')
        with self.assertLogs('account_usage_load_abandoned', level='WARNING') as logs:
            ctx = self.register(plugin, Ctx(abandoned=True))
        self.addCleanup(self.s._install_failures.pop, str(self.home.resolve()), None)
        self.addCleanup(self.s._load_abandoned.pop, str(self.home.resolve()), None)
        text = '\n'.join(logs.output)
        self.assertIn(f'pid={os.getpid()}', text)
        self.assertIn('role=', text)
        # No middleware will ever consume, so nothing stays patched and nothing is half-registered.
        self.assertEqual(ctx.middleware, [])
        self.assertEqual(ctx.cleanup, [])
        self.assertIs(ClientLifecycleMixin._create_request_anthropic_client, original)
        # A selection queued for a chat this process serves is refused visibly, not left pending.
        got = service.get('runtime-A')
        self.assertEqual(got['state'], 'rejected')
        self.assertGreater(got['revision'], queued['revision'])
        self.assertIn('restart', got['error'])
        with self.assertRaises(self.s.SelectionRejected):
            service.put('runtime-A', provider='anthropic', account_id=account,
                        expected_revision=got['revision'])

    def test_load_that_times_out_mid_register_also_falls_back(self):
        from agent.client_lifecycle import ClientLifecycleMixin
        original = ClientLifecycleMixin._create_request_anthropic_client
        plugin = load_plugin('account_usage_load_late')
        ctx = Ctx()
        install = self.s.ExecutionIntegration.install
        def slow_install(integration):
            install(integration)
            ctx._load_abandoned = True  # core's deadline fired while install() ran
        with patch.object(self.s.ExecutionIntegration, 'install', slow_install), \
                self.assertLogs('account_usage_load_late', level='WARNING'):
            self.register(plugin, ctx)
        self.addCleanup(self.s._install_failures.pop, str(self.home.resolve()), None)
        self.addCleanup(self.s._load_abandoned.pop, str(self.home.resolve()), None)
        self.assertEqual(ctx.middleware, [])
        self.assertIs(ClientLifecycleMixin._create_request_anthropic_client, original)

    def test_slow_load_is_logged_with_pid_and_role(self):
        plugin = load_plugin('account_usage_load_slow')
        with patch.object(plugin, '_SLOW_LOAD_SECS', 0.0), \
                patch.dict(os.environ, {'HERMES_KANBAN_TASK': 't_fixture'}), \
                self.assertLogs('account_usage_load_slow', level='WARNING') as logs:
            ctx = self.register(plugin, Ctx())
        self.assertTrue(ctx.middleware[0].__self__.active)
        self.assertIn(f'pid={os.getpid()}', logs.output[0])
        self.assertIn('kanban-worker', logs.output[0])

    def test_fast_load_is_quiet(self):
        plugin = load_plugin('account_usage_load_fast')
        logger = logging.getLogger('account_usage_load_fast')
        with patch.object(plugin, '_SLOW_LOAD_SECS', 1e9), patch.object(logger, 'warning') as warn:
            self.register(plugin, Ctx())
        warn.assert_not_called()


if __name__ == '__main__':
    unittest.main()
