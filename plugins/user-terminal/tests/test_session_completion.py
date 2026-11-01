"""Real gateway FIFO, isolated stores; only model scheduling is suppressed."""
import asyncio
from contextlib import asynccontextmanager
import json
from types import SimpleNamespace

import pytest
from test_closed_asks import ut


@pytest.mark.parametrize('surface', ['cli', 'gui'])
@pytest.mark.parametrize('compressed', [False, True])
@pytest.mark.parametrize('final', ['ran', 'declined', 'failed', 'withdrawn', 'abandoned'])
def test_session_completion_commits_once_after_tool_turn(tmp_path, monkeypatch, surface, final, compressed):
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    monkeypatch.setenv('HERMES_USER_TERMINAL_DIR', str(tmp_path / 'jobs'))
    monkeypatch.setenv('HERMES_DISABLE_LAZY_INSTALLS', '1')
    monkeypatch.setattr(ut, 'confirm_mode', lambda: 'off')
    monkeypatch.setattr(ut, 'max_wait_minutes', lambda: 120)
    monkeypatch.setattr(ut, 'reattach_grace_minutes', lambda: 10)
    asyncio.run(scenario(tmp_path, monkeypatch, surface, final, compressed))


async def scenario(tmp_path, monkeypatch, surface, final, compressed):
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
    assert str(store._db.db_path).startswith(str(tmp_path))
    runner = SimpleNamespace(session_store=store, _session_db=store._db, adapters={}, _draining=False,
        _running=True, _gateway_loop=asyncio.get_running_loop(), _cached_agent_for=lambda route: None)
    runner._adapter_for_source = lambda source: runner.adapters.get(source.platform)
    authority = await initialize_session_authority(runner, profile_id=str(get_hermes_home()), instance_id='owner')
    monkeypatch.setattr(authority, '_schedule_admitted', lambda *args: None)
    @asynccontextmanager
    async def scope(source, home=None):
        assert home.resolve() == tmp_path.resolve()
        yield
    runner._plugin_injection_scope = scope
    monkeypatch.setattr(plugins, '_published_gateway_message_injector', (runner, None))
    viewer = AuthorityConnection(authority, object(), {'user_id': 'human', 'profile_id': authority.profile_id})
    ref = create_local_session(authority, viewer.actor, {'request_id': 'create', 'source': surface,
        'cwd': str(tmp_path), 'model': 'm', 'toolsets': []})
    owner_sid = ref.session_id
    if compressed:
        from tests.gateway.test_local_resume_compression_child import _compress
        live = authority.sessions[ref.session_id]
        store._record_gateway_session_peer(ref.session_id, live.route, live.source)
        _compress(authority.db, ref.session_id, 'compressed-child', authority.db.get_session(ref.session_id)['source'])
        store._record_gateway_session_peer('compressed-child', live.route, live.source)
        # Simulate the production turn's route advancing with its physical transcript.
        from gateway.session_detached import route_entry
        route_entry(authority, live.route).session_id = 'compressed-child'
        owner_sid = 'compressed-child'
    job = ut.create_job('true', 'fixture', str(tmp_path), owner={'kind': 'session', 'session_id': owner_sid})
    (job / 'output.log').write_text('x' * 50000 + 'RESULT-END')
    ut._take_final(job, {'status': final, 'exit_code': 0 if final == 'ran' else None})
    try:
        # Cold session after the caller has gone; preserve durable owner policy.
        authority.sessions.pop(ref.session_id)
        for _ in range(2):
            await asyncio.to_thread(ut.sweep_jobs)
        receipt = json.loads((job / 'completion-receipt.json').read_text())
        assert receipt['status'] == 'admitted'
        rows = list_session_admissions(authority.db, session_id=ref.session_id, pending_only=False)
        assert len(rows) == 1
        text = rows[0]['payload']['text']
        assert job.name in text and f'status: {final}' in text and 'exit_code:' in text
        assert text.endswith('RESULT-END') and len(text) < 6500
        assert rows[0]['principal_id'].startswith('automation:')
        # Lost receipt / restarted producer must read back the same admission.
        (job / 'completion-receipt.json').unlink()
        (job / 'output.log').write_text('late mutation must not change payload')
        await asyncio.to_thread(ut.sweep_jobs)
        assert len(list_session_admissions(authority.db, session_id=ref.session_id, pending_only=False)) == 1
        assert json.loads((job / 'completion-receipt.json').read_text()) == receipt
        missing = ut.create_job('true', 'missing fixture', str(tmp_path),
                                owner={'kind': 'session', 'session_id': 'missing-session'})
        ut._take_final(missing, {'status': 'ran', 'exit_code': 0})
        await asyncio.to_thread(ut.sweep_jobs)
        assert json.loads((missing / 'completion-receipt.json').read_text())['status'] == 'missing'
    finally:
        store.close_all_db_handles()
