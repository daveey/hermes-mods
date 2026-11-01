"""Native messaging-session completion: real GatewayRunner, DiscordAdapter, SessionAuthority and
SQLite FIFO in an isolated home. Only model scheduling is suppressed; nothing reaches a network."""
import asyncio
import json
import logging

import pytest
from test_closed_asks import ut

USER = '4242'  # fixture platform user id, not a real account


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    monkeypatch.setenv('HERMES_USER_TERMINAL_DIR', str(tmp_path / 'jobs'))
    monkeypatch.setenv('HERMES_DISABLE_LAZY_INSTALLS', '1')
    monkeypatch.setenv('DISCORD_ALLOWED_USERS', USER)
    for name in ('HERMES_KANBAN_TASK', 'HERMES_KANBAN_WORKER_TASK', 'HERMES_KANBAN_DB'):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(ut, 'confirm_mode', lambda: 'off')
    monkeypatch.setattr(ut, 'max_wait_minutes', lambda: 120)
    monkeypatch.setattr(ut, 'reattach_grace_minutes', lambda: 10)
    return tmp_path


def run(env, monkeypatch, body, **kw):
    asyncio.run(_harness(env, monkeypatch, body, **kw))


async def _harness(tmp_path, monkeypatch, body, compressed=False):
    from gateway.config import GatewayConfig, Platform, PlatformConfig
    from gateway.platforms.base import SendResult
    from gateway.platforms.event import MessageEvent
    from gateway.run import GatewayRunner
    from gateway.session_authority import initialize_session_authority
    from gateway.session_results import execution_result
    from hermes_cli import plugins
    from plugins.platforms.discord.adapter import DiscordAdapter

    runner = GatewayRunner(GatewayConfig())
    adapter = DiscordAdapter(PlatformConfig(enabled=True, token='fixture-token', typing_indicator=False))

    async def send(chat_id, content, reply_to=None, metadata=None):
        return SendResult(success=True, message_id='sent')
    monkeypatch.setattr(adapter, 'send', send)
    adapter.gateway_runner = runner
    runner.adapters = {Platform.DISCORD: adapter}
    authority = await initialize_session_authority(runner, profile_id='default', instance_id='owner')
    # Every store this test writes is the isolated home's.
    assert str(authority.db.db_path).startswith(str(tmp_path))
    runner._wire_adapter_handlers(adapter)
    turns = []

    async def turn(event, source, quick_key, run_generation):
        turns.append(event.text)
        captured = execution_result.get()
        if captured is not None:
            captured['result'] = {'final_response': 'reply', 'messages': []}
        return 'reply'
    monkeypatch.setattr(runner, '_handle_message_with_agent', turn)
    source = adapter.build_source(chat_id=USER, chat_type='dm', user_id=USER)
    key = runner.session_store._generate_session_key(source)
    await adapter.handle_message(MessageEvent(text='start a login', source=source, message_id='human-1'))
    for _ in range(200):
        if turns and not [r for r in _rows(authority, runner.session_store.peek_session_id(key))
                          if r['status'] != 'terminal']:
            break
        await asyncio.sleep(0.05)
    assert turns == ['start a login']
    owner_sid = runner.session_store.peek_session_id(key)
    if compressed:
        from tests.gateway.test_local_resume_compression_child import _compress
        _compress(authority.db, owner_sid, 'native-child', authority.db.get_session(owner_sid)['source'])
        runner.session_store._record_gateway_session_peer('native-child', key, source)
        runner.session_store.lookup_by_session_key(key).session_id = 'native-child'
        owner_sid = 'native-child'
    # From here on admitted producer rows stay queued: no model turn runs.
    monkeypatch.setattr(authority, '_schedule_admitted', lambda *args: None)
    runner._gateway_loop = asyncio.get_running_loop()
    runner._running = True
    monkeypatch.setattr(plugins, '_published_gateway_message_injector', (runner, None))
    ctx = type('Ctx', (), {})()
    ctx.runner, ctx.authority, ctx.adapter, ctx.source, ctx.key = runner, authority, adapter, source, key
    ctx.owner_sid, ctx.root = owner_sid, authority.logical_owner(owner_sid)
    ctx.turns = turns
    try:
        await body(ctx)
    finally:
        runner.session_store.close_all_db_handles() if hasattr(runner.session_store, 'close_all_db_handles') else None


def _rows(authority, sid):
    from hermes_state_runtime import list_session_admissions
    return list_session_admissions(authority.db, session_id=authority.logical_owner(sid), pending_only=False)


def _producer_rows(authority, sid, job):
    identity = json.dumps(['user-terminal', job.name, 'final'], separators=(',', ':'))
    return [r for r in _rows(authority, sid) if r['request_id'] == identity]


def _job(tmp_path, sid, final='ran', tail='RESULT-END'):
    job = ut.create_job('true', 'fixture', str(tmp_path), owner={'kind': 'session', 'session_id': sid})
    (job / 'output.log').write_text('x' * 50000 + tail)
    ut._take_final(job, {'status': final, 'exit_code': 0 if final == 'ran' else None})
    return job


def _pending(job):
    path = job / 'completion-pending.json'
    return json.loads(path.read_text()) if path.exists() else None


def _expire_backoff(job):
    state = _pending(job)
    state['next_at'] = 0
    (job / 'completion-pending.json').write_text(json.dumps(state))


async def _sweep(times=1):
    for _ in range(times):
        await asyncio.to_thread(ut.sweep_jobs)


@pytest.mark.parametrize('compressed', [False, True])
@pytest.mark.parametrize('final', ['ran', 'failed', 'abandoned'])
def test_native_session_commits_once_as_automation(env, monkeypatch, final, compressed):
    async def body(c):
        job = _job(env, c.owner_sid, final)
        # The live session is evicted: the route is resolved from the store, not a live owner.
        c.authority.sessions.pop(c.root, None)
        await _sweep(2)
        receipt = json.loads((job / 'completion-receipt.json').read_text())
        assert receipt['status'] == 'admitted' and receipt['session_id'] == c.root
        rows = _producer_rows(c.authority, c.root, job)
        assert len(rows) == 1
        row = rows[0]
        # A trusted producer admission on the route, never a messaging (human) principal.
        assert row['principal_id'] == 'automation:' + c.key
        envelope = row['payload']['native_text_v1']
        assert envelope['automation']['identity'] == row['request_id']
        assert envelope['automation']['owner'] == c.root
        assert envelope['route'] == c.key
        text = row['payload']['text']
        assert text.startswith('[User-terminal completion — tool result, not user input]')
        assert f'status: {final}' in text and text.endswith('RESULT-END') and len(text) < 6500
        assert c.turns == ['start a login']  # nothing was run as human input
        # A lost receipt reads back the same admission; a late output change cannot alter it.
        (job / 'completion-receipt.json').unlink()
        (job / 'output.log').write_text('late mutation')
        await _sweep()
        assert json.loads((job / 'completion-receipt.json').read_text()) == receipt
        assert len(_producer_rows(c.authority, c.root, job)) == 1
        assert not (job / 'completion-pending.json').exists()
    run(env, monkeypatch, body, compressed=compressed)


def test_route_moved_by_new_stays_pending_and_logs_once(env, monkeypatch, caplog):
    async def body(c):
        old = c.owner_sid
        # The rotation /new performs (its command turn is not scheduled in this fixture).
        await c.runner.async_session_store.reset_session(c.key)
        c.authority.sessions.pop(c.root, None)
        assert c.runner.session_store.peek_session_id(c.key) != old
        job = _job(env, old)
        caplog.set_level(logging.INFO)
        await _sweep()
        state = _pending(job)
        assert state['kind'] == 'blocked' and state['reason'] == 'route_moved'
        assert not (job / 'completion-receipt.json').exists()
        assert _producer_rows(c.authority, old, job) == []
        new = c.runner.session_store.peek_session_id(c.key)
        assert _producer_rows(c.authority, new, job) == []
        for _ in range(3):
            _expire_backoff(job)
            await _sweep()
        assert _pending(job)['attempts'] == 4
        mine = [r for r in caplog.records if job.name in r.getMessage()]
        assert len(mine) == 1 and mine[0].levelno == logging.WARNING
        assert not [r for r in caplog.records if r.exc_info]
    run(env, monkeypatch, body)


def test_unauthorized_sender_is_held_then_admitted_once_policy_allows(env, monkeypatch, caplog):
    async def body(c):
        monkeypatch.setenv('DISCORD_ALLOWED_USERS', '1')  # today's policy no longer admits the route
        job = _job(env, c.owner_sid)
        caplog.set_level(logging.INFO)
        await _sweep()
        assert _pending(job)['reason'] == 'unauthorized'
        assert _producer_rows(c.authority, c.root, job) == []
        calls = []
        real = c.authority.admit_automation
        monkeypatch.setattr(c.authority, 'admit_automation', lambda *a, **k: calls.append(a) or real(*a, **k))
        await _sweep(3)  # within backoff: no attempt at all
        assert _pending(job)['attempts'] == 1 and calls == []
        monkeypatch.setenv('DISCORD_ALLOWED_USERS', USER)
        _expire_backoff(job)
        await _sweep()
        assert json.loads((job / 'completion-receipt.json').read_text())['status'] == 'admitted'
        assert len(_producer_rows(c.authority, c.root, job)) == 1 and len(calls) == 1
        assert not (job / 'completion-pending.json').exists()
        assert any('after 1 deferred attempt' in r.getMessage() for r in caplog.records)
    run(env, monkeypatch, body)


def test_session_without_gateway_route_uses_trusted_producer_ingress(env, monkeypatch):
    async def body(c):
        from pathlib import Path
        from hermes_state_producer_admission import read_producer_admission
        # A legacy inline TUI conversation: no local receipt or gateway route.
        c.authority.db.create_session('inline-tui-session', source='tui')
        job = _job(env, 'inline-tui-session')
        await _sweep(2)
        receipt = json.loads((job / 'completion-receipt.json').read_text())
        assert receipt['status'] == 'admitted'
        assert receipt['ingress'] == 'producer_completion'
        row = read_producer_admission(producer=receipt['producer'], profile_home=Path(c.authority.db.db_path).parent)
        assert row['outcome'] == 'admitted' and row['status'] == 'queued'
        assert _producer_rows(c.authority, 'inline-tui-session', job) == []
    run(env, monkeypatch, body)


def test_transient_refusal_retries_quietly_and_admits_once(env, monkeypatch, caplog):
    from hermes_state_runtime import RuntimeStoreError

    async def body(c):
        real = c.authority.admit_automation
        failures = [RuntimeStoreError('runtime_draining'), TimeoutError('slow loop'), ValueError('boom')]

        async def flaky(*a, **k):
            if failures:
                raise failures.pop(0)
            return await real(*a, **k)
        monkeypatch.setattr(c.authority, 'admit_automation', flaky)
        job = _job(env, c.owner_sid)
        caplog.set_level(logging.DEBUG)
        reasons = []
        for _ in range(3):
            await _sweep()
            reasons.append((_pending(job)['kind'], _pending(job)['reason']))
            await _sweep(2)  # inside the backoff window: nothing is attempted or logged
            _expire_backoff(job)
        assert reasons == [('transient', 'store_runtime_draining'), ('transient', 'admission_timeout'),
                           ('blocked', 'unexpected_ValueError')]
        await _sweep()
        assert json.loads((job / 'completion-receipt.json').read_text())['status'] == 'admitted'
        assert len(_producer_rows(c.authority, c.root, job)) == 1
        mine = [r for r in caplog.records if job.name in r.getMessage()]
        # One line per distinct reason plus the recovery line; only the unexpected one has a traceback.
        assert [r.levelno for r in mine] == [logging.INFO, logging.INFO, logging.WARNING, logging.INFO]
        assert [bool(r.exc_info) for r in mine] == [False, False, True, False]
        # The plugin's generic reconciler never logged an exception for these retries.
        assert not [r for r in caplog.records if 'session completion pending' in r.getMessage()]
    run(env, monkeypatch, body)
