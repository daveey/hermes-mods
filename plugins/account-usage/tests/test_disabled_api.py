"""GET/POST /disabled: switch an account off/on through core's disabled-credentials store (stubbed pool)."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest

from fastapi import HTTPException

_spec = importlib.util.spec_from_file_location(
    'account_usage_plugin_api_disabled', Path(__file__).resolve().parents[1] / 'dashboard' / 'plugin_api.py')
api = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(api)


class Store:
    """Stands in for core's set/is_credential_disabled: rows keyed by id, plus a root-held set."""

    def __init__(self, pools, root=()):
        self.pools, self.off, self.root = pools, set(), set(root)

    def tools(self):
        load_pool = lambda provider: SimpleNamespace(entries=lambda: list(self.pools.get(provider, [])))
        ident = lambda provider, e: e.ident
        is_off = lambda provider, e: e.id in self.off or e.id in self.root

        def set_off(provider, e, disabled):
            had = e.id in self.off
            (self.off.add if disabled else self.off.discard)(e.id)
            return had != disabled
        return load_pool, ident, is_off, set_off


def row(id, ident):
    return SimpleNamespace(id=id, ident=ident, label='acct')


class DisabledApiTests(unittest.TestCase):
    def test_disable_every_row_of_one_account_then_enable(self):
        st = Store({'anthropic': [row('a', 'X'), row('b', 'Y'), row('c', 'X')]})
        out = api.set_account_disabled('anthropic', 'X', True, st.tools())
        self.assertEqual(out, {'ok': True, 'changed': 2, 'disabled': True})
        self.assertEqual(st.off, {'a', 'c'})
        self.assertEqual(api.disabled_accounts(st.tools()), {'supported': True, 'accounts': {'anthropic': ['X']}})
        out = api.set_account_disabled('anthropic', 'X', False, st.tools())
        self.assertEqual(out, {'ok': True, 'changed': 2, 'disabled': False})
        self.assertEqual(api.disabled_accounts(st.tools()), {'supported': True, 'accounts': {}})

    def test_account_listed_only_when_every_row_is_disabled(self):
        st = Store({'openai-codex': [row('a', 'X'), row('b', 'X')]})
        st.off.add('a')
        self.assertEqual(api.disabled_accounts(st.tools())['accounts'], {})

    def test_enable_blocked_by_root_record_reports_not_ok(self):
        st = Store({'anthropic': [row('a', 'X')]}, root={'a'})
        out = api.set_account_disabled('anthropic', 'X', False, st.tools())
        self.assertEqual(out['ok'], False)
        self.assertEqual(out['disabled'], True)

    def test_unknown_account_unsupported_provider_and_old_core(self):
        st = Store({'anthropic': [row('a', 'X')]})
        with self.assertRaises(HTTPException) as cm:
            api.set_account_disabled('anthropic', 'nope', True, st.tools())
        self.assertEqual(cm.exception.status_code, 404)
        with self.assertRaises(HTTPException) as cm:
            api.set_account_disabled('gemini', 'X', True, st.tools())
        self.assertEqual(cm.exception.status_code, 400)
        orig = api._disable_tools
        api._disable_tools = lambda **_: None
        try:
            self.assertEqual(api.disabled_accounts(), {'supported': False, 'accounts': {}})
            with self.assertRaises(HTTPException) as cm:
                api.set_account_disabled('anthropic', 'X', True)
            self.assertEqual(cm.exception.status_code, 501)
        finally:
            api._disable_tools = orig

    def test_unreadable_pool_is_skipped_not_fatal(self):
        st = Store({'anthropic': [row('a', 'X')]})
        st.off.add('a')
        load_pool, ident, is_off, set_off = st.tools()

        def flaky(provider):
            if provider == 'openai-codex':
                raise OSError('EIO')
            return load_pool(provider)
        self.assertEqual(api.disabled_accounts((flaky, ident, is_off, set_off))['accounts'], {'anthropic': ['X']})
        with self.assertRaises(HTTPException) as cm:
            api.set_account_disabled('openai-codex', 'X', True, (flaky, ident, is_off, set_off))
        self.assertEqual(cm.exception.status_code, 503)


class RealCorePollTests(unittest.TestCase):
    """GET /disabled against the real core module: read-only, and free while nothing is disabled."""

    def setUp(self):
        try:
            import agent.credential_pool_disabled  # noqa: F401
        except ImportError:
            self.skipTest('core without credential_pool_disabled')
        import json
        import os
        import tempfile
        from unittest import mock
        self.home = tempfile.mkdtemp(prefix='au-disabled-')
        env = mock.patch.dict(os.environ, {'HERMES_HOME': self.home})
        env.start()
        self.addCleanup(env.stop)
        rows = [{'id': 'row0', 'label': 'a@example.com', 'auth_type': 'api_key', 'priority': 0, 'source': 'manual',
                 'access_token': 'fixture-a'}]
        with open(os.path.join(self.home, 'auth.json'), 'w') as fh:
            json.dump({'version': 1, 'providers': {}, 'credential_pool': {'anthropic': rows}}, fh)
        import agent.credential_pool as cp
        import agent.credential_pool_disabled as cpd
        root = mock.patch.object(cpd, '_root_path', lambda: None)
        root.start()
        self.addCleanup(root.stop)
        cpd._cache.clear()
        self.cp, self.cpd = cp, cpd
        no_load = mock.patch.object(cp, 'load_pool', side_effect=AssertionError('panel poll must not load_pool'))
        no_load.start()
        self.addCleanup(no_load.stop)

    def test_poll_reads_persisted_rows_only_and_nothing_when_nothing_is_disabled(self):
        from unittest import mock
        import hermes_cli.auth as auth
        with mock.patch.object(auth, 'read_credential_pool', wraps=auth.read_credential_pool) as reads:
            self.assertEqual(api.disabled_accounts(), {'supported': True, 'accounts': {}})
            self.assertEqual(reads.call_count, 0)
        entry = self.cp.PooledCredential.from_dict('anthropic', auth.read_credential_pool('anthropic')[0])
        self.assertTrue(self.cpd.set_credential_disabled('anthropic', entry, True))
        self.cpd._cache.clear()
        out = api.disabled_accounts()
        self.assertEqual(list(out['accounts']), ['anthropic'])
        self.assertEqual(len(out['accounts']['anthropic']), 1)


if __name__ == '__main__':
    unittest.main()
