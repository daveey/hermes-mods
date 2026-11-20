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


def entry(id, ident, label='acct', auth_type='oauth', refresh_token='rt', last_status=None,
          last_error_code=None, last_error_reason=None, extra=None):
    return SimpleNamespace(
        id=id, ident=ident, label=label, auth_type=auth_type, refresh_token=refresh_token,
        last_status=last_status, last_error_code=last_error_code, last_error_reason=last_error_reason,
        extra=extra or {})


def tools(pool, is_disabled=None):
    triple = (lambda provider: pool), (lambda provider, e: e.ident), DEAD
    return triple if is_disabled is None else triple + (is_disabled,)


class ReconnectTests(unittest.TestCase):
    def test_refreshes_every_credential_of_the_account_only(self):
        pool = Pool([entry('a', 'X'), entry('b', 'Y'), entry('c', 'X')],
                     lambda cid: SimpleNamespace(last_status='ok'))
        out = api.reconnect_account('openai-codex', 'X', tools(pool))
        self.assertEqual(pool.refreshed, ['a', 'c'])
        self.assertEqual(out['ok'], True)
        self.assertEqual(out['renewed'], 2)
        self.assertEqual(out['needs_sign_in'], False)
        self.assertEqual(out['status'], 'renewed')
        self.assertEqual(out['auth_flow'], 'device_code')
        self.assertEqual(out['account_id'], 'X')
        self.assertNotIn('sign_in_command', out)

    def test_dead_grant_asks_for_sign_in_without_cli_command(self):
        pool = Pool([entry('a', 'X', label="bob's acct", last_status=DEAD)], lambda cid: None)
        out = api.reconnect_account('anthropic', 'X', tools(pool))
        self.assertEqual(out['ok'], False)
        self.assertEqual(out['needs_sign_in'], True)
        self.assertEqual(out['status'], 'needs_sign_in')
        self.assertEqual(out['auth_flow'], 'external')
        self.assertEqual(out['label'], "bob's acct")
        self.assertNotIn('sign_in_command', out)

    def test_refresh_that_leaves_entry_dead_is_not_success(self):
        pool = Pool([entry('a', 'X')], lambda cid: SimpleNamespace(last_status=DEAD))
        self.assertEqual(api.reconnect_account('anthropic', 'X', tools(pool))['needs_sign_in'], True)

    def test_swallowed_refresh_none_on_live_entry_is_transient(self):
        pool = Pool([entry('a', 'X', last_status='ok')], lambda cid: None)
        out = api.reconnect_account('anthropic', 'X', tools(pool))
        self.assertEqual(out['status'], 'transient')
        self.assertEqual(out['needs_sign_in'], False)

    def test_row_quarantined_out_of_the_pool_by_a_dead_grant_is_sign_in_not_transient(self):
        # Core's Codex path: terminal refresh error -> _quarantine_sources drops the device_code row,
        # try_refresh_matching returns None. No DEAD row is left to read.
        for before in (None, 'ok', 'exhausted'):
            with self.subTest(before=before):
                pool = Pool([entry('a', 'X', last_status=before), entry('b', 'Y')], None)

                def drop(cid, pool=pool):
                    pool._entries = [e for e in pool._entries if e.id != cid]
                    return None
                pool._refresh = drop
                out = api.reconnect_account('openai-codex', 'X', tools(pool))
                self.assertEqual(out['status'], 'needs_sign_in')
                self.assertEqual(out['needs_sign_in'], True)
                self.assertEqual(out['removed'], 1)
                self.assertEqual(out['auth_flow'], 'device_code')
                self.assertEqual([e.id for e in pool.entries()], ['b'])

    def test_surviving_row_is_not_reported_removed(self):
        pool = Pool([entry('a', 'X', last_status=DEAD)], lambda cid: None)
        self.assertEqual(api.reconnect_account('openai-codex', 'X', tools(pool))['removed'], 0)
        pool = Pool([entry('a', 'X', last_status='ok')], lambda cid: None)
        out = api.reconnect_account('openai-codex', 'X', tools(pool))
        self.assertEqual((out['status'], out['removed']), ('transient', 0))

    def test_unreadable_pool_after_refresh_stays_transient(self):
        pool = Pool([entry('a', 'X', last_status='ok')], lambda cid: None)
        reads = []

        def entries():
            reads.append(1)
            if len(reads) > 1:
                raise OSError('pool busy')
            return list(pool._entries)
        pool.entries = entries
        out = api.reconnect_account('openai-codex', 'X', tools(pool))
        self.assertEqual((out['status'], out['removed']), ('transient', 0))

    def test_dead_grant_is_not_quota_even_if_entry_was_exhausted(self):
        pool = Pool(
            [entry('a', 'X', last_status='exhausted', last_error_code=429, last_error_reason='quota')],
            lambda cid: SimpleNamespace(last_status=DEAD, last_error_reason='invalid_grant'),
        )
        out = api.reconnect_account('openai-codex', 'X', tools(pool))
        self.assertEqual(out['status'], 'needs_sign_in')
        self.assertEqual(out['needs_sign_in'], True)

    def test_refresh_exception_is_transient_and_not_echoed(self):
        def boom(cid):
            raise RuntimeError('secret-token-xyz timeout talking to issuer')
        pool = Pool([entry('a', 'X')], boom)
        out = api.reconnect_account('anthropic', 'X', tools(pool))
        self.assertEqual(out['ok'], False)
        self.assertEqual(out['needs_sign_in'], False)
        self.assertEqual(out['status'], 'transient')
        self.assertNotIn('secret-token-xyz', repr(out))

    def test_invalid_grant_exception_is_sign_in_not_transient(self):
        def boom(cid):
            raise RuntimeError('invalid_grant: refresh token revoked')
        pool = Pool([entry('a', 'X')], boom)
        out = api.reconnect_account('anthropic', 'X', tools(pool))
        self.assertEqual(out['status'], 'needs_sign_in')
        self.assertEqual(out['needs_sign_in'], True)

    def test_exhausted_quota_is_not_sign_in(self):
        pool = Pool(
            [entry('a', 'X', last_status='exhausted', last_error_code=429, last_error_reason='quota')],
            lambda cid: SimpleNamespace(last_status='exhausted', last_error_code=429, last_error_reason='quota'),
        )
        out = api.reconnect_account('openai-codex', 'X', tools(pool))
        self.assertEqual(out['ok'], False)
        self.assertEqual(out['needs_sign_in'], False)
        self.assertEqual(out['status'], 'quota')

    def test_disabled_account_is_not_sign_in_and_is_not_refreshed(self):
        pool = Pool([entry('a', 'X')], lambda cid: SimpleNamespace(last_status='ok'))
        out = api.reconnect_account('anthropic', 'X', tools(pool, is_disabled=lambda p, e: True))
        self.assertEqual(pool.refreshed, [])
        self.assertEqual(out['ok'], False)
        self.assertEqual(out['needs_sign_in'], False)
        self.assertEqual(out['status'], 'disabled')

    def test_non_oauth_entries_are_skipped(self):
        pool = Pool([entry('a', 'X', auth_type='api_key'), entry('b', 'X', refresh_token=None)],
                    lambda cid: SimpleNamespace(last_status='ok'))
        out = api.reconnect_account('anthropic', 'X', tools(pool))
        self.assertEqual(pool.refreshed, [])
        self.assertEqual(out['needs_sign_in'], False)
        self.assertEqual(out['status'], 'unsupported')

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

    def test_ping_advertises_reconnect_status(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        app = FastAPI()
        app.include_router(api.router)
        r = TestClient(app).get('/ping')
        self.assertEqual(r.status_code, 200)
        self.assertGreaterEqual(r.json()['api'], 2)
        self.assertIn('reconnect_status', r.json()['features'])
        self.assertIn('auth_launch', r.json()['features'])
        self.assertIn('reconnect_removed', r.json()['features'])


if __name__ == '__main__':
    unittest.main()
