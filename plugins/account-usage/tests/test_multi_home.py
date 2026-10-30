"""Shared execution seams preserve home isolation and late-worker lifetimes."""
import gc
import sys
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).parent))
from test_install_cause import InstallCause
from test_request_selection import entry


class MultiHome(InstallCause):
    def pair(self):
        first, second = self.integration(), self.integration()
        second.home = first.home + '-other'
        self.addCleanup(self.s._install_failures.pop, second.home, None)
        first.install()
        second.install()
        return first, second

    def snapshot(self):
        return self.s.CredentialSnapshot('anthropic', 'claude-fixture', 'B', 'B',
                                         'https://api.anthropic.com', 'fixture-B')

    def test_adopting_home_snapshot_survives_both_unload_orders(self):
        from agent.client_lifecycle import ClientLifecycleMixin as M
        for owner_first in (True, False):
            with self.subTest(owner_first=owner_first):
                original = M._create_request_anthropic_client
                first, second = self.pair()
                wrapper = M._create_request_anthropic_client
                snap = self.snapshot()
                second.retain_snapshot(snap)
                for integration in ((first, second) if owner_first else (second, first)):
                    integration.unload()
                retained = M._create_request_anthropic_client is wrapper
                del snap
                gc.collect()
                self.assertTrue(retained, 'late adopter worker lost its selected factory')
                self.assertIs(M._create_request_anthropic_client, original)

    def test_reload_adopts_an_inactive_owner_with_a_live_snapshot(self):
        from agent.client_lifecycle import ClientLifecycleMixin as M
        original = M._create_request_anthropic_client
        first = self.integration()
        first.install()
        snap = self.snapshot()
        first.retain_snapshot(snap)
        first.unload()
        second = self.integration()
        second.install()
        self.assertTrue(second.active)
        second.unload()
        del snap
        gc.collect()
        self.assertIs(M._create_request_anthropic_client, original)

    def test_owner_unload_is_serialized_with_adoption(self):
        from agent.client_lifecycle import ClientLifecycleMixin as M
        first = self.integration()
        first.install()
        wrapper = M._create_request_anthropic_client
        second = self.integration()
        second.home += '-other'
        entered, finished = threading.Event(), threading.Event()
        def unload():
            entered.set()
            first.unload()
            finished.set()
        real = self.s._live_owner
        threads, calls, interleaved = [], [], []
        def observe(value):
            owner = real(value)
            calls.append(1)
            if len(calls) == 7:
                thread = threading.Thread(target=unload)
                threads.append(thread)
                thread.start()
                self.assertTrue(entered.wait(10))
                interleaved.append(finished.wait(0.1))
            return owner
        try:
            with patch.object(self.s, '_live_owner', observe):
                second.install()
        finally:
            for thread in threads:
                thread.join(10)
                self.assertFalse(thread.is_alive())
        self.assertEqual(interleaved, [False], 'unload raced the adoption transaction')
        self.assertTrue(second.active)
        self.assertIs(M._create_request_anthropic_client, wrapper)

    def test_same_thread_finalizer_during_owner_checks_retries_cleanly(self):
        from agent.client_lifecycle import ClientLifecycleMixin as M
        for boundary in (3, 7):
            with self.subTest(boundary=boundary):
                original = M._create_request_anthropic_client
                first = self.integration()
                first.install()
                held = [self.snapshot()]
                first.retain_snapshot(held[0])
                first.unload()
                second = self.integration()
                real, calls = self.s._live_owner, []
                def release(value):
                    owner = real(value)
                    calls.append(1)
                    if len(calls) == boundary:
                        held.clear()  # run the last snapshot finalizer reentrantly
                        gc.collect()
                    return owner
                with patch.object(self.s, '_live_owner', release):
                    second.install()
                self.assertTrue(second.active)
                self.assertIsNot(M._create_request_anthropic_client, original)
                second.unload()
                self.assertIs(M._create_request_anthropic_client, original)

    def test_reinstall_same_instance_has_no_adoption_cycle(self):
        first = self.integration()
        first.install()
        first.install()
        snap = self.snapshot()
        first.retain_snapshot(snap)
        self.assertEqual(first.adopted, [])
        first.unload()
        del snap
        gc.collect()

    def test_two_homes_consume_only_their_store_and_send_their_selected_token(self):
        from agent.account_usage_cache import _identity_id_for
        from agent.client_lifecycle import ClientLifecycleMixin as M
        from hermes_constants import set_hermes_home_override, reset_hermes_home_override
        from test_selection_backend import Backend
        # Two complete authorities, stores and disjoint account pools. The same core
        # factory wrappers serve both; middleware must use the turn's home.
        other = Backend('test_runtime_authority_and_main_identity')
        other.setUp()
        self.addCleanup(other.doCleanups)
        token = set_hermes_home_override(str(self.home))
        self.addCleanup(reset_hermes_home_override, token)
        first = self.integration()
        first.install()
        second = self.integration()
        second.home = str(other.home.resolve())
        second.install()
        sent = []
        for fixture, own, foreign, label in ((self, first, second, 'first'), (other, second, first, 'second')):
            token = set_hermes_home_override(str(fixture.home))
            try:
                fixture.lease()
                row = entry(label, source='manual', access_token='fixture-' + label)
                fixture.credentials([row.to_dict()])
                service = self.s.SelectionService(fixture.home, server=fixture.server)
                service.put('runtime-A', provider='anthropic', account_id=_identity_id_for('anthropic', row), expected_revision=0)
                agent = fixture.agent
                agent._build_anthropic_client_for_key = Mock(side_effect=lambda key: sent.append(key) or SimpleNamespace(default_headers={'x-api-key': key[1]}))
                def next_call(request):
                    return M._create_request_anthropic_client(agent, reason='fixture')
                with patch.dict(sys.modules, {'tui_gateway.server': fixture.server}), \
                        patch.object(own, 'calling_agent', return_value=agent), \
                        patch.object(foreign, 'calling_agent', side_effect=AssertionError('foreign middleware reached')):
                    foreign.middleware(next_call=lambda req: own.middleware(next_call=next_call, request=req, turn_id=label), request={})
                self.assertEqual(service.get('runtime-A')['state'], 'consumed')
                self.assertIsNone(self.s.current_snapshot(agent))
            finally:
                reset_hermes_home_override(token)
        self.assertEqual([key[1] for key in sent], ['fixture-first', 'fixture-second'])
