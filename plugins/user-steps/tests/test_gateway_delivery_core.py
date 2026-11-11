"""Real gateway FIFO admission (model execution suppressed), alongside TUI tests."""
import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from core_runtime import require_isolated_core
require_isolated_core()
from test_decisions import load_module, ROOT
from tests.tui_gateway import test_prompt_admission as core_prompt_admission  # noqa: E402

owner = core_prompt_admission.owner


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


@asynccontextmanager
async def legacy_gateway(tmp_path, monkeypatch, *, cold=False, compressed=False):
    """Adopt an actual pre-authority row, retaining its opaque stored identity."""
    from gateway import run
    from gateway.config import GatewayConfig
    from gateway.session import SessionStore
    from gateway.session_authority import initialize_session_authority
    from gateway.session_controls import AuthorityConnection
    from gateway.session_local_migration import adopt_legacy_session
    from hermes_cli import plugins
    from hermes_constants import get_hermes_home
    from hermes_cli.install_identity import get_install_id
    from hermes_cli.profiles import current_profile_name

    monkeypatch.setattr(run, '_load_gateway_config', lambda: {'platform_toolsets': {'cli': []}})
    stores = []
    async def start(instance):
        store = SessionStore(tmp_path / 'sessions', GatewayConfig())
        stores.append(store)
        runner = SimpleNamespace(session_store=store, _session_db=store._db, adapters={}, _draining=False,
            _running=True, _gateway_loop=asyncio.get_running_loop(), _cached_agent_for=lambda route: None)
        runner._adapter_for_source = lambda source: runner.adapters.get(source.platform)
        authority = await initialize_session_authority(runner, profile_id=str(get_hermes_home()), instance_id=instance)
        monkeypatch.setattr(authority, '_schedule_admitted', lambda *args: None)
        @asynccontextmanager
        async def scope(source):
            yield
        runner._plugin_injection_scope = scope
        monkeypatch.setattr(plugins, '_published_gateway_message_injector', (runner, None))
        return runner, authority

    try:
        runner, authority = await start('first')
        sid = '20260101_010101_abcdef'
        authority.db.create_session(sid, source='desktop', model='m', cwd=str(tmp_path))
        authority.db.append_message(sid, role='user', content='Retained legacy history')
        viewer = AuthorityConnection(authority, object(), {'provider': 'local', 'user_id': 'human',
            'profile_id': authority.profile_id, 'instance_id': authority.instance_id})
        ref = adopt_legacy_session(authority, viewer.actor, authority.db.get_session(sid))
        assert ref.session_id == sid and not sid.startswith('local-')
        if compressed:
            from tests.gateway.test_local_resume_compression_child import _compress
            live = authority.sessions[sid]
            runner.session_store._record_gateway_session_peer(sid, live.route, live.source)
            _compress(authority.db, sid, 'legacy-compressed-child', 'desktop')
            runner.session_store._record_gateway_session_peer('legacy-compressed-child', live.route, live.source)
        if cold:
            runner, authority = await start('restarted')
            # Startup may eagerly restore local sessions; exercise a persisted,
            # currently unmounted owner rather than relying on startup ordering.
            authority.sessions.clear()
            runner.adapters.clear()
            assert sid not in authority.sessions
        op = {'operationId': 'legacy-operation', 'owner': {'storedSessionId': sid,
            'profile': current_profile_name(), 'gatewayId': f'install:{get_install_id()}'},
            'intent': {'effect': 'answer', 'answer': 'Open a pull request'}}
        yield runner, authority, op
    finally:
        for store in stores:
            store.close_all_db_handles()


@pytest.mark.asyncio
@pytest.mark.parametrize('cold', [False, True])
@pytest.mark.parametrize('compressed', [False, True])
async def test_adopted_legacy_gateway_operation_commits_once_and_readback(tmp_path, monkeypatch, cold, compressed):
    from hermes_state_runtime import list_session_admissions
    from tui_gateway import server
    gateway = load_module('legacy_gateway_delivery_test', ROOT / 'gateway_delivery.py')
    prompt = load_module('legacy_prompt_gateway_test', ROOT / 'prompt_delivery.py')
    receiver = prompt.PromptAdmission()
    async with legacy_gateway(tmp_path, monkeypatch, cold=cold, compressed=compressed) as (_, authority, op):
        sid = op['owner']['storedSessionId']
        assert not any(s.get('session_key') == sid for s in server._sessions.values())
        receipt = await asyncio.to_thread(receiver.deliver, op)
        assert receipt['admission']['transport'] == 'gateway_fifo'
        assert await asyncio.to_thread(receiver.readback, op, receipt)
        assert await asyncio.to_thread(receiver.deliver, op) == receipt
        assert await asyncio.to_thread(gateway.call, 'prompt.admit', receiver._params(op)) == receipt['admission']
        rows = list_session_admissions(authority.db, session_id=sid, pending_only=False)
        assert len(rows) == 1
        assert rows[0]['payload']['text'] == receiver._params(op)['text']
        assert rows[0]['payload']['local_automation_v1']['owner'] == sid
        assert rows[0]['request_id'] == gateway._identity(receiver._params(op))
        bad = dict(receiver._params(op), payload_digest='wrong')
        with pytest.raises(RuntimeError, match='mismatch'):
            await asyncio.to_thread(gateway.call, 'prompt.admission.get', bad)


@pytest.mark.asyncio
async def test_legacy_selector_reads_persisted_ownership_without_restoring(tmp_path, monkeypatch):
    gateway = load_module('legacy_owner_selector_test', ROOT / 'gateway_delivery.py')
    async with legacy_gateway(tmp_path, monkeypatch, cold=True) as (runner, authority, op):
        sid = op['owner']['storedSessionId']
        with authority.db._read_ctx() as observer:
            before = observer.execute('SELECT key,value FROM state_meta ORDER BY key').fetchall()
            version = observer.execute('PRAGMA data_version').fetchone()[0]
            assert gateway.owns_session(sid)
            assert not gateway.owns_session('unowned-legacy-id')
            assert observer.execute('SELECT key,value FROM state_meta ORDER BY key').fetchall() == before
            assert observer.execute('PRAGMA data_version').fetchone()[0] == version
        assert sid not in authority.sessions
        assert not runner.adapters


@pytest.mark.asyncio
@pytest.mark.parametrize('field,value', [('gatewayId', 'install:foreign'), ('profile', 'foreign')])
async def test_adopted_legacy_foreign_owner_is_refused(tmp_path, monkeypatch, field, value):
    from hermes_state_runtime import list_session_admissions
    prompt = load_module('legacy_foreign_prompt_test', ROOT / 'prompt_delivery.py')
    async with legacy_gateway(tmp_path, monkeypatch) as (_, authority, op):
        op['owner'][field] = value
        with pytest.raises(RuntimeError, match='Prompt owner differs from authenticated runtime'):
            await asyncio.to_thread(prompt.PromptAdmission().deliver, op)
        assert list_session_admissions(authority.db, session_id=op['owner']['storedSessionId']) == []


@pytest.mark.asyncio
@pytest.mark.parametrize('published', [False, True])
async def test_unowned_legacy_id_does_not_acquire_gateway_ownership(tmp_path, monkeypatch, published):
    from hermes_cli import plugins
    from hermes_state_runtime import list_session_admissions
    gateway = load_module('unowned_gateway_selector_test', ROOT / 'gateway_delivery.py')
    prompt = load_module('unowned_prompt_test', ROOT / 'prompt_delivery.py')
    async with legacy_gateway(tmp_path, monkeypatch) as (_, authority, op):
        sid = '20260102_020202_abcdef'
        # The same historical source label and ID shape confer no Local ownership.
        authority.db.create_session(sid, source='desktop', model='m', cwd=str(tmp_path))
        op['owner']['storedSessionId'] = sid
        if not published:
            monkeypatch.setattr(plugins, '_published_gateway_message_injector', None)
        assert not gateway.owns_session(sid)
        with pytest.raises(RuntimeError, match='no unique live owner'):
            await asyncio.to_thread(prompt.PromptAdmission().deliver, op)
        assert list_session_admissions(authority.db, session_id=sid) == []
        assert sid not in authority.sessions


@pytest.mark.asyncio
async def test_adopted_legacy_owner_requires_a_published_gateway(tmp_path, monkeypatch):
    from hermes_cli import plugins
    from hermes_state_runtime import list_session_admissions
    gateway = load_module('unpublished_legacy_selector_test', ROOT / 'gateway_delivery.py')
    prompt = load_module('unpublished_legacy_prompt_test', ROOT / 'prompt_delivery.py')
    async with legacy_gateway(tmp_path, monkeypatch, cold=True) as (_, authority, op):
        sid = op['owner']['storedSessionId']
        monkeypatch.setattr(plugins, '_published_gateway_message_injector', None)
        assert not gateway.owns_session(sid)
        with pytest.raises(RuntimeError, match='no unique live owner'):
            await asyncio.to_thread(prompt.PromptAdmission().deliver, op)
        assert list_session_admissions(authority.db, session_id=sid) == []
        assert sid not in authority.sessions


@pytest.mark.asyncio
@pytest.mark.parametrize('fault,reason', [('corrupt', 'storage_unavailable'),
    ('database', 'fixture database read failed'), ('profile', 'gateway_profile_unavailable')])
async def test_invalid_legacy_ownership_never_falls_back_to_tui(tmp_path, monkeypatch, fault, reason):
    import sqlite3
    from gateway.session_authorities import SessionAuthorities
    from hermes_state_local import POLICY_PREFIX
    from hermes_state_runtime import list_session_admissions
    from tui_gateway import server
    prompt = load_module('invalid_legacy_prompt_test', ROOT / 'prompt_delivery.py')
    async with legacy_gateway(tmp_path, monkeypatch, cold=True) as (runner, authority, op):
        sid = op['owner']['storedSessionId']
        with monkeypatch.context() as failed:
            failed.setattr(server, 'handle_request', lambda *args: pytest.fail('invalid ownership fell back to TUI'))
            if fault == 'corrupt':
                # Corrupt only this fixture-owned receipt through core's write seam.
                authority.db._write_sql('UPDATE state_meta SET value=? WHERE key=?', ('{}', POLICY_PREFIX + sid))
            elif fault == 'database':
                def unreadable():
                    raise sqlite3.DatabaseError(reason)
                failed.setattr(authority.db, '_read_ctx', unreadable)
            else:
                failed.setattr(runner, 'session_authorities', SessionAuthorities(tmp_path / 'unserved'), raising=False)
            with pytest.raises((RuntimeError, ValueError, sqlite3.DatabaseError), match=reason):
                await asyncio.to_thread(prompt.PromptAdmission().deliver, op)
        assert list_session_admissions(authority.db, session_id=sid) == []
        assert sid not in authority.sessions


@pytest.mark.asyncio
async def test_inline_tui_owner_stays_inline_with_an_unrelated_published_gateway(owner, tmp_path, monkeypatch):
    from hermes_state_runtime import list_session_admissions
    db, _, params, started = owner
    gateway = load_module('inline_gateway_selector_test', ROOT / 'gateway_delivery.py')
    prompt = load_module('inline_prompt_with_gateway_test', ROOT / 'prompt_delivery.py')
    async with legacy_gateway(tmp_path, monkeypatch) as (_, authority, op):
        sid = params['stored_session_id']
        op['owner']['storedSessionId'] = sid
        assert not sid.startswith('local-') and not gateway.owns_session(sid)
        receiver = prompt.PromptAdmission()
        receipt = await asyncio.to_thread(receiver.deliver, op)
        assert 'transport' not in receipt['admission']
        assert await asyncio.to_thread(receiver.readback, op, receipt)
        assert await asyncio.to_thread(receiver.deliver, op) == receipt
        assert len(started) == 1
        assert len(db.get_messages(sid)) == 1
        assert list_session_admissions(authority.db, session_id=sid) == []
