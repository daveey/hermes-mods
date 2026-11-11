"""Plugin reload and the runtime ingress kill switch.

Core wires a native Slack handler factory once per ``(plugin, qualname)`` on a live Bolt client
(``gateway/platforms/base.py`` ``BasePlatformAdapter._wire_plugin_handlers``) and Bolt middleware
cannot be removed. After ``reload-plugins`` (``hermes_plugins.<slug>.*`` evicted from ``sys.modules``,
imported again, ``register()`` run again) the FIRST middleware keeps receiving events while the
tools come from the NEW registration: they must share one authenticated-event store, and the wired
middleware must run the NEWEST registration's gate/queue (continuous gating keeps its semantics).

A disable + reload cannot unwire that middleware either, so ingress also reads a per-profile kill
switch on every event: ``<home>/project-work.ingress-off`` or project-work absent from the
profile's ``plugins.enabled`` / present in ``plugins.disabled`` refuses before anything is queued.
"""
import importlib
import importlib.util
import json
import sys
import types

import pytest
from conftest import PACKAGE, ROOT

from test_ingress_gate_drift import (CLEAN, FakeSlack, NoStartQueue, StubCore, body, pending,
                                     run, write_plugins)

im = importlib.import_module(PACKAGE + '.ingress')
plugin = importlib.import_module(PACKAGE + '.__init__')

LIVE = '_project_work_live_ingress'
_loads = [0]


def fresh_plugin(monkeypatch):
    """Import project-work under a NEW package name, as core does after evicting hermes_plugins.<slug>."""
    _loads[0] += 1
    name = '_project_work_reload_%d' % _loads[0]
    spec = importlib.util.spec_from_file_location(name, ROOT / '__init__.py',
                                                  submodule_search_locations=[str(ROOT)])
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    stub_helpers(mod, monkeypatch)
    return mod


def stub_helpers(mod, monkeypatch):
    # Covered against real core elsewhere (test_sandbox_credentials.py, test_sandbox_mounts.py).
    monkeypatch.setattr(mod, '_core_credential_files', lambda: ([], []), raising=False)
    monkeypatch.setattr(mod, '_check_no_credential_dirs', lambda: None, raising=False)
    monkeypatch.setattr(mod, '_check_docker_env_refs', lambda: None, raising=False)
    monkeypatch.setattr(mod, '_check_no_env_passthrough', lambda: None, raising=False)
    monkeypatch.setattr(mod, 'DurableIngressQueue', NoStartQueue)
    monkeypatch.setattr(mod, 'Client', lambda endpoint: types.SimpleNamespace(
        call=lambda op, **kw: {'op': op, 'context': kw['context']}))


@pytest.fixture(autouse=True)
def no_live_holder(monkeypatch):
    monkeypatch.delitem(sys.modules, LIVE, raising=False)
    yield
    sys.modules.pop(LIVE, None)


class Ctx:
    def __init__(self):
        self.tools, self.factories = {}, []

    def register_tool(self, **kw):
        self.tools[kw['name']] = kw

    def register_platform_handler(self, platform, factory):
        assert platform == 'slack'
        self.factories.append((factory, 'project-work'))


class Native:
    def __init__(self):
        self.middleware = []

    def use(self, mw):
        self.middleware.append(mw)


def setup_home(core, env, tmp_path, monkeypatch, name='alpha'):
    service, _, _ = env
    home = tmp_path / name
    home.mkdir(parents=True)
    (home / 'project-work.json').write_text('{}')
    write_plugins(home)
    core.files[str(home)] = dict(CLEAN)
    endpoint = tmp_path / ('run-' + name) / 'runtime.sock'
    endpoint.parent.mkdir(mode=0o700, parents=True)
    return home, (service.policy, endpoint, None)


def register(mod, core, home, config, monkeypatch):
    monkeypatch.setattr(mod, 'load_config', lambda h: config)
    ctx = Ctx()
    reset = core.bind(home)
    try:
        mod.register(ctx)
    finally:
        reset()
    return ctx


def real_wire(monkeypatch, current):
    """Core's REAL _wire_plugin_handlers dedupe, fed ``current[0]``'s factories."""
    try:
        from gateway.platforms import base
        from hermes_cli import plugins as core_plugins
    except ImportError:
        pytest.skip('needs Hermes core (gateway.platforms.base)')
    manager = types.SimpleNamespace(get_platform_handler_factories=lambda platform: list(current[0].factories))
    monkeypatch.setattr(core_plugins, 'get_plugin_manager', lambda: manager)
    adapter = types.SimpleNamespace(platform=types.SimpleNamespace(value='slack'), name='Slack',
                                    _plugin_handler_native=None, _plugin_handlers_wired=None)
    return lambda native: base.BasePlatformAdapter._wire_plugin_handlers(adapter, native)


def tool_call(ctx, name, args, auth):
    """Call a registered tool inside a native Slack turn bound to ``auth`` (an AuthContext)."""
    from gateway.session_context import set_session_vars
    import contextvars

    def go():
        set_session_vars(platform='slack', profile=auth.profile, scope_id=auth.team_id, chat_id=auth.channel_id,
                         message_id=auth.message_id, thread_id=auth.message_id, user_id=auth.user_id,
                         cron_session='', session_key='k', session_id='s')
        return json.loads(ctx.tools[name]['handler'](args))
    return contextvars.copy_context().run(go)


def latest(home):
    return sys.modules[LIVE].current[im.home_key(home)]


def test_reimported_registration_authenticates_through_first_wired_middleware(env, tmp_path, monkeypatch):
    pytest.importorskip('gateway.session_context')
    _, auth, _ = env
    current = [None]
    wire = real_wire(monkeypatch, current)   # import real core BEFORE StubCore stubs hermes_constants
    core = StubCore(monkeypatch, ambient={'env_type': 'local'})
    home, config = setup_home(core, env, tmp_path, monkeypatch)
    native = Native()
    current[0] = register(fresh_plugin(monkeypatch), core, home, config, monkeypatch)
    wire(native)
    current[0] = register(fresh_plugin(monkeypatch), core, home, config, monkeypatch)   # reload-plugins
    wire(native)
    assert len(native.middleware) == 1   # core skipped the already-wired (plugin, qualname)
    reset = core.bind(home)
    try:
        assert run(native.middleware[0], body(auth, 'hello there'), FakeSlack()) == ('routed', [True])
    finally:
        reset()
    reset = core.bind(home)   # core's per-turn profile scope, as for any tool call
    try:
        out = tool_call(current[0], 'project_work_status', {'workflow_id': 'w1'}, auth)
    finally:
        reset()
    assert out['ok'] is True and out['result']['op'] == 'status', out
    second_ingress = latest(home)
    # The newest registration's queue took the command, not the stale one.
    reset = core.bind(home)
    try:
        run(native.middleware[0], body(auth, '!project request Add a README line'), None)
    finally:
        reset()
    assert len(pending(second_ingress.queue)) == 1


def test_wired_middleware_runs_the_newest_gate(env, tmp_path, monkeypatch):
    """Continuous gating survives reload: the first-wired middleware runs the NEW registration's gate."""
    _, auth, _ = env
    core = StubCore(monkeypatch, ambient={'env_type': 'local'})
    home, config = setup_home(core, env, tmp_path, monkeypatch)
    m1 = fresh_plugin(monkeypatch)
    ctx1 = register(m1, core, home, config, monkeypatch)
    native = Native()
    ctx1.factories[0][0](native, None)
    m2 = fresh_plugin(monkeypatch)
    register(m2, core, home, config, monkeypatch)
    calls = []
    monkeypatch.setattr(m2, 'require_sandbox', lambda: calls.append('new') or None)
    monkeypatch.setattr(m1, 'require_sandbox', lambda: calls.append('old') or None)
    run(native.middleware[0], body(auth, 'hi'), None)
    assert calls == ['new']


def test_reload_stops_the_replaced_queue_worker(env, tmp_path, monkeypatch):
    core = StubCore(monkeypatch, ambient={'env_type': 'local'})
    home, config = setup_home(core, env, tmp_path, monkeypatch)
    register(fresh_plugin(monkeypatch), core, home, config, monkeypatch)
    old = latest(home)
    register(fresh_plugin(monkeypatch), core, home, config, monkeypatch)
    assert old.queue.stop.is_set() and not latest(home).queue.stop.is_set()


def test_store_is_per_profile_and_bounded(env, tmp_path, monkeypatch):
    _, auth, _ = env
    core = StubCore(monkeypatch, ambient={'env_type': 'local'})
    a, ca = setup_home(core, env, tmp_path, monkeypatch, 'a')
    b, cb = setup_home(core, env, tmp_path, monkeypatch, 'b')
    mod = fresh_plugin(monkeypatch)
    xa = register(mod, core, a, ca, monkeypatch)
    xb = register(mod, core, b, cb, monkeypatch)
    na, nb = Native(), Native()
    xa.factories[0][0](na, None)
    xb.factories[0][0](nb, None)
    run(na.middleware[0], body(auth, 'hello'), None)
    assert len(latest(a).events) == 1 and len(latest(b).events) == 0
    assert latest(a).events is not latest(b).events
    for i in range(2050):
        ev = body(auth, 'x')
        ev['event']['ts'] = '1793300002.%06d' % i
        run(nb.middleware[0], ev, None)
    assert len(latest(b).events) == 2048 and len(latest(a).events) == 1


def test_unknown_holder_schema_fails_closed(env, tmp_path, monkeypatch):
    core = StubCore(monkeypatch, ambient={'env_type': 'local'})
    home, config = setup_home(core, env, tmp_path, monkeypatch)
    holder = types.ModuleType(LIVE)
    holder.version = 999
    monkeypatch.setitem(sys.modules, LIVE, holder)
    with pytest.raises(PermissionError, match='live ingress'):
        register(fresh_plugin(monkeypatch), core, home, config, monkeypatch)


# ------------------------------------------------------------------------------- kill switch
@pytest.mark.parametrize('switch', ['marker', 'disabled', 'not_enabled', 'config_unreadable', 'config_missing'])
def test_kill_switch_refuses_through_wired_middleware(env, tmp_path, monkeypatch, switch, caplog):
    _, auth, _ = env
    core = StubCore(monkeypatch, ambient={'env_type': 'local'})
    home, config = setup_home(core, env, tmp_path, monkeypatch)
    ctx = register(fresh_plugin(monkeypatch), core, home, config, monkeypatch)
    native = Native()
    ctx.factories[0][0](native, None)
    ingress = latest(home)
    if switch == 'marker':
        (home / plugin.INGRESS_OFF).write_text('')
    elif switch == 'disabled':
        write_plugins(home, enabled=['project-work'], disabled=['project-work'])
    elif switch == 'not_enabled':
        write_plugins(home, enabled=['askeve-github-read'])
    elif switch == 'config_unreadable':
        (home / 'config.yaml').write_text('plugins: [unclosed\n')
    else:
        (home / 'config.yaml').unlink()
    slack = FakeSlack()
    for text in ('!project request Add a README line', 'please run !project now'):
        assert run(native.middleware[0], body(auth, text), slack) == ('routed', [True])
    assert pending(ingress.queue) == [] and len(ingress.events) == 0 and slack.posts == []
    # Lifting the switch reopens ingress on the next event, no reload needed.
    if switch == 'marker':
        (home / plugin.INGRESS_OFF).unlink()
    else:
        write_plugins(home)
    run(native.middleware[0], body(auth, '!project request Add a README line'), None)
    assert len(pending(ingress.queue)) == 1


def test_kill_switch_only_affects_its_profile(env, tmp_path, monkeypatch):
    _, auth, _ = env
    core = StubCore(monkeypatch, ambient={'env_type': 'local'})
    a, ca = setup_home(core, env, tmp_path, monkeypatch, 'a')
    b, cb = setup_home(core, env, tmp_path, monkeypatch, 'b')
    mod = fresh_plugin(monkeypatch)
    xa, xb = register(mod, core, a, ca, monkeypatch), register(mod, core, b, cb, monkeypatch)
    na, nb = Native(), Native()
    xa.factories[0][0](na, None)
    xb.factories[0][0](nb, None)
    (a / plugin.INGRESS_OFF).write_text('')
    run(na.middleware[0], body(auth, '!project request one'), None)
    run(nb.middleware[0], body(auth, '!project request two'), None)
    assert pending(latest(a).queue) == [] and len(pending(latest(b).queue)) == 1


def test_no_live_registration_routes_natively(env, tmp_path, monkeypatch):
    _, auth, _ = env
    core = StubCore(monkeypatch, ambient={'env_type': 'local'})
    home, config = setup_home(core, env, tmp_path, monkeypatch)
    ctx = register(fresh_plugin(monkeypatch), core, home, config, monkeypatch)
    native = Native()
    ctx.factories[0][0](native, None)
    sys.modules[LIVE].current.clear()
    assert run(native.middleware[0], body(auth, '!project request x'), None) == ('routed', [True])
