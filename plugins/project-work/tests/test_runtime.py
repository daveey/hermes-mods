import asyncio
from dataclasses import asdict
import importlib
import json
import os
import threading
import uuid
import pytest
from conftest import PACKAGE

runtime = importlib.import_module(PACKAGE + '.runtime')
ingress_mod = importlib.import_module(PACKAGE + '.ingress')
config_mod = importlib.import_module(PACKAGE + '.config')


def test_real_unix_socket_request_and_status(env, tmp_path):
    service, ctx, _ = env
    directory = tmp_path.parent / ('sock-' + uuid.uuid4().hex[:8])
    directory.mkdir(mode=0o700)
    endpoint = directory / 'runtime.sock'
    with runtime.Server(endpoint, runtime.Controller(service)) as server:
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            client = runtime.Client(endpoint)
            row = client.call('request', context=asdict(ctx), text='requested work')
            assert row['state'] == 'awaiting_approval'
            assert client.call('status', context=asdict(ctx), workflow_id=row['workflow_id']) == row
            assert client.call('reconcile')[0]['workflow_id'] == row['workflow_id']
            with pytest.raises(RuntimeError):
                client.call('request', context=asdict(ctx), text='work', repo='other/repo')
            assert (endpoint.stat().st_mode & 0o777) == 0o600
        finally:
            server.shutdown()
            worker.join(timeout=10)


def test_runtime_unknown_thread_does_not_create(env):
    service, ctx, _ = env
    assert runtime.Controller(service).call('observe_followup', {'context': asdict(ctx), 'text': 'unrelated'}) == {'ignored': True}
    assert not service.store.rows()


def test_private_policy_exact_schema_and_socket(env, tmp_path):
    service, _, _ = env
    home = tmp_path / 'home'
    home.mkdir(mode=0o700)
    data = {'version': 1, 'policy': service.policy.document(),
            'runtime_socket': str(home / 'project-work' / 'runtime.sock')}
    path = home / 'project-work.json'
    path.write_text(json.dumps(data))
    path.chmod(0o600)
    assert config_mod.load_config(home)[0] == service.policy
    data['shell'] = 'arbitrary'
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        config_mod.load_config(home)
    del data['shell']
    data['runtime_socket'] = str(tmp_path / 'socket')
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        config_mod.load_config(home)


def test_ingress_native_event_and_tool_scope(env, tmp_path):
    service, ctx, _ = env
    class Client:
        def __init__(self): self.calls = []
        def call(self, op, **args):
            self.calls.append((op, args))
            return runtime.Controller(service).call(op, args)
    client = Client()
    queue = importlib.import_module(PACKAGE + '.client').DurableIngressQueue(tmp_path / 'queue' / 'ingress.db', client)
    ingress = ingress_mod.Ingress(service.policy, client, queue)
    body = {'type': 'event_callback', 'team_id': ctx.team_id,
            'event': {'type': 'message', 'user': ctx.user_id, 'channel': ctx.channel_id,
                      'ts': ctx.message_id, 'text': '!project request Build a feature'}}
    called = []
    async def next_(): called.append(True)
    asyncio.run(ingress.middleware(body, next_))
    assert called and not client.calls
    assert queue.drain_once()
    assert client.calls[0][0] == 'request'
    assert ingress.observe(body) == ctx
    bad = dict(body, team_id='elsewhere')
    assert ingress.observe(bad) is None
    bot = dict(body, event=dict(body['event'], bot_id='bot'))
    assert ingress.observe(bot) is None
    ingress.current = lambda: ctx
    with pytest.raises(ValueError):
        ingress.tool('project_work_request', {'request': 'work', 'profile': 'elsewhere'})
    assert ingress.tool('project_work_request', {'request': 'work'})['workflow_id']


def test_only_two_model_tools_registered(env):
    service, _, _ = env
    names = []
    class Registry:
        def register_tool(self, **kwargs): names.append(kwargs['name'])
    ingress_mod.register_tools(Registry(), ingress_mod.Ingress(service.policy, None))
    assert names == ['project_work_request', 'project_work_status']


def test_controller_rejects_nonowner_approval(env):
    service, ctx, _ = env
    controller = runtime.Controller(service)
    row = controller.call('request', {'context': asdict(ctx), 'text': 'work'})
    plan = controller.call('propose_plan', {'workflow_id': row['workflow_id'], 'plan_text': 'Concrete scope', 'generation': row['generation']})
    context = asdict(ctx)
    context['user_id'] = 'member'
    assert controller.call('approve', {'context': context, 'workflow_id': row['workflow_id'], 'plan_hash': plan['plan_hash']}) == {'rejected': True, 'reason': 'RejectedScope'}
    assert service.store.get(row['workflow_id'])['approved'] is False


def test_environment_cannot_impersonate_native_context(env, monkeypatch):
    import contextvars
    import sys
    import types
    service, ctx, _ = env
    ingress = ingress_mod.Ingress(service.policy, None)
    names = ('PLATFORM', 'PROFILE', 'SCOPE_ID', 'CHAT_ID', 'MESSAGE_ID', 'USER_ID', 'THREAD_ID')
    variables = {'HERMES_SESSION_' + n: contextvars.ContextVar(n, default=None) for n in names}
    fake = types.ModuleType('gateway')
    fake.session_context = types.SimpleNamespace(_VAR_MAP=variables)
    monkeypatch.setitem(sys.modules, 'gateway', fake)
    values = ('slack', ctx.profile, ctx.team_id, ctx.channel_id, ctx.message_id, ctx.user_id, '')
    for name, value in zip(names, values):
        monkeypatch.setenv('HERMES_SESSION_' + name, value)
    with pytest.raises(PermissionError): ingress.current()
    for name, value in zip(names, values): variables['HERMES_SESSION_' + name].set(value)
    with pytest.raises(PermissionError): ingress.current()
    ingress.observe({'type': 'event_callback', 'team_id': ctx.team_id, 'event': {
        'type': 'message', 'channel': ctx.channel_id, 'user': ctx.user_id, 'ts': ctx.message_id}})
    assert ingress.current() == ctx
