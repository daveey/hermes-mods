"""Continuous native-ingress gating: the sandbox gate re-runs for every Slack event, BEFORE the event
is observed, queued or answered, and in the REGISTERED profile's freshly read policy.

The Bolt middleware is not inside core's per-turn profile scope: it inherits the ContextVars the
Socket Mode task was created with at connect() (a terminal-policy snapshot), so a config edit after
register() (e.g. an operator adding docker_forward_env) must be seen by re-reading the profile.
"""
import asyncio
import contextvars
import importlib
import importlib.util
import logging
import os
import sys
import types
from typing import Any

import pytest
from conftest import PACKAGE

im = importlib.import_module(PACKAGE + '.ingress')
cm = importlib.import_module(PACKAGE + '.client')
plugin = importlib.import_module(PACKAGE + '.__init__')

CLEAN = {'env_type': 'docker', 'container_persistent': False}


def body(ctx, text):
    return {'type': 'event_callback', 'team_id': ctx.team_id, 'event': {
        'type': 'message', 'channel': ctx.channel_id, 'user': ctx.user_id,
        'ts': ctx.message_id, 'text': text}}


class FakeSlack:
    def __init__(self):
        self.posts = []

    async def chat_postMessage(self, **kwargs):
        self.posts.append(kwargs)
        return {'ok': True, 'ts': 'reply-ts'}


def run(middleware, event_body, slack):
    calls = []

    async def next_():
        calls.append(True)
        return 'routed'
    result = asyncio.run(middleware(event_body, next_, client=slack))
    return result, calls


def pending(queue):
    with queue.connect() as db:
        return [r[0] for r in db.execute('SELECT payload FROM pending')]


class NoStartQueue(cm.DurableIngressQueue):
    def start(self):  # no drain thread: these tests only inspect what was appended
        return None


@pytest.fixture
def helpers(monkeypatch):
    # Covered against real core elsewhere (test_sandbox_credentials.py, test_sandbox_mounts.py).
    monkeypatch.setattr(plugin, '_core_credential_files', lambda: ([], []), raising=False)
    monkeypatch.setattr(plugin, '_check_no_credential_dirs', lambda: None, raising=False)
    monkeypatch.setattr(plugin, '_check_docker_env_refs', lambda: None, raising=False)
    monkeypatch.setattr(plugin, '_check_no_env_passthrough', lambda: None, raising=False)
    monkeypatch.setattr(plugin, 'DurableIngressQueue', NoStartQueue)
    monkeypatch.setattr(plugin, 'Client', lambda endpoint: None)


class StubCore:
    """Core's per-profile seams: a home override and a bound terminal-policy snapshot (a ContextVar),
    each profile's on-disk ``terminal:`` config in ``files``. ``_get_env_config`` reads the bound
    snapshot, else the process-ambient policy, as core's ``terminal_env`` does."""

    def __init__(self, monkeypatch, ambient):
        self.files = {}
        self.ambient = ambient
        self.home: Any = contextvars.ContextVar('home', default=None)
        self.scope: Any = contextvars.ContextVar('scope', default=None)
        self.builds = []
        monkeypatch.setitem(sys.modules, 'hermes_constants', types.SimpleNamespace(
            get_hermes_home=lambda: self.home.get(),
            set_hermes_home_override=self.home.set, reset_hermes_home_override=self.home.reset))
        monkeypatch.setitem(sys.modules, 'tools.terminal_scope', types.SimpleNamespace(
            build_profile_terminal_scope=self.build,
            set_terminal_scope=self.scope.set, reset_terminal_scope=self.scope.reset))
        monkeypatch.setitem(sys.modules, 'tools.terminal_tool', types.SimpleNamespace(
            _get_env_config=lambda: dict(self.scope.get() or self.ambient)))

    def build(self, home):
        self.builds.append(str(home))
        return dict(self.files[str(home)])  # a snapshot, like core's

    def bind(self, home):
        """Enter ``home``'s scope as core does for connect(); returns a reset callable."""
        tokens = self.home.set(home), self.scope.set(self.build(home))
        return lambda: (self.scope.reset(tokens[1]), self.home.reset(tokens[0]))


def register_profile(core, env, tmp_path, monkeypatch, name='alpha'):
    """register() under profile ``name``'s scope (as core loads plugins), then wire Slack."""
    service, _, _ = env
    home = tmp_path / name
    home.mkdir()
    (home / 'project-work.json').write_text('{}')
    core.files[str(home)] = dict(CLEAN)
    endpoint = tmp_path / ('run-' + name) / 'runtime.sock'
    endpoint.parent.mkdir(mode=0o700)
    monkeypatch.setattr(plugin, 'load_config', lambda h: (service.policy, endpoint, None))
    factories = []
    ctx = types.SimpleNamespace(register_tool=lambda **kw: None,
                                register_platform_handler=lambda p, f: factories.append((p, f)))
    reset = core.bind(home)
    try:
        plugin.register(ctx)
    finally:
        reset()
    assert [p for p, _ in factories] == ['slack']
    native = types.SimpleNamespace(middleware=[])
    native.use = native.middleware.append
    factories[0][1](native, None)
    (middleware,) = native.middleware
    return home, middleware, middleware.__self__


def ingress_warnings(caplog):
    return [r for r in caplog.records if r.name == im.log.name and r.levelno == logging.WARNING]


def test_config_drift_after_register_closes_ingress(env, tmp_path, monkeypatch, helpers, caplog):
    """(a) Allowed at register(); then an operator adds docker_forward_env. The middleware runs with
    the connect-time snapshot still bound (what the Socket Mode task inherited) and must refuse."""
    _, ctx, _ = env
    core = StubCore(monkeypatch, ambient={'env_type': 'local'})
    home, middleware, ingress = register_profile(core, env, tmp_path, monkeypatch)
    reset = core.bind(home)  # connect-time snapshot: still clean
    try:
        core.files[str(home)]['docker_forward_env'] = ['GH_TOKEN']  # drift after register/connect
        slack = FakeSlack()
        for text in ('!project request Add a README line', 'please run !project now'):
            assert run(middleware, body(ctx, text), slack) == ('routed', [True])
    finally:
        reset()
    assert pending(ingress.queue) == []
    assert len(ingress.events) == 0
    assert slack.posts == []
    warnings = ingress_warnings(caplog)
    assert len(warnings) == 2  # one line per refused event
    for record in warnings:
        line = record.getMessage()
        assert 'refused' in line and 'PermissionError' in line
        assert 'GH_TOKEN' not in line and 'README' not in line and ctx.user_id not in line


def test_allowed_path_unchanged(env, tmp_path, monkeypatch, helpers, caplog):
    """(b) A clean profile still queues commands and answers owner near-misses, routing once."""
    _, ctx, _ = env
    core = StubCore(monkeypatch, ambient={'env_type': 'local'})
    home, middleware, ingress = register_profile(core, env, tmp_path, monkeypatch)
    reset = core.bind(home)
    try:
        slack = FakeSlack()
        assert run(middleware, body(ctx, '!project request Add a README line'), slack) == ('routed', [True])
        rows = pending(ingress.queue)
        assert len(rows) == 1 and '"operation": "request"' in rows[0]
        assert slack.posts == []
        assert run(middleware, body(ctx, 'please run !project now'), slack) == ('routed', [True])
        assert len(slack.posts) == 1 and slack.posts[0]['thread_ts'] == ctx.message_id
    finally:
        reset()
    assert len(ingress.events) == 1
    assert ingress_warnings(caplog) == []


class Odd(Exception):
    pass


@pytest.mark.parametrize('exc', [PermissionError('no'), RuntimeError('x'), KeyError('y'), Odd('z')])
def test_gate_raising_anything_fails_closed(env, tmp_path, exc, caplog):
    """(c) Any Exception from the gate refuses: no observe entry, no queue row, no notice; next once."""
    service, ctx, _ = env
    queue = cm.DurableIngressQueue(tmp_path / 'q' / 'q.db', None)
    calls = []

    def gate():
        calls.append('gate')
        raise exc
    ingress = im.Ingress(service.policy, None, queue, gate=gate)
    slack = FakeSlack()
    for text in ('!project request Add a README line', 'please run !project now'):
        assert run(ingress.middleware, body(ctx, text), slack) == ('routed', [True])
    assert calls == ['gate', 'gate']
    assert pending(queue) == [] and len(ingress.events) == 0 and slack.posts == []
    assert [type(exc).__name__ in r.getMessage() for r in ingress_warnings(caplog)] == [True, True]


def test_gate_runs_before_observe(env, tmp_path):
    service, ctx, _ = env
    order = []
    ingress = im.Ingress(service.policy, None, cm.DurableIngressQueue(tmp_path / 'q' / 'q.db', None),
                         gate=lambda: order.append('gate'))
    observe = ingress.observe
    ingress.observe = lambda b: (order.append('observe'), observe(b))[1]
    run(ingress.middleware, body(ctx, '!project request x'), None)
    assert order == ['gate', 'observe']


def test_middleware_gate_uses_registered_profile(env, tmp_path, monkeypatch, helpers):
    """(d) The middleware evaluates the gate in the REGISTERED profile's freshly read policy, whatever
    profile scope is ambient: another profile's refusing policy does not close it, another profile's
    clean policy does not open a drifted registered profile."""
    _, ctx, _ = env
    core = StubCore(monkeypatch, ambient=dict(CLEAN))
    home, middleware, ingress = register_profile(core, env, tmp_path, monkeypatch)
    other = str(tmp_path / 'other')
    core.files[other] = {'env_type': 'local'}  # this profile would refuse
    reset = core.bind(other)
    try:
        assert run(middleware, body(ctx, '!project request one'), None) == ('routed', [True])
    finally:
        reset()
    assert len(pending(ingress.queue)) == 1
    core.files[other] = dict(CLEAN)  # now the other profile is clean and the registered one drifts
    core.files[str(home)]['container_persistent'] = True
    reset = core.bind(other)
    try:
        assert run(middleware, body(ctx, '!project request two'), None) == ('routed', [True])
    finally:
        reset()
    assert len(pending(ingress.queue)) == 1
    # Each event re-read the registered profile (not just the bind() calls above).
    assert core.builds.count(str(home)) >= 3


@pytest.mark.parametrize('broken', ['policy_unreadable', 'core_without_scope_api'])
def test_unenterable_profile_scope_fails_closed(env, tmp_path, monkeypatch, helpers, broken, caplog):
    _, ctx, _ = env
    core = StubCore(monkeypatch, ambient=dict(CLEAN))
    _, middleware, ingress = register_profile(core, env, tmp_path, monkeypatch)
    if broken == 'policy_unreadable':
        def unreadable(home):
            raise OSError('cannot parse config.yaml')
        monkeypatch.setattr(sys.modules['tools.terminal_scope'], 'build_profile_terminal_scope', unreadable)
    else:
        monkeypatch.setitem(sys.modules, 'tools.terminal_scope', None)  # import raises ImportError
    assert run(middleware, body(ctx, '!project request x'), FakeSlack()) == ('routed', [True])
    assert pending(ingress.queue) == [] and len(ingress.events) == 0
    assert ['PermissionError' in r.getMessage() for r in ingress_warnings(caplog)] == [True]


def test_tool_gate_is_unchanged_by_ingress_gate(env):
    """The tool path keeps calling ``gate`` (core's per-turn scope is already fresh there)."""
    service, _, _ = env
    calls = []
    ingress = im.Ingress(service.policy, None, gate=lambda: calls.append('tool'),
                         ingress_gate=lambda: calls.append('ingress'))
    with pytest.raises(Exception):
        ingress.tool('project_work_status', {'workflow_id': 'x'})  # no authenticated turn
    assert calls == ['tool']


# --------------------------------------------------------------------------------------- real core
@pytest.fixture
def real_core(monkeypatch, helpers):
    if not os.environ.get('HERMES_CORE_CHECKOUT') and importlib.util.find_spec('tools.terminal_scope') is None:
        pytest.skip('needs Hermes core (tools.terminal_scope not importable, HERMES_CORE_CHECKOUT unset)')
    for name in ('hermes_constants', 'tools.terminal_scope', 'tools.terminal_tool'):
        monkeypatch.delitem(sys.modules, name, raising=False)
    hc = importlib.import_module('hermes_constants')
    ts = importlib.import_module('tools.terminal_scope')
    importlib.import_module('tools.terminal_tool')
    return hc, ts


def write_terminal(home, **keys):
    lines = ['terminal:'] + ['  %s: %s' % (k, v) for k, v in keys.items()]
    (home / 'config.yaml').write_text('\n'.join(lines) + '\n')


def test_real_core_drift_and_profile_binding(env, tmp_path, monkeypatch, real_core):
    """(a)+(d) against real core's home override, terminal scope and _get_env_config."""
    hc, ts = real_core
    service, ctx, _ = env
    alpha, other = tmp_path / 'alpha', tmp_path / 'other'
    for home in (alpha, other):
        home.mkdir()
    (alpha / 'project-work.json').write_text('{}')
    write_terminal(alpha, backend='docker', container_persistent='false')
    write_terminal(other, backend='local')
    endpoint = tmp_path / 'run' / 'runtime.sock'
    endpoint.parent.mkdir(mode=0o700)
    monkeypatch.setattr(plugin, 'load_config', lambda h: (service.policy, endpoint, None))
    factories = []
    reg = types.SimpleNamespace(register_tool=lambda **kw: None,
                                register_platform_handler=lambda p, f: factories.append(f))

    def bound(home):
        tokens = hc.set_hermes_home_override(str(home)), ts.set_terminal_scope(ts.build_profile_terminal_scope(home))
        return lambda: (ts.reset_terminal_scope(tokens[1]), hc.reset_hermes_home_override(tokens[0]))
    reset = bound(alpha)  # core loads the plugin and connects Slack under alpha's scope
    try:
        plugin.register(reg)
        native = types.SimpleNamespace(middleware=[])
        native.use = native.middleware.append
        factories[0](native, None)
        (middleware,) = native.middleware
        ingress = middleware.__self__
        assert run(middleware, body(ctx, '!project request one'), None) == ('routed', [True])
        assert len(pending(ingress.queue)) == 1
        # Drift after connect: the snapshot bound above still says clean; the file now forwards a credential.
        write_terminal(alpha, backend='docker', container_persistent='false', docker_forward_env='[GH_TOKEN]')
        assert run(middleware, body(ctx, '!project request two'), None) == ('routed', [True])
        assert len(pending(ingress.queue)) == 1
    finally:
        reset()
    # Ambient = another (refusing) profile; the registered profile, clean again, still admits.
    write_terminal(alpha, backend='docker', container_persistent='false')
    reset = bound(other)
    try:
        with pytest.raises(PermissionError):
            plugin.require_sandbox()  # precondition: the ambient profile itself refuses
        assert run(middleware, body(ctx, '!project request three'), None) == ('routed', [True])
    finally:
        reset()
    rows = pending(ingress.queue)
    assert len(rows) == 2 and 'three' in rows[1]
