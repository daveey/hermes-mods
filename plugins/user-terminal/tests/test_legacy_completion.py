"""Real-core boundary: adopted legacy history is routable; a live inline owner is not.

No viewer prompt is submitted, no legacy injector is called, and no model is run.
The native actor below models core's authenticated adoption prerequisite, not an
identity a plugin is allowed to manufacture in production.
"""
import asyncio
from contextlib import asynccontextmanager
import json
from types import SimpleNamespace

import pytest
from test_closed_asks import ut


@pytest.fixture
def legacy_home(tmp_path, monkeypatch):
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    monkeypatch.setenv('HERMES_USER_TERMINAL_DIR', str(tmp_path / 'jobs'))
    monkeypatch.setenv('HERMES_DISABLE_LAZY_INSTALLS', '1')
    monkeypatch.setattr(ut, 'confirm_mode', lambda: 'off')
    monkeypatch.setattr(ut, 'max_wait_minutes', lambda: 120)
    monkeypatch.setattr(ut, 'reattach_grace_minutes', lambda: 10)
    return tmp_path


async def make_owner(home, monkeypatch, surface='tui'):
    from gateway import run
    from gateway.config import GatewayConfig
    from gateway.session import SessionStore
    from gateway.session_authority import initialize_session_authority
    from hermes_cli import plugins

    monkeypatch.setattr(run, '_load_gateway_config', lambda: {'platform_toolsets': {'tui': [], 'cli': []}})
    store = SessionStore(home / 'sessions', GatewayConfig())
    assert str(store._db.db_path).startswith(str(home))
    runner = SimpleNamespace(session_store=store, _session_db=store._db, adapters={}, _draining=False,
        _running=True, _gateway_loop=asyncio.get_running_loop(), _cached_agent_for=lambda route: None)
    runner._adapter_for_source = lambda source: runner.adapters.get(source.platform)
    authority = await initialize_session_authority(runner, profile_id=str(home), instance_id='fixture')
    monkeypatch.setattr(authority, '_schedule_admitted', lambda *args: None)
    @asynccontextmanager
    async def scope(source, home=None):
        assert home.resolve() == legacy_home_path.resolve()
        yield
    legacy_home_path = home
    runner._plugin_injection_scope = scope
    monkeypatch.setattr(plugins, '_published_gateway_message_injector', (runner, None))
    sid = '20260101_120000_abcdef'
    authority.db.create_session(sid, source=surface, model='saved-model', cwd=str(home))
    authority.db.append_message(sid, role='user', content='original request')
    authority.db.append_message(sid, role='assistant', content='original response')
    return store, authority, sid


def native_actor(authority):
    from gateway.session_contract import Principal
    from gateway.session_local_migration import bind_native_transport
    actor = Principal('uid:1000', authority.profile_id,
                      frozenset({'session:read', 'session:create'}), 'fixture-native')
    bind_native_transport(authority, actor, {'provider': 'local', 'profile_id': authority.profile_id,
                                            'instance_id': authority.instance_id})
    return actor


@pytest.mark.parametrize('surface', ['tui', 'cli'])
def test_previously_adopted_legacy_completion_is_durable_and_cold_restorable(legacy_home, monkeypatch, surface):
    async def scenario():
        from gateway.session_local_migration import resolve_local_target, unbind_native_transport
        from hermes_state_runtime import list_session_admissions
        store, authority, sid = await make_owner(legacy_home, monkeypatch, surface)
        try:
            actor = native_actor(authority)
            ref = resolve_local_target(authority, actor, sid)
            assert ref.session_id == sid  # adoption retains historical FIFO identity
            unbind_native_transport(authority, actor)  # delivery needs no connected viewer
            authority.sessions.clear()
            job = ut.create_job('true', 'legacy fixture', str(legacy_home),
                                owner={'kind': 'session', 'session_id': sid})
            (job / 'output.log').write_text('legacy result')
            ut._take_final(job, {'status': 'ran', 'exit_code': 0})
            await asyncio.to_thread(ut.sweep_jobs)
            receipt = json.loads((job / 'completion-receipt.json').read_text())
            assert receipt['status'] == 'admitted'
            rows = list_session_admissions(authority.db, session_id=sid, pending_only=False)
            assert len(rows) == 1
            assert rows[0]['principal_id'].startswith('automation:')
            assert rows[0]['payload']['local_automation_v1']
            assert rows[0]['payload']['text'].endswith('legacy result')
            assert rows[0]['request_id'] == json.dumps(['user-terminal', job.name, 'final'], separators=(',', ':'))
            (job / 'completion-receipt.json').unlink()
            authority.sessions.clear()
            await asyncio.to_thread(ut.sweep_jobs)
            assert json.loads((job / 'completion-receipt.json').read_text()) == receipt
            assert len(list_session_admissions(authority.db, session_id=sid, pending_only=False)) == 1
        finally:
            store.close_all_db_handles()
    asyncio.run(scenario())


def test_unadopted_legacy_stays_pending_then_retries_after_authenticated_adoption(legacy_home, monkeypatch):
    async def scenario():
        from gateway.session_local_migration import resolve_local_target
        from hermes_state_runtime import list_session_admissions
        store, authority, sid = await make_owner(legacy_home, monkeypatch)
        try:
            original = authority.db.get_session(sid)
            job = ut.create_job('true', 'legacy fixture', str(legacy_home),
                                owner={'kind': 'session', 'session_id': sid})
            ut._take_final(job, {'status': 'ran', 'exit_code': 0})
            await asyncio.to_thread(ut.sweep_jobs)
            assert not (job / 'completion-receipt.json').exists()
            pending = json.loads((job / 'completion-pending.json').read_text())
            assert pending['reason'] == 'no_gateway_route'
            assert authority.db.get_session(sid) == original
            assert list_session_admissions(authority.db, session_id=sid, pending_only=False) == []
            # Authenticated owner resume is an external core action, not a completion fallback.
            resolve_local_target(authority, native_actor(authority), sid)
            pending['next_at'] = 0
            (job / 'completion-pending.json').write_text(json.dumps(pending))
            await asyncio.to_thread(ut.sweep_jobs)
            assert json.loads((job / 'completion-receipt.json').read_text())['status'] == 'admitted'
            assert not (job / 'completion-pending.json').exists()
            assert len(list_session_admissions(authority.db, session_id=sid, pending_only=False)) == 1
        finally:
            store.close_all_db_handles()
    asyncio.run(scenario())


def test_existing_adoption_requires_authenticated_transport_and_refuses_live_inline_owner(legacy_home, monkeypatch):
    async def scenario():
        from gateway.session_contract import Principal
        from gateway.session_local_migration import resolve_local_target
        from hermes_cli.active_sessions import try_acquire_active_session, release_active_session
        from hermes_state_runtime import RuntimeStoreError
        store, authority, sid = await make_owner(legacy_home, monkeypatch)
        lease = None
        try:
            original = authority.db.get_session(sid)
            producer = Principal('automation:terminal', authority.profile_id,
                                 frozenset({'session:read', 'session:create'}), 'producer')
            with pytest.raises(RuntimeStoreError, match='not_found'):
                resolve_local_target(authority, producer, sid)
            actor = native_actor(authority)
            lease, refusal = try_acquire_active_session(session_id=sid, surface='tui',
                                                       registry_home=legacy_home, config={})
            assert lease is not None, refusal
            with pytest.raises(RuntimeStoreError, match='runtime_coordination_required'):
                resolve_local_target(authority, actor, sid)
            assert authority.db.get_session(sid) == original
            release_active_session(lease)
            lease = None
            assert resolve_local_target(authority, actor, sid).session_id == sid
        finally:
            if lease is not None:
                release_active_session(lease)
            store.close_all_db_handles()
    asyncio.run(scenario())
