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
        api._disable_tools = lambda: None
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


if __name__ == '__main__':
    unittest.main()
