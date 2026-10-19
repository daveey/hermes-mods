"""POST /reconnect: force-refresh one account's pooled OAuth credentials (stubbed pool, no network)."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest

from fastapi import HTTPException

_spec = importlib.util.spec_from_file_location(
    'account_usage_plugin_api', Path(__file__).resolve().parents[1] / 'dashboard' / 'plugin_api.py')
api = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(api)

DEAD = 'dead'


class Pool:
    def __init__(self, entries, refresh):
        self._entries, self._refresh, self.refreshed = entries, refresh, []

    def entries(self):
        return list(self._entries)

    def try_refresh_matching(self, credential_id=None):
        self.refreshed.append(credential_id)
        return self._refresh(credential_id)


def entry(id, ident, label='acct', auth_type='oauth', refresh_token='rt'):
    return SimpleNamespace(id=id, ident=ident, label=label, auth_type=auth_type, refresh_token=refresh_token)


def tools(pool):
    return (lambda provider: pool), (lambda provider, e: e.ident), DEAD


class ReconnectTests(unittest.TestCase):
    def test_refreshes_every_credential_of_the_account_only(self):
        pool = Pool([entry('a', 'X'), entry('b', 'Y'), entry('c', 'X')],
                     lambda cid: SimpleNamespace(last_status='ok'))
        out = api.reconnect_account('openai-codex', 'X', tools(pool))
        self.assertEqual(pool.refreshed, ['a', 'c'])
        self.assertEqual(out['ok'], True)
        self.assertEqual(out['renewed'], 2)
        self.assertEqual(out['needs_sign_in'], False)

    def test_dead_grant_asks_for_sign_in_with_quoted_label(self):
        pool = Pool([entry('a', 'X', label="bob's acct")], lambda cid: None)
        out = api.reconnect_account('anthropic', 'X', tools(pool))
        self.assertEqual(out['ok'], False)
        self.assertEqual(out['needs_sign_in'], True)
        self.assertEqual(out['sign_in_command'], "hermes auth add anthropic --type oauth --label 'bob'\"'\"'s acct'")

    def test_refresh_that_leaves_entry_dead_is_not_success(self):
        pool = Pool([entry('a', 'X')], lambda cid: SimpleNamespace(last_status=DEAD))
        self.assertEqual(api.reconnect_account('anthropic', 'X', tools(pool))['needs_sign_in'], True)

    def test_refresh_exception_is_not_echoed(self):
        def boom(cid):
            raise RuntimeError('secret-token-xyz')
        pool = Pool([entry('a', 'X')], boom)
        out = api.reconnect_account('anthropic', 'X', tools(pool))
        self.assertEqual(out['ok'], False)
        self.assertNotIn('secret-token-xyz', repr(out))

    def test_non_oauth_entries_are_skipped(self):
        pool = Pool([entry('a', 'X', auth_type='api_key'), entry('b', 'X', refresh_token=None)],
                    lambda cid: SimpleNamespace(last_status='ok'))
        out = api.reconnect_account('anthropic', 'X', tools(pool))
        self.assertEqual(pool.refreshed, [])
        self.assertEqual(out['needs_sign_in'], True)

    def test_unknown_account_is_404_and_other_providers_400(self):
        pool = Pool([entry('a', 'X')], lambda cid: None)
        with self.assertRaises(HTTPException) as cm:
            api.reconnect_account('anthropic', 'nope', tools(pool))
        self.assertEqual(cm.exception.status_code, 404)
        with self.assertRaises(HTTPException) as cm:
            api.reconnect_account('openrouter', 'X', tools(pool))
        self.assertEqual(cm.exception.status_code, 400)

    def test_unreadable_pool_is_503_without_detail(self):
        def bad(provider):
            raise OSError('/Users/x/.hermes/auth.json corrupt')
        with self.assertRaises(HTTPException) as cm:
            api.reconnect_account('anthropic', 'X', (bad, None, DEAD))
        self.assertEqual(cm.exception.status_code, 503)
        self.assertNotIn('auth.json', cm.exception.detail)

    def test_route_rejects_extra_fields(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        app = FastAPI()
        app.include_router(api.router)
        r = TestClient(app).post('/reconnect', json={'provider': 'anthropic', 'account_id': 'X', 'x': 1})
        self.assertEqual(r.status_code, 422)


if __name__ == '__main__':
    unittest.main()
