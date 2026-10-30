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


@pytest.fixture
def tool_home(tmp_path, monkeypatch):
    home = tmp_path.resolve() / 'profile'
    (home / 'sandbox-bin').mkdir(parents=True)
    tool = home / 'sandbox-bin' / 'gh'
    tool.write_bytes(b'\x7fELF')
    monkeypatch.setitem(sys.modules, 'hermes_constants', types.SimpleNamespace(get_hermes_home=lambda: home))
    return home, tool


def gate(monkeypatch, **config):
    config = {'env_type': 'docker', **config}
    monkeypatch.setitem(sys.modules, 'tools.terminal_tool', types.SimpleNamespace(_get_env_config=lambda: config))
    return plugin.require_sandbox


@pytest.mark.parametrize('args', [['--runtime=runsc'], ['--runtime', 'runsc'], []])
def test_sandbox_accepts_runsc_runtime_arg(monkeypatch, args):
    gate(monkeypatch, docker_extra_args=args)()


@pytest.mark.parametrize('args', [['--privileged'], ['-v', '/:/host'], ['--runtime=runsc', '--privileged'],
                                  ['--runtime=runc'], ['--network=host'], ['--cap-add=ALL']])
def test_sandbox_refuses_other_extra_args(monkeypatch, args):
    with pytest.raises(PermissionError): gate(monkeypatch, docker_extra_args=args)()


def test_sandbox_accepts_sandbox_bin_ro_file_mount(monkeypatch, tool_home):
    _, tool = tool_home
    gate(monkeypatch, docker_extra_args=['--runtime=runsc'],
         docker_volumes=[f'{tool}:/usr/local/bin/gh:ro'])()


@pytest.mark.parametrize('mode', ['', ':rw', ':ro,rw', ':z'])
def test_sandbox_refuses_rw_mount(monkeypatch, tool_home, mode):
    _, tool = tool_home
    with pytest.raises(PermissionError): gate(monkeypatch, docker_volumes=[f'{tool}:/usr/local/bin/gh{mode}'])()


def test_sandbox_refuses_dir_mount(monkeypatch, tool_home):
    home, _ = tool_home
    (home / 'sandbox-bin' / 'sub').mkdir()
    with pytest.raises(PermissionError):
        gate(monkeypatch, docker_volumes=[f'{home / "sandbox-bin" / "sub"}:/usr/local/bin/sub:ro'])()
    with pytest.raises(PermissionError):
        gate(monkeypatch, docker_volumes=[f'{home / "sandbox-bin"}:/usr/local/bin/x:ro'])()


def test_sandbox_refuses_docker_socket_mount(monkeypatch, tool_home):
    with pytest.raises(PermissionError):
        gate(monkeypatch, docker_volumes=['/var/run/docker.sock:/usr/local/bin/docker.sock:ro'])()


def test_sandbox_refuses_socket_in_sandbox_bin(monkeypatch, tool_home):
    import socket
    home, _ = tool_home
    path = home / 'sandbox-bin' / 's'
    sock = socket.socket(socket.AF_UNIX)
    try:
        monkeypatch.chdir(path.parent)  # AF_UNIX paths are capped near 108 bytes
        sock.bind('s')
        with pytest.raises(PermissionError): gate(monkeypatch, docker_volumes=[f'{path}:/usr/local/bin/s:ro'])()
    finally:
        sock.close()


def test_sandbox_refuses_symlinked_src(monkeypatch, tool_home, tmp_path):
    home, _ = tool_home
    target = tmp_path / 'secret'
    target.write_text('x')
    link = home / 'sandbox-bin' / 'link'
    link.symlink_to(target)
    with pytest.raises(PermissionError): gate(monkeypatch, docker_volumes=[f'{link}:/usr/local/bin/link:ro'])()


def test_sandbox_refuses_symlinked_sandbox_bin_dir(monkeypatch, tmp_path):
    home, real = tmp_path.resolve() / 'profile', tmp_path.resolve() / 'elsewhere'
    real.mkdir(); home.mkdir()
    (real / 'gh').write_text('x')
    (home / 'sandbox-bin').symlink_to(real)
    monkeypatch.setitem(sys.modules, 'hermes_constants', types.SimpleNamespace(get_hermes_home=lambda: home))
    with pytest.raises(PermissionError):
        gate(monkeypatch, docker_volumes=[f'{home / "sandbox-bin" / "gh"}:/usr/local/bin/gh:ro'])()


@pytest.mark.parametrize('where', ['home', 'other-sandbox-bin'])
def test_sandbox_refuses_src_outside_sandbox_bin(monkeypatch, tool_home, tmp_path, where):
    home, _ = tool_home
    path = home / 'config.yaml' if where == 'home' else tmp_path.resolve() / 'other' / 'sandbox-bin' / 'gh'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('x')
    with pytest.raises(PermissionError): gate(monkeypatch, docker_volumes=[f'{path}:/usr/local/bin/x:ro'])()


@pytest.mark.parametrize('src', ['{home}/sandbox-bin/../sandbox-bin/gh', 'sandbox-bin/gh', '{home}/sandbox-bin/missing'])
def test_sandbox_refuses_dotdot_relative_or_missing_src(monkeypatch, tool_home, src):
    home, _ = tool_home
    with pytest.raises(PermissionError):
        gate(monkeypatch, docker_volumes=[src.format(home=home) + ':/usr/local/bin/gh:ro'])()


@pytest.mark.parametrize('dst', ['/etc/passwd', '/usr/local/bin/../../etc/gh', '/usr/local/bin/sub/gh', '/usr/local/bin'])
def test_sandbox_refuses_dst_outside_usr_local_bin(monkeypatch, tool_home, dst):
    _, tool = tool_home
    with pytest.raises(PermissionError): gate(monkeypatch, docker_volumes=[f'{tool}:{dst}:ro'])()


def test_sandbox_refuses_mount_cwd_even_with_allowed_mount(monkeypatch, tool_home):
    _, tool = tool_home
    with pytest.raises(PermissionError):
        gate(monkeypatch, docker_mount_cwd_to_workspace=True, docker_extra_args=['--runtime=runsc'],
             docker_volumes=[f'{tool}:/usr/local/bin/gh:ro'])()


def test_sandbox_refuses_one_bad_volume_among_good(monkeypatch, tool_home):
    _, tool = tool_home
    with pytest.raises(PermissionError):
        gate(monkeypatch, docker_volumes=[f'{tool}:/usr/local/bin/gh:ro', '/var/run/docker.sock:/var/run/docker.sock'])()


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


class FakeSlack:
    def __init__(self, fail=False):
        self.posts, self.fail = [], fail

    async def chat_postMessage(self, **kwargs):
        if self.fail:
            raise ConnectionError('slack down')
        self.posts.append(kwargs)
        return {'ok': True, 'ts': 'reply-ts'}


def run_middleware(ingress, event_body, client):
    async def next_(): return 'routed'
    return asyncio.run(ingress.middleware(event_body, next_, client=client))


def pending(queue):
    with queue.connect() as db:
        return [r[0] for r in db.execute('SELECT payload FROM pending')]


def test_top_level_owner_request_is_queued_and_logged(env, tmp_path, caplog):
    # Regression: an owner's top-level `!project request` (no @mention) reaches the queue
    # that the runtime plans from, and the decision is logged.
    service, ctx, _ = env
    queue = cm.DurableIngressQueue(tmp_path / 'queue' / 'q.db', None)
    ingress, slack = im.Ingress(service.policy, None, queue), FakeSlack()
    caplog.set_level('INFO')
    assert run_middleware(ingress, body(ctx, text='!project request Add a README line'), slack) == 'routed'
    rows = pending(queue)
    assert len(rows) == 1 and '"operation": "request"' in rows[0] and 'Add a README line' in rows[0]
    assert slack.posts == []  # the plan reply comes from the runtime, not a usage notice
    assert 'Project ingress queued request ts=message' in caplog.text


def test_pasted_instruction_gets_logged_refusal_and_thread_reply(env, tmp_path, caplog):
    # Regression for the live miss: the owner sent the step title, not the command.
    service, ctx, _ = env
    queue = cm.DurableIngressQueue(tmp_path / 'queue' / 'q.db', None)
    ingress, slack = im.Ingress(service.policy, None, queue), FakeSlack()
    caplog.set_level('INFO')
    text = 'Send the !project request command in the Ask Eve Slack channel from your own account'
    assert run_middleware(ingress, body(ctx, text=text), slack) == 'routed'
    assert pending(queue) == []
    assert 'Project ingress ignored ts=message thread=None user=owner: not_a_command' in caplog.text
    assert len(slack.posts) == 1
    post = slack.posts[0]
    assert post['channel'] == ctx.channel_id and post['thread_ts'] == ctx.message_id
    assert "isn't a `!project` command" in post['text'] and '`!project request ' in post['text']
    assert 'Project ingress notice sent: thread=message reply_ts=reply-ts' in caplog.text


@pytest.mark.parametrize('text,reason', [
    ('!project request', 'empty_request'),
    ('!project approve abc', 'malformed_approve'),
    ('please run !PROJECT request foo', 'not_a_command'),
])
def test_top_level_near_misses_reply_to_owner(env, tmp_path, text, reason, caplog):
    service, ctx, _ = env
    queue = cm.DurableIngressQueue(tmp_path / 'queue' / 'q.db', None)
    ingress, slack = im.Ingress(service.policy, None, queue), FakeSlack()
    caplog.set_level('INFO')
    run_middleware(ingress, body(ctx, text=text), slack)
    assert pending(queue) == [] and len(slack.posts) == 1
    assert slack.posts[0]['text'] == im.NOTICES[reason]
    assert f': {reason}' in caplog.text


def test_no_reply_for_ordinary_chat_non_owner_or_threads(env, tmp_path, caplog):
    service, ctx, _ = env
    queue = cm.DurableIngressQueue(tmp_path / 'queue' / 'q.db', None)
    ingress, slack = im.Ingress(service.policy, None, queue), FakeSlack()
    caplog.set_level('INFO')
    run_middleware(ingress, body(ctx, text='good morning'), slack)
    assert 'not_project_traffic' in caplog.text
    stranger = body(ctx, text='Send the !project request command', ts='m2')
    stranger['event']['user'] = 'stranger'
    run_middleware(ingress, stranger, slack)
    # A malformed approve in a thread stays a followup observation, as before.
    run_middleware(ingress, body(ctx, ts='m3', thread_ts='root', text='!project approve x'), slack)
    assert slack.posts == []
    rows = pending(queue)
    assert len(rows) == 1 and '"operation": "observe_followup"' in rows[0]


def test_dropped_channel_event_is_logged(env, tmp_path, caplog):
    service, ctx, _ = env
    ingress = im.Ingress(service.policy, None, cm.DurableIngressQueue(tmp_path / 'q' / 'q.db', None))
    caplog.set_level('INFO')
    run_middleware(ingress, body(ctx, bot_id='B1', text='!project request x'), FakeSlack())
    assert 'Project ingress ignored ts=message: not an authenticated human event' in caplog.text


@pytest.mark.parametrize('client', [None, 'failing'])
def test_notice_failure_never_breaks_routing(env, tmp_path, client, caplog):
    service, ctx, _ = env
    ingress = im.Ingress(service.policy, None, cm.DurableIngressQueue(tmp_path / 'q' / 'q.db', None))
    slack = FakeSlack(fail=True) if client else None
    assert run_middleware(ingress, body(ctx, text='do !project stuff'), slack) == 'routed'
    assert ('notice failed' if client else 'no native Slack client') in caplog.text


def test_bolt_injects_client_into_middleware(env):
    # slack_bolt builds middleware kwargs from parameter names; `client` must be one of them.
    import inspect
    service, _, _ = env
    params = inspect.signature(im.Ingress(service.policy, None).middleware).parameters
    assert list(params) == ['body', 'next', 'client']


def test_real_bolt_middleware_receives_client(env, tmp_path):
    # Runs the real slack_bolt kwargs injection when it is installed (gateway env).
    pytest.importorskip('slack_bolt')
    from slack_bolt.kwargs_injection.async_utils import build_async_required_kwargs
    from slack_bolt.request.async_request import AsyncBoltRequest
    import logging
    service, ctx, _ = env
    ingress = im.Ingress(service.policy, None, cm.DurableIngressQueue(tmp_path / 'q' / 'q.db', None))
    from slack_sdk.web.async_client import AsyncWebClient
    class RealClient(AsyncWebClient):
        posts = []
        async def chat_postMessage(self, **kwargs):
            self.posts.append(kwargs)
            return {'ok': True, 'ts': 'reply-ts'}
    slack = RealClient(token='xoxb-test')
    request = AsyncBoltRequest(body='{}', mode='socket_mode')
    request.body = body(ctx, text='Send the !project request command')
    request.context['client'] = slack
    async def next_(): return 'routed'
    kwargs = build_async_required_kwargs(
        logger=logging.getLogger('t'), required_arg_names=['self', 'body', 'next', 'client'],
        request=request, response=None, next_func=next_)
    assert kwargs['client'] is slack
    assert asyncio.run(ingress.middleware(**kwargs)) == 'routed'
    assert len(slack.posts) == 1
