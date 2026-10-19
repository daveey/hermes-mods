"""Load-time cost and deadline behavior (plugins.load_timeout_seconds, 10 s by default).

register() runs on core's loader thread, often while the main thread is still importing
run_agent/model_tools (model_tools discovers plugins at import time). Importing run_agent
from register() then waits on that module's import lock until core's deadline fires, and in
other processes it pays the whole cold run_agent import. So register() installs at once only
when every seam module is importable without that wait; otherwise it defers the atomic
install to the first pre_api_request (every recovery decision follows an API request).
"""
import ast
import json
import logging
import sys
from types import SimpleNamespace

import pytest


def seams():
    import agent.agent_runtime_helpers as helpers
    import agent.turn_recovery as recovery
    import agent.turn_api_error as api_error
    import run_agent
    return [(helpers, '_recover_rate_limit'), (recovery, 'route_classified_error'),
            (api_error, 'route_classified_error'), (run_agent, '_pool_may_recover_from_rate_limit')]


def active():
    return [bool(getattr(getattr(m, n), '_fast_rate_limit_fallback', False)) for m, n in seams()]


class Ctx:
    """Records what register() asks of core; abandon_on flips _load_abandoned the way core's
    deadline does while register() is still running."""

    def __init__(self, abandon_on=None):
        self._load_abandoned = False
        self.hooks, self.unloads, self.abandon_on = [], [], abandon_on

    def _maybe_abandon(self, what):
        if self.abandon_on == what:
            self._load_abandoned = True

    def register_hook(self, name, callback):
        if self._load_abandoned:
            return None  # core ignores registrations after the deadline
        self.hooks.append((name, callback))
        self._maybe_abandon('register_hook')

    def on_unload(self, callback):
        if self._load_abandoned:
            return None
        self.unloads.append(callback)
        self._maybe_abandon('on_unload')

    def fire(self, name='pre_api_request'):
        for hook, callback in self.hooks:
            if hook == name:
                assert callback(task_id='t', session_id='s', model='m') is None

    def unload(self):
        for callback in reversed(self.unloads):
            callback()


@pytest.fixture
def plugin(loaded_local_plugin):
    module = loaded_local_plugin._plugins['fast-rate-limit-fallback'].module
    loaded_local_plugin.unload()
    assert not any(active())
    originals = [(m, n, getattr(m, n)) for m, n in seams()]
    yield module
    for m, n, original in originals:  # a failed test must not leak patches into the next
        setattr(m, n, original)


def test_ready_core_installs_during_register(plugin):
    ctx = Ctx()
    plugin.register(ctx)
    assert all(active())
    ctx.unload()
    assert not any(active())


def test_run_agent_mid_import_defers_install_to_first_api_request(plugin, monkeypatch):
    import run_agent
    # The loader-thread case: run_agent is in sys.modules but still initializing on the main
    # thread (it imports model_tools, which discovers plugins).
    monkeypatch.setattr(run_agent.__spec__, '_initializing', True, raising=False)
    ctx = Ctx()
    plugin.register(ctx)
    assert not any(active())
    assert [name for name, _ in ctx.hooks] == ['pre_api_request']
    monkeypatch.setattr(run_agent.__spec__, '_initializing', False, raising=False)
    ctx.fire()
    assert all(active())
    first = [getattr(m, n) for m, n in seams()]
    ctx.fire()  # idempotent on every later request
    assert [getattr(m, n) for m, n in seams()] == first
    ctx.unload()
    assert not any(active())


def test_register_never_imports_run_agent(plugin):
    saved = sys.modules.pop('run_agent')
    try:
        ctx = Ctx()
        plugin.register(ctx)
        assert 'run_agent' not in sys.modules, 'register() imported run_agent on the loader thread'
    finally:
        sys.modules['run_agent'] = saved
    assert not any(active())
    ctx.fire()
    assert all(active())
    ctx.unload()
    assert not any(active())


def test_unload_before_first_request_cancels_deferred_install(plugin, monkeypatch):
    import run_agent
    monkeypatch.setattr(run_agent.__spec__, '_initializing', True, raising=False)
    ctx = Ctx()
    plugin.register(ctx)
    monkeypatch.setattr(run_agent.__spec__, '_initializing', False, raising=False)
    ctx.unload()
    ctx.fire()
    assert not any(active())


def test_deferred_install_that_fails_guards_warns_once_and_stays_off(plugin, monkeypatch, caplog):
    import run_agent
    import agent.retry_utils as retry_utils
    monkeypatch.setattr(run_agent.__spec__, '_initializing', True, raising=False)
    monkeypatch.setattr(plugin.logger, '_compatibility_warning_emitted', False, raising=False)
    ctx = Ctx()
    plugin.register(ctx)
    monkeypatch.setattr(run_agent.__spec__, '_initializing', False, raising=False)
    monkeypatch.setattr(retry_utils, 'RETRY_AFTER_CAP_S', 601)
    with caplog.at_level(logging.WARNING):
        ctx.fire()
        ctx.fire()
    assert not any(active())
    assert sum('fast-rate-limit-fallback DISABLED' in r.message for r in caplog.records) == 1


@pytest.mark.parametrize('when', ['on_unload', 'register_hook', 'before'])
def test_abandoned_load_leaves_core_unpatched(plugin, monkeypatch, caplog, when):
    """Core timed the load out: its on_unload never lands, so nothing could ever remove the
    patches. Undo them and keep core behavior (core already reports the plugin as failed)."""
    import run_agent
    if when == 'register_hook':
        monkeypatch.setattr(run_agent.__spec__, '_initializing', True, raising=False)
    ctx = Ctx(abandon_on=None if when == 'before' else when)
    ctx._load_abandoned = when == 'before'
    with caplog.at_level(logging.WARNING):
        plugin.register(ctx)
    monkeypatch.setattr(run_agent.__spec__, '_initializing', False, raising=False)
    ctx.fire()
    assert not any(active())
    warning = [r.getMessage() for r in caplog.records if "after core's deadline" in r.getMessage()]
    assert len(warning) == 1 and 'pid=' in warning[0] and 'role=' in warning[0]
    # A later, successful load in the same process still installs normally.
    fresh = Ctx()
    plugin.register(fresh)
    assert all(active())
    fresh.unload()
    assert not any(active())


def test_abandoned_second_owner_does_not_keep_the_install_alive(plugin):
    first = Ctx()
    plugin.register(first)
    late = Ctx(abandon_on='on_unload')
    plugin.register(late)  # reuses the existing installation, then core abandons it
    assert all(active())
    first.unload()
    assert not any(active()), 'abandoned owner leaked into state and pinned the patches'


def test_ast_fingerprints_are_cached_per_file_digest(plugin, monkeypatch):
    from hermes_constants import get_hermes_home
    cache = get_hermes_home() / 'cache' / 'fast-rate-limit-fallback' / 'code-fingerprints.json'
    ctx = Ctx()
    plugin.register(ctx)
    ctx.unload()
    assert cache.is_file()
    entries = json.loads(cache.read_text())['files']
    assert len(entries) >= len(json.loads((plugin._PLUGIN_DIR / 'compatibility.json').read_text()))

    def forbidden(*args, **kwargs):
        raise AssertionError('cache hit expected; source was re-parsed')
    monkeypatch.setattr(ast, 'parse', forbidden)
    ctx = Ctx()
    plugin.register(ctx)
    assert all(active())
    ctx.unload()


@pytest.mark.parametrize('content', ['{not json', '[]', '{"files": []}'])
def test_corrupt_cache_falls_back_to_parsing(plugin, content):
    from hermes_constants import get_hermes_home
    cache = get_hermes_home() / 'cache' / 'fast-rate-limit-fallback' / 'code-fingerprints.json'
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(content)
    ctx = Ctx()
    plugin.register(ctx)
    assert all(active())
    ctx.unload()
    assert isinstance(json.loads(cache.read_text())['files'], dict)


def test_cache_never_accepts_a_drifted_function(plugin, monkeypatch, caplog):
    """A cached digest is only reused for the same file bytes and line; a live function from
    elsewhere is parsed and compared against the pin, as before."""
    ctx = Ctx()
    plugin.register(ctx)
    ctx.unload()
    import agent.agent_runtime_helpers as helpers

    def _recover_rate_limit(pool, *, has_retried_429, error_context, api_key_hint, credential_id,
                            rotate_and_swap):
        return False, False
    monkeypatch.setattr(helpers, '_recover_rate_limit', _recover_rate_limit)
    monkeypatch.setattr(plugin.logger, '_compatibility_warning_emitted', False, raising=False)
    with caplog.at_level(logging.WARNING):
        plugin.register(Ctx())
    assert not getattr(helpers._recover_rate_limit, '_fast_rate_limit_fallback', False)
    assert any('fast-rate-limit-fallback DISABLED' in r.message for r in caplog.records)


def test_slow_load_logs_pid_role_and_phases(plugin, monkeypatch, caplog):
    monkeypatch.setattr(plugin, '_SLOW_LOAD_SECS', -1)
    monkeypatch.setenv('HERMES_KANBAN_TASK', 't_probe')
    ctx = Ctx()
    with caplog.at_level(logging.WARNING):
        plugin.register(ctx)
    ctx.unload()
    lines = [r.getMessage() for r in caplog.records if 'slow plugin load' in r.getMessage()]
    assert len(lines) == 1
    assert 'pid=' in lines[0] and 'role=kanban-worker:t_probe' in lines[0] and 'guards=' in lines[0]

