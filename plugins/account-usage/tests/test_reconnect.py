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

    def test_dead_manual_row_names_itself_as_the_sign_in_target(self):
        row = entry('pool-7', 'codex:ws:sub', last_status=DEAD)
        row.source = 'manual:device_code'
        pool = Pool([row, entry('other', 'Y')], lambda cid: None)
        out = api.reconnect_account('openai-codex', 'codex:ws:sub', tools(pool))
        self.assertEqual(out['status'], 'needs_sign_in')
        self.assertEqual(out['sign_in'], {'mode': 'target', 'credential_id': 'pool-7'})
        self.assertNotIn('rt', repr(out))

    def test_singleton_seeded_or_removed_row_routes_to_the_ordinary_login(self):
        seeded = entry('a', 'X', last_status=DEAD)
        seeded.source = 'device_code'
        out = api.reconnect_account('openai-codex', 'X', tools(Pool([seeded], lambda cid: None)))
        self.assertEqual(out['sign_in'], {'mode': 'singleton'})
        gone = entry('a', 'X')
        gone.source = 'manual:device_code'  # core dropped it: no row is left to replace
        pool = Pool([gone], None)
        pool._refresh = lambda cid: setattr(pool, '_entries', []) or None
        out = api.reconnect_account('openai-codex', 'X', tools(pool))
        self.assertEqual((out['removed'], out['sign_in']), (1, {'mode': 'singleton'}))

    def test_rows_this_panel_cannot_bind_are_unsupported_not_guessed(self):
        for sources in (['env:OPENAI_TOKEN'], ['borrowed:hub'], [''], ['manual:device_code', 'manual:device_code'],
                        ['manual:device_code', 'device_code']):
            with self.subTest(sources=sources):
                rows = []
                for i, source in enumerate(sources):
                    row = entry(f'r{i}', 'X', last_status=DEAD)
                    row.source = source
                    rows.append(row)
                out = api.reconnect_account('openai-codex', 'X', tools(Pool(rows, lambda cid: None)))
                self.assertEqual(out['sign_in'], {'mode': 'unsupported'})

    def test_sign_in_route_only_accompanies_a_dead_grant(self):
        for refresh in (lambda cid: SimpleNamespace(last_status='ok'),
                        lambda cid: SimpleNamespace(last_status='exhausted', last_error_code=429, last_error_reason='quota')):
            for provider, source in (('openai-codex', 'manual:device_code'), ('anthropic', 'manual:pkce')):
                live = entry('a', 'X')
                live.source = source
                self.assertNotIn('sign_in', api.reconnect_account(provider, 'X', tools(Pool([live], refresh))))

    def test_dead_anthropic_manual_row_names_itself_as_the_sign_in_target(self):
        row = entry('pool-9', 'fp:old', last_status=DEAD)
        row.source = 'manual:pkce'
        out = api.reconnect_account('anthropic', 'fp:old', tools(Pool([row, entry('other', 'Y')], lambda cid: None)))
        self.assertEqual(out['status'], 'needs_sign_in')
        self.assertEqual(out['auth_flow'], 'external')
        self.assertEqual(out['sign_in'], {'mode': 'target', 'credential_id': 'pool-9'})
        self.assertNotIn('rt', repr(out))

    def test_anthropic_never_routes_to_an_ordinary_login(self):
        # Anthropic has no in-app login to rewrite a singleton: every row that is not one owned
        # manual row is unsupported, including the shapes Codex answers with ``singleton``.
        for sources in (['device_code'], ['claude_code'], ['env:ANTHROPIC_TOKEN'], ['borrowed:hub'], [''],
                        ['manual:pkce', 'manual:pkce']):
            with self.subTest(sources=sources):
                rows = []
                for i, source in enumerate(sources):
                    row = entry(f'r{i}', 'X', last_status=DEAD)
                    row.source = source
                    rows.append(row)
                out = api.reconnect_account('anthropic', 'X', tools(Pool(rows, lambda cid: None)))
                self.assertEqual(out['sign_in'], {'mode': 'unsupported'})
        gone = entry('a', 'X')
        gone.source = 'manual:pkce'
        pool = Pool([gone], None)
        pool._refresh = lambda cid: setattr(pool, '_entries', []) or None
        self.assertEqual(api.reconnect_account('anthropic', 'X', tools(pool))['sign_in'], {'mode': 'unsupported'})

    def test_credential_health_follows_the_row_id_to_its_current_account(self):
        # After a sign-in replaced the row in place, the same row id carries a new account id.
        row = entry('pool-9', 'fp:new', last_status='ok')
        pool = Pool([row, entry('other', 'fp:new2', last_status=DEAD)], lambda cid: self.fail('health never refreshes'))
        out = api.credential_health('anthropic', 'pool-9', tools(pool))
        self.assertEqual(out, {'found': True, 'provider': 'anthropic', 'credential_id': 'pool-9',
                               'account_id': 'fp:new', 'status': 'ok'})
        self.assertEqual(pool.refreshed, [])
        self.assertNotIn('rt', repr(out))

    def test_credential_health_reports_dead_quota_disabled_and_missing_rows(self):
        dead = entry('d', 'D', last_status=DEAD)
        quota = entry('q', 'Q', last_status='exhausted', last_error_code=429, last_error_reason='usage_limit')
        off = entry('o', 'O', last_status=DEAD)
        pool = Pool([dead, quota, off], lambda cid: None)
        t = tools(pool, is_disabled=lambda provider, e: e.id == 'o')
        self.assertEqual(api.credential_health('anthropic', 'd', t)['status'], 'needs_sign_in')
        self.assertEqual(api.credential_health('anthropic', 'q', t)['status'], 'quota')
        self.assertEqual(api.credential_health('anthropic', 'o', t)['status'], 'disabled')
        gone = api.credential_health('anthropic', 'nope', t)
        self.assertEqual((gone['found'], gone['account_id'], gone['status']), (False, None, 'unknown'))

    def test_credential_health_refuses_other_providers_and_hides_pool_errors(self):
        with self.assertRaises(HTTPException) as bad:
            api.credential_health('gemini', 'a', tools(Pool([], lambda cid: None)))
        self.assertEqual(bad.exception.status_code, 400)

        def broken(provider):
            raise RuntimeError('secret-token-in-traceback')
        with self.assertRaises(HTTPException) as down:
            api.credential_health('anthropic', 'a', (broken, lambda provider, e: e.ident, DEAD))
        self.assertEqual(down.exception.status_code, 503)
        self.assertNotIn('secret', str(down.exception.detail))

    def test_credential_health_request_rejects_ids_that_are_not_plain_row_ids(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        app = FastAPI()
        app.include_router(api.router)
        client = TestClient(app)
        for body in ({'provider': 'anthropic', 'credential_id': '-rf'}, {'provider': 'anthropic', 'credential_id': 'a b'},
                     {'provider': 'anthropic', 'credential_id': 'a;id'}, {'provider': 'anthropic', 'credential_id': ''},
                     {'provider': 'anthropic', 'credential_id': 'a', 'x': 1}, {'provider': 'anthropic'}):
            with self.subTest(body=body):
                self.assertEqual(client.post('/credential-health', json=body).status_code, 422)

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
