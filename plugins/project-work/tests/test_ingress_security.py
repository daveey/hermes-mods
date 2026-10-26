import asyncio
import contextvars
import importlib
import sys
import threading
import types
import pytest
from conftest import PACKAGE

im = importlib.import_module(PACKAGE + '.ingress')
cm = importlib.import_module(PACKAGE + '.client')
rm = importlib.import_module(PACKAGE + '.runtime')
plugin = importlib.import_module(PACKAGE + '.__init__')


def body(ctx, **extra):
    return {'type': 'event_callback', 'team_id': ctx.team_id, 'event': {
        'type': 'message', 'channel': ctx.channel_id, 'user': ctx.user_id,
        'ts': ctx.message_id, 'text': '!project request work', **extra}}


@pytest.mark.parametrize('thread', [None, '', 'message', 'wrong'])
def test_root_native_thread_identity(env, monkeypatch, thread):
    service, ctx, _ = env
    ingress = im.Ingress(service.policy, None)
    ingress.observe(body(ctx))
    values = dict(PLATFORM='slack', PROFILE=ctx.profile, SCOPE_ID=ctx.team_id,
                  CHAT_ID=ctx.channel_id, MESSAGE_ID=ctx.message_id, USER_ID=ctx.user_id,
                  THREAD_ID=thread)
    variables = {'HERMES_SESSION_' + k: contextvars.ContextVar(k, default=v) for k, v in values.items()}
    monkeypatch.setitem(sys.modules, 'gateway', types.SimpleNamespace(
        session_context=types.SimpleNamespace(_VAR_MAP=variables)))
    if thread == 'wrong':
        with pytest.raises(PermissionError): ingress.current()
    else:
        assert ingress.current() == ctx


@pytest.mark.parametrize('subtype', ['file_share', 'thread_broadcast', 'me_message'])
def test_admitted_human_subtypes_and_external_team(env, tmp_path, subtype):
    service, ctx, _ = env
    queue = cm.DurableIngressQueue(tmp_path / 'queue' / 'q.db', None)
    ingress = im.Ingress(service.policy, None, queue)
    ingress.enqueue(body(ctx, subtype=subtype, thread_ts='root', text='followup'))
    with queue.connect() as db:
        assert db.execute('SELECT count(*) FROM pending').fetchone()[0] == 1
    assert ingress.observe(body(ctx, subtype=subtype, user_team='external')) is None
    assert ingress.observe(body(ctx, subtype='bot_message')) is None


@pytest.mark.parametrize('failure', ['observe', 'append', 'malformed'])
def test_middleware_always_routes(env, failure):
    service, ctx, _ = env
    def fail(*args, **kwargs): raise OSError('unavailable')
    ingress = im.Ingress(service.policy, None, types.SimpleNamespace(append=fail))
    if failure == 'observe': ingress.observe = fail
    called = []
    async def next_():
        called.append(True)
        return 'routed'
    assert asyncio.run(ingress.middleware(None if failure == 'malformed' else body(ctx), next_)) == 'routed'
    assert called == [True]


def test_outage_survives_restart_and_retries(env, tmp_path):
    service, ctx, _ = env
    path = tmp_path / 'queue' / 'q.db'
    class Offline:
        def call(self, *args, **kwargs): raise ConnectionError()
    queue = cm.DurableIngressQueue(path, Offline())
    ingress = im.Ingress(service.policy, queue.client, queue)
    async def next_(): return True
    assert asyncio.run(ingress.middleware(body(ctx), next_))
    with pytest.raises(ConnectionError): queue.drain_once()
    delivered = threading.Event()
    class Online:
        def call(self, *args, **kwargs): delivered.set()
    restarted = cm.DurableIngressQueue(path, Online(), retry_interval=.01)
    restarted.start()
    assert delivered.wait(2)
    restarted.close()
    with restarted.connect() as db:
        assert db.execute('SELECT count(*) FROM pending').fetchone()[0] == 0


def test_network_worker_cannot_block_durable_ingress(env, tmp_path):
    service, ctx, _ = env
    entered, release = threading.Event(), threading.Event()
    class Blocked:
        def call(self, *args, **kwargs):
            entered.set()
            release.wait(5)
    queue = cm.DurableIngressQueue(tmp_path / 'queue' / 'q.db', Blocked())
    ingress = im.Ingress(service.policy, queue.client, queue)
    ingress.enqueue(body(ctx))
    queue.start()
    try:
        assert entered.wait(2)
        async def next_(): return True
        assert asyncio.run(asyncio.wait_for(ingress.middleware(body(ctx, ts='second'), next_), .5))
        with queue.connect() as db:
            assert db.execute('SELECT count(*) FROM pending').fetchone()[0] == 2
    finally:
        release.set()
        queue.close()


@pytest.mark.parametrize('config,allowed', [
    ({'env_type': 'local'}, False), ({'env_type': 'ssh'}, False), ({}, False),
    ({'env_type': 'custom'}, False), ({'env_type': 'docker'}, True),
    ({'env_type': 'modal'}, True), ({'env_type': 'daytona'}, True),
    ({'env_type': 'docker', 'docker_volumes': ['/host:/host']}, False),
    ({'env_type': 'docker', 'docker_extra_args': ['--privileged']}, False),
    ({'env_type': 'docker', 'docker_mount_cwd_to_workspace': True}, False),
])
def test_registration_sandbox_gate(monkeypatch, config, allowed):
    monkeypatch.setitem(sys.modules, 'tools.terminal_tool', types.SimpleNamespace(_get_env_config=lambda: config))
    if allowed:
        plugin.require_sandbox()
    else:
        with pytest.raises(PermissionError): plugin.require_sandbox()


def test_unsafe_backend_refuses_before_registration(env, tmp_path, monkeypatch):
    service, _, _ = env
    (tmp_path / 'project-work.json').write_text('{}')
    monkeypatch.setitem(sys.modules, 'hermes_constants', types.SimpleNamespace(get_hermes_home=lambda: tmp_path))
    monkeypatch.setitem(sys.modules, 'tools.terminal_tool', types.SimpleNamespace(_get_env_config=lambda: {'env_type': 'local'}))
    monkeypatch.setattr(plugin, 'load_config', lambda home: (service.policy, tmp_path / 'runtime.sock', None))
    calls = []
    registry = types.SimpleNamespace(register_tool=lambda **kw: calls.append(kw),
        register_platform_handler=lambda *args: calls.append(args))
    with pytest.raises(PermissionError): plugin.register(registry)
    assert calls == []
    assert not (tmp_path / 'ingress.sqlite3').exists()


def test_plan_generation_required_and_forwarded():
    calls = []
    class Service:
        def propose_plan(self, workflow_id, plan_text, generation):
            calls.append((workflow_id, plan_text, generation))
            return {}
    controller = rm.Controller(Service())
    with pytest.raises(ValueError):
        controller.call('propose_plan', {'workflow_id': 'id', 'plan_text': 'plan'})
    controller.call('propose_plan', {'workflow_id': 'id', 'plan_text': 'plan', 'generation': 3})
    assert calls == [('id', 'plan', 3)]
