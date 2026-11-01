"""The disabled lesson hook must not schedule work into a later test's environment."""
import importlib.util
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

HERE = Path(__file__).resolve().parents[1]


def test_disabled_lessons_do_not_schedule_after_environment_restore(monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location('coach_isolation_probe', HERE / '__init__.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setenv('COACH_LESSONS', '0')
    monkeypatch.setenv('COACH_SELFHEAL', 'off')
    core = mod._load('core')
    _, hooks = mod.build(store=core.Store(root=tmp_path / 'coach'), checkout=None)
    pending = []

    def capture(*, target, args=(), **kwargs):
        return SimpleNamespace(start=lambda: pending.append((target, args)))

    # Capture only the scheduling window. Core's lazy imports still get real threads.
    with monkeypatch.context() as patch:
        patch.setattr(threading, 'Thread', capture)
        hooks['pre_llm_call'](session_id='isolation-probe', user_message='Please fix this test')
    assert not pending, 'leaked _lesson_bg scheduled while COACH_LESSONS=0'


def test_enabled_lessons_complete_within_test(monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location('coach_enabled_probe', HERE / '__init__.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setenv('COACH_LESSONS', '1')
    called = threading.Event()
    monkeypatch.setattr(mod._load('lessons'), 'aux_judge', lambda *a: called.set() or '{}')
    coach, hooks = mod.build(store=mod._load('core').Store(root=tmp_path / 'coach'), checkout=None)
    coach.loop_state('enabled-probe')['last_reply'] = 'I fixed the code.'
    hooks['pre_llm_call'](session_id='enabled-probe', user_message='Please remember this rule')
    assert called.wait(10), 'enabled lessons did not run'


@pytest.mark.parametrize('event', ['open', 'sqlite3.connect', 'exec'])
def test_home_guard_records_and_refuses_even_swallowed_leaks(tmp_path, event):
    from home_guard import HomeGuard
    live = tmp_path / 'live'
    live.mkdir()
    guard = HomeGuard(live)
    path = live / 'state.db'
    arg = compile('', str(path), 'exec') if event == 'exec' else str(path)
    # Product paths often swallow exceptions. The independent violation ledger must survive.
    try:
        guard.audit(event, (arg,))
    except AssertionError:
        pass
    assert len(guard.violations) == 1
    assert str(path) in guard.violations[0]


def test_home_guard_resolves_sqlite_uri_symlink_and_foreign_plugin(tmp_path):
    from home_guard import HomeGuard
    live = tmp_path / 'live'
    live.mkdir()
    alias = tmp_path / 'alias'
    alias.symlink_to(live, target_is_directory=True)
    foreign = tmp_path / 'other-checkout/plugins/coach'
    foreign.mkdir(parents=True)
    guard = HomeGuard(live, plugin_roots=[foreign])
    with pytest.raises(AssertionError, match='sqlite3.connect'):
        guard.audit('sqlite3.connect', (f'file:{alias}/state.db?mode=ro',))
    with pytest.raises(AssertionError, match='foreign-plugin'):
        guard.audit('exec', (compile('', str(foreign/'__init__.py'), 'exec'),))
    assert len(guard.violations) == 2


def test_home_guard_allows_only_declared_code_and_fixture_roots(tmp_path):
    from home_guard import HomeGuard
    live = tmp_path / 'live'
    core = live / 'hermes-agent'
    fixture = live / 'owned-test'
    guard = HomeGuard(live, allowed=[core, fixture])
    guard.audit('open', (str(core/'module.py'),))
    guard.audit('sqlite3.connect', (str(fixture/'state.db'),))
    assert not guard.violations
    with pytest.raises(AssertionError):
        guard.audit('open', (str(live/'auth.json'),))


def test_runtime_code_exemption_is_read_only_and_never_sqlite(tmp_path):
    import os
    from home_guard import HomeGuard
    live = tmp_path / 'live'
    core = live / 'hermes-agent'
    guard = HomeGuard(live, code_roots=[core])
    guard.audit('open', (str(core/'module.py'), 'r', os.O_RDONLY))
    guard.audit('exec', (compile('', str(core/'module.py'), 'exec'),))
    assert not guard.violations
    for event, args in [('open', (str(core/'module.py'), 'w', os.O_WRONLY)),
                        ('sqlite3.connect', (str(core/'state.db'),))]:
        with pytest.raises(AssertionError):
            guard.audit(event, args)
    assert len(guard.violations) == 2
