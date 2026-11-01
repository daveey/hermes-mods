"""Real-core trusted legacy ingress and authenticated adoption boundaries.

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


@pytest.mark.parametrize('surface', ['tui', 'cli'])
@pytest.mark.parametrize('published', [False, True])
@pytest.mark.parametrize('active', [False, True])
@pytest.mark.parametrize('entry', ['sweep', 'watch_once'])
def test_unadopted_legacy_queues_trusted_notice(legacy_home, monkeypatch, surface, published, active, entry):
    async def scenario():
        from hermes_cli import plugins
        from hermes_cli.active_sessions import try_acquire_active_session, release_active_session
        from hermes_state_producer_admission import pending_for_owner, read_producer_admission
        from hermes_state_runtime import list_session_admissions
        store, authority, sid = await make_owner(legacy_home, monkeypatch, surface)
        lease = None
        try:
            if not published:
                monkeypatch.setattr(plugins, '_published_gateway_message_injector', None)
            if active:
                lease, refusal = try_acquire_active_session(session_id=sid, surface=surface,
                                                           registry_home=legacy_home, config={})
                assert lease is not None, refusal
            original = authority.db.get_session(sid)
            job = legacy_job(legacy_home, sid)
            if entry == 'watch_once':
                await asyncio.to_thread(ut.watch_once, job, {})
            else:
                await asyncio.to_thread(ut.sweep_jobs)
            receipt = json.loads((job / 'completion-receipt.json').read_text())
            assert receipt['status'] == 'admitted'
            payload = json.loads((job / 'completion-payload.json').read_text())
            core = read_producer_admission(producer=payload['identity'], profile_home=legacy_home)
            assert core['outcome'] == 'admitted' and core['status'] == 'queued'
            rows = pending_for_owner(authority.db, sid)
            assert len(rows) == 1 and rows[0]['text'] == payload['text']
            assert rows[0]['producer'] == payload['identity']
            assert rows[0]['text'].endswith('legacy result')
            assert authority.db.get_session(sid) == original
            assert list_session_admissions(authority.db, session_id=sid, pending_only=False) == []
            (job / 'completion-receipt.json').unlink()
            (job / 'output.log').write_text('late mutation must not change the notice')
            await asyncio.to_thread(ut.sweep_jobs)
            assert json.loads((job / 'completion-receipt.json').read_text()) == receipt
            assert pending_for_owner(authority.db, sid) == rows
        finally:
            if lease is not None:
                release_active_session(lease)
            store.close_all_db_handles()
    asyncio.run(scenario())


def legacy_job(home, sid):
    job = ut.create_job('true', 'legacy fixture', str(home),
                        owner={'kind': 'session', 'session_id': sid})
    (job / 'output.log').write_text('legacy result')
    ut._take_final(job, {'status': 'ran', 'exit_code': 0})
    return job


def expire_backoff(job):
    path = job / 'completion-pending.json'
    state = json.loads(path.read_text())
    state['next_at'] = 0
    path.write_text(json.dumps(state))


@pytest.mark.parametrize('failure', ['lost_reply', 'retry'])
def test_legacy_retry_recovers_one_frozen_core_notice(legacy_home, monkeypatch, failure):
    async def scenario():
        from hermes_cli import plugins
        import hermes_state_producer_admission as ingress
        store, authority, sid = await make_owner(legacy_home, monkeypatch)
        monkeypatch.setattr(plugins, '_published_gateway_message_injector', None)
        real = ingress.admit_producer_completion
        calls = []
        def flaky(**kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                if failure == 'retry':
                    return {'outcome': 'retry', 'reason': 'storage_busy'}
                real(**kwargs)
                raise TimeoutError('reply lost after commit')
            return real(**kwargs)
        monkeypatch.setattr(ingress, 'admit_producer_completion', flaky)
        try:
            job = legacy_job(legacy_home, sid)
            await asyncio.to_thread(ut.sweep_jobs)
            assert not (job / 'completion-receipt.json').exists()
            pending = json.loads((job / 'completion-pending.json').read_text())
            assert pending['kind'] == 'transient'
            assert len(ingress.pending_for_owner(authority.db, sid)) == (failure == 'lost_reply')
            await asyncio.to_thread(ut.sweep_jobs)
            assert len(calls) == 1
            (job / 'output.log').write_text('changed after first attempt')
            expire_backoff(job)
            await asyncio.to_thread(ut.sweep_jobs)
            assert json.loads((job / 'completion-receipt.json').read_text())['status'] == 'admitted'
            rows = ingress.pending_for_owner(authority.db, sid)
            assert len(rows) == 1 and rows[0]['text'].endswith('legacy result')
            assert not (job / 'completion-pending.json').exists()
        finally:
            store.close_all_db_handles()
    asyncio.run(scenario())


def test_missing_legacy_without_gateway_is_terminal(legacy_home, monkeypatch):
    async def scenario():
        from hermes_cli import plugins
        from hermes_state_producer_admission import pending_owners
        store, authority, sid = await make_owner(legacy_home, monkeypatch)
        monkeypatch.setattr(plugins, '_published_gateway_message_injector', None)
        try:
            job = legacy_job(legacy_home, 'missing-session')
            await asyncio.to_thread(ut.sweep_jobs)
            receipt = json.loads((job / 'completion-receipt.json').read_text())
            assert receipt['status'] == 'missing' and receipt['reason'] == 'not_found'
            assert pending_owners(authority.db) == []
            await asyncio.to_thread(ut.sweep_jobs)
            assert json.loads((job / 'completion-receipt.json').read_text()) == receipt
        finally:
            store.close_all_db_handles()
    asyncio.run(scenario())



@pytest.mark.parametrize('fault', ['missing_api', 'readback_retry', 'payload_mismatch'])
def test_legacy_admission_fails_closed_until_recovered(legacy_home, monkeypatch, fault):
    async def scenario():
        import sys
        import hermes_state_producer_admission as ingress
        store, authority, sid = await make_owner(legacy_home, monkeypatch)
        try:
            job = legacy_job(legacy_home, sid)
            real_read = ingress.read_producer_admission
            calls = []
            def flaky_read(**kwargs):
                calls.append(kwargs)
                result = real_read(**kwargs)
                if result['outcome'] == 'admitted':
                    if fault == 'readback_retry':
                        return {'outcome': 'retry', 'reason': 'storage_busy'}
                    return {**result, 'payload_digest': 'wrong-digest'}
                return result
            with monkeypatch.context() as patcher:
                if fault == 'missing_api':
                    patcher.setitem(sys.modules, 'hermes_state_producer_admission', None)
                else:
                    patcher.setattr(ingress, 'read_producer_admission', flaky_read)
                await asyncio.to_thread(ut.sweep_jobs)
            assert not (job / 'completion-receipt.json').exists()
            state = json.loads((job / 'completion-pending.json').read_text())
            assert state['reason'] == {'missing_api': 'producer_ingress_unavailable',
                'readback_retry': 'producer_storage_busy',
                'payload_mismatch': 'admission_payload_mismatch'}[fault]
            expire_backoff(job)
            await asyncio.to_thread(ut.sweep_jobs)
            assert json.loads((job / 'completion-receipt.json').read_text())['status'] == 'admitted'
            assert len(ingress.pending_for_owner(authority.db, sid)) == 1
        finally:
            store.close_all_db_handles()
    asyncio.run(scenario())


def test_queued_legacy_reroutes_same_identity_after_adoption(legacy_home, monkeypatch):
    async def scenario():
        from gateway.session_local_migration import resolve_local_target
        from hermes_cli import plugins
        from hermes_state_producer_admission import read_producer_admission
        from hermes_state_runtime import list_session_admissions
        store, authority, sid = await make_owner(legacy_home, monkeypatch)
        try:
            job = legacy_job(legacy_home, sid)
            await asyncio.to_thread(ut.sweep_jobs)
            payload = json.loads((job / 'completion-payload.json').read_text())
            (job / 'completion-receipt.json').unlink()  # producer lost its acknowledgment
            resolve_local_target(authority, native_actor(authority), sid)
            with monkeypatch.context() as patcher:
                patcher.setattr(plugins, '_published_gateway_message_injector', None)
                await asyncio.to_thread(ut.sweep_jobs)
            assert not (job / 'completion-receipt.json').exists()
            assert list_session_admissions(authority.db, session_id=sid, pending_only=False) == []
            core = read_producer_admission(producer=payload['identity'], profile_home=legacy_home)
            assert core['outcome'] == 'routed'
            expire_backoff(job)
            await asyncio.to_thread(ut.sweep_jobs)
            receipt = json.loads((job / 'completion-receipt.json').read_text())
            assert receipt['status'] == 'admitted'
            rows = list_session_admissions(authority.db, session_id=sid, pending_only=False)
            assert len(rows) == 1 and rows[0]['request_id'] == payload['identity']
            assert rows[0]['payload']['text'] == payload['text']
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
