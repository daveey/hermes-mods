"""Safety gates for the approved Account Usage selection seams.

Uses REAL installed core methods with only disk/network boundaries replaced.
Never loads the user's credential store and never performs provider requests.
Run Contract for RED acceptance evidence; Characterization for observed behavior.
No production selection feature is installed while these contract gates fail.
"""
from contextlib import ExitStack
from types import SimpleNamespace
import sys
import time
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, '/Users/daveey/.hermes/hermes-agent')
import hermes_bootstrap  # noqa: F401
from agent.credential_pool import CredentialPool, PooledCredential
from agent.client_lifecycle import ClientLifecycleMixin


def probe_reclaim(target='ready'):
    """Reclaim a READY account while another account has a FUTURE cooldown."""
    now = time.time()
    entries = [
        PooledCredential(provider='openai-codex', id='ready', label='duplicate label',
                         auth_type='oauth', priority=0, source='fixture',
                         access_token='fixture-ready-not-a-secret', last_status='ok'),
        PooledCredential(provider='openai-codex', id='cooling', label='duplicate label',
                         auth_type='oauth', priority=1, source='fixture',
                         access_token='fixture-cooling-not-a-secret', last_status='exhausted',
                         last_status_at=now, last_error_reset_at=now + 86400),
    ]
    with patch('agent.credential_pool.get_pool_strategy', return_value='priority'):
        pool = CredentialPool('openai-codex', entries)
    with ExitStack() as stack:
        # Isolate external I/O only. reclaim/_available_entries/_adopt are real.
        stack.enter_context(patch.object(pool, '_resync_stale_entry', side_effect=lambda e: e))
        stack.enter_context(patch.object(pool, '_entry_needs_refresh', return_value=False))
        probe = stack.enter_context(patch.object(pool, '_codex_quota_restored_upstream', return_value=True))
        persist = stack.enter_context(patch.object(pool, '_persist'))
        chosen = pool.reclaim(target, model='gpt-fixture')
        return {
            'chosen': chosen.id if chosen else None,
            'cooldown_status': next(e.last_status for e in pool.entries() if e.id == 'cooling'),
            'probe_calls': probe.call_count,
            'persist_calls': persist.call_count,
        }


def probe_anthropic_dispatch():
    """Swap to B, then build the actual request client via the real core path."""
    agent = SimpleNamespace(
        provider='anthropic', model='claude-fixture', api_mode='anthropic_messages',
        api_key='fixture-A', base_url='https://api.anthropic.com',
        _anthropic_api_key='fixture-A', _anthropic_base_url='https://api.anthropic.com',
        _anthropic_client=SimpleNamespace(close=lambda: None),
        _build_direct_anthropic_client=lambda key, base: SimpleNamespace(close=lambda: None),
        _anthropic_oauth_flag=lambda token: False,
        _checkout_request_slot=lambda slot, key: (None, None),
        _store_request_slot=lambda slot, client, key: None,
        _anthropic_log_context=lambda: 'fixture',
    )
    agent._try_refresh_anthropic_client_credentials = lambda: ClientLifecycleMixin._try_refresh_anthropic_client_credentials(agent)
    # Capture which key the actual request-client constructor receives, without HTTP.
    agent._request_anthropic_client_key = lambda: ('anthropic', agent._anthropic_api_key)
    agent._build_anthropic_client_for_key = lambda key: SimpleNamespace(selected_key=key[1])
    entry = SimpleNamespace(id='B', runtime_api_key='fixture-B', access_token='fixture-B',
                            runtime_base_url='https://api.anthropic.com', base_url=None)
    with patch('hermes_cli.anon_auth.route_can_serve_model', return_value=True), \
         patch('agent.anthropic_credentials.resolve_anthropic_token', return_value='fixture-A'), \
         patch('agent.auxiliary_key_rotation.rotate_runtime_main_api_key'):
        swapped = ClientLifecycleMixin._swap_credential(agent, entry)
        after_swap = agent._anthropic_api_key
        client = ClientLifecycleMixin._create_request_anthropic_client(agent, reason='selection-test')
    return {'swapped': swapped, 'after_swap': after_swap,
            'request_key': client.selected_key, 'recorded_entry_id': agent._credential_pool_entry_id}


class Characterization(unittest.TestCase):
    def test_ready_reclaim_can_clear_another_future_cooldown(self):
        self.assertEqual(probe_reclaim(), {'chosen': 'ready', 'cooldown_status': 'ok',
                                         'probe_calls': 1, 'persist_calls': 1})

    def test_cooling_reclaim_can_clear_selected_future_cooldown(self):
        self.assertEqual(probe_reclaim('cooling')['chosen'], 'cooling')

    def test_request_client_reverts_successful_anthropic_swap(self):
        self.assertEqual(probe_anthropic_dispatch(), {
            'swapped': True, 'after_swap': 'fixture-B', 'request_key': 'fixture-A',
            'recorded_entry_id': 'B'})


class Contract(unittest.TestCase):
    def test_click_must_not_clear_other_accounts_cooldowns(self):
        result = probe_reclaim()
        self.assertEqual(result['cooldown_status'], 'exhausted',
                         'Approved reclaim seam clears an unrelated future cooldown')
        self.assertEqual(result['persist_calls'], 0)

    def test_cooling_account_must_be_unavailable_not_reset(self):
        self.assertIsNone(probe_reclaim('cooling')['chosen'],
                          'Approved reclaim seam can return a cooling account after clearing it')

    def test_selected_anthropic_identity_must_reach_request_client(self):
        result = probe_anthropic_dispatch()
        self.assertTrue(result['swapped'])
        self.assertEqual(result['request_key'], 'fixture-B',
                         'Request-client refresh overwrites the selection before dispatch')


if __name__ == '__main__':
    unittest.main(verbosity=2)
