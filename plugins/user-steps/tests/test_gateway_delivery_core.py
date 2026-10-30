"""Real gateway FIFO admission (model execution suppressed), alongside TUI tests."""
import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from core_runtime import require_isolated_core
require_isolated_core()
from test_decisions import load_module, ROOT


@pytest.mark.asyncio
@pytest.mark.parametrize('compressed', [False, True])
async def test_local_gateway_operation_commits_once_and_readback_validates_payload(tmp_path, monkeypatch, compressed):
    from gateway import run
    from gateway.config import GatewayConfig
    from gateway.session import SessionStore
    from gateway.session_authority import initialize_session_authority
    from gateway.session_controls import AuthorityConnection
    from gateway.session_local import create_local_session
    from hermes_cli import plugins
    from hermes_constants import get_hermes_home
    from hermes_state_runtime import list_session_admissions

    monkeypatch.setattr(run, '_load_gateway_config', lambda: {'platform_toolsets': {'cli': []}})
    store = SessionStore(tmp_path / 'sessions', GatewayConfig())
    runner = SimpleNamespace(session_store=store, _session_db=store._db, adapters={}, _draining=False,
        _running=True, _gateway_loop=asyncio.get_running_loop(), _cached_agent_for=lambda route: None)
    runner._adapter_for_source = lambda source: runner.adapters.get(source.platform)
    authority = await initialize_session_authority(runner, profile_id=str(get_hermes_home()), instance_id='owner')
    monkeypatch.setattr(authority, '_schedule_admitted', lambda *args: None)
    @asynccontextmanager
    async def scope(source):
        yield
    runner._plugin_injection_scope = scope
    monkeypatch.setattr(plugins, '_published_gateway_message_injector', (runner, None))
    viewer = AuthorityConnection(authority, object(), {'user_id': 'human', 'profile_id': authority.profile_id})
    ref = create_local_session(authority, viewer.actor, {'request_id': 'create', 'source': 'gui',
        'cwd': str(tmp_path), 'model': 'm', 'toolsets': []})
    if compressed:
        from tests.gateway.test_local_resume_compression_child import _compress
        live = authority.sessions[ref.session_id]
        store._record_gateway_session_peer(ref.session_id, live.route, live.source)
        _compress(authority.db, ref.session_id, 'compressed-child', 'gui')
        store._record_gateway_session_peer('compressed-child', live.route, live.source)
    gateway = load_module('gateway_delivery_test', ROOT / 'gateway_delivery.py')
    prompt = load_module('prompt_gateway_test', ROOT / 'prompt_delivery.py')
    from hermes_cli.install_identity import get_install_id
    from hermes_cli.profiles import current_profile_name
    op = {'operationId': 'test-operation', 'owner': {'storedSessionId': ref.session_id,
        'profile': current_profile_name(), 'gatewayId': f'install:{get_install_id()}'},
        'intent': {'effect': 'answer', 'answer': 'Open a pull request'}}
    receiver = prompt.PromptAdmission()
    try:
        assert ref.session_id.startswith('local-')
        receipt = await asyncio.to_thread(receiver.deliver, op)
        assert receipt['admission']['transport'] == 'gateway_fifo'
        assert await asyncio.to_thread(receiver.readback, op, receipt)
        again = await asyncio.to_thread(receiver.deliver, op)
        assert again == receipt
        rows = list_session_admissions(authority.db, session_id=ref.session_id, pending_only=False)
        assert len(rows) == 1
        assert rows[0]['payload']['text'] == receiver._params(op)['text']
        assert 'Open a pull request' in rows[0]['payload']['text']
        # Even direct duplicate admissions share the core UNIQUE request identity.
        assert await asyncio.to_thread(gateway.call, 'prompt.admit', receiver._params(op)) == receipt['admission']
        assert len(list_session_admissions(authority.db, session_id=ref.session_id)) == 1
        bad = dict(receiver._params(op), payload_digest='wrong')
        with pytest.raises(RuntimeError, match='mismatch'):
            await asyncio.to_thread(gateway.call, 'prompt.admission.get', bad)
        from gateway import session_detached
        with monkeypatch.context() as rotated:
            rotated.setattr(session_detached, 'route_entry', lambda *args: None)
            assert await asyncio.to_thread(receiver.readback, op, receipt)
            assert await asyncio.to_thread(receiver.deliver, op) == receipt
            fresh = dict(op, operationId='not-yet-admitted')
            with pytest.raises(RuntimeError, match='gateway_target_rotated'):
                await asyncio.to_thread(receiver.deliver, fresh)
        monkeypatch.setattr(plugins, '_published_gateway_message_injector', None)
        with pytest.raises(RuntimeError, match='unavailable'):
            await asyncio.to_thread(receiver.deliver, op)
    finally:
        store.close_all_db_handles()
