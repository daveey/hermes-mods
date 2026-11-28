"""Actual configured callbacks, authenticated registration-only bootstrap and real exec READY."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest

pytest.importorskip('hermes_cli.serve_producer_callbacks')
from test_panel_callback_inventory import configured
from test_panel_provider_manifest import load_api, _scratch_home, _complete_cut

PLUGINS = Path(__file__).resolve().parents[2]


def real_registrations(tmp_path, monkeypatch):
    activity, host, _ = configured(tmp_path, monkeypatch)
    for registry in (activity._done_checks, activity._waiting_sources, host._declared,
                     host._event_sources, host._done_event_sources):
        registry.clear()
    for name, file, entry in (('kanban-honest-done', 'core.py', 'register_with'),
                              ('chat-closeout', '__init__.py', 'register_waiting_with')):
        spec = importlib.util.spec_from_file_location('_parent_safe_' + name.replace('-', '_'), PLUGINS / name / file)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        # Use ordinary package loading for chat-closeout and the standalone core for honest-done.
        monkeypatch.setitem(sys.modules, spec.name, module)
        spec.loader.exec_module(module)
        getattr(module, entry)(activity)
    return activity


def test_real_exec_configured_callback_registrars_reach_ready(tmp_path, monkeypatch):
    from hermes_cli.serve_producer_admission import LaunchGate
    from hermes_cli import serve_producer_broker as broker
    _scratch_home(tmp_path, monkeypatch)
    real_registrations(tmp_path, monkeypatch)
    runtime = tmp_path / 'runtime'
    relay = load_api().make_panel_relay(tmp_path, runtime)
    gate = LaunchGate.acquire(runtime)
    try:
        relay.start_for_production(cut=_complete_cut(gate, tmp_path), gate=gate, ready_timeout=20)
        owner = broker.read_owner(runtime)
        assert owner is not None
        assert relay.config['provider']['manifest']['callback_registry']['done']
        assert relay.config['provider']['manifest']['callback_registry']['waiting']
    finally:
        gate.close()
        assert relay.retire(timeout=10), 'fixture shared producer did not retire'


def test_manifest_authenticates_registration_support_closure(tmp_path, monkeypatch):
    _scratch_home(tmp_path, monkeypatch)
    real_registrations(tmp_path, monkeypatch)
    manifest = load_api()._panel_provider_spec()['manifest']
    support = manifest['callback_bootstrap']
    assert set(support) == {'kanban-honest-done', 'chat-closeout'}
    assert set(support['chat-closeout']['files']) == {'__init__.py', 'core.py', 'waiting_inputs.py', 'hook_work.py'}
    assert 'core.py' in support['kanban-honest-done']['files']
    assert '__init__.py' not in support['kanban-honest-done']['files']


@pytest.mark.parametrize('factory', [False, True], ids=['bootstrap', 'provider-factory'])
def test_authenticated_bootstrap_is_registration_only_in_clean_exec(tmp_path, monkeypatch, factory):
    _scratch_home(tmp_path, monkeypatch)
    real_registrations(tmp_path, monkeypatch)
    api = load_api()
    manifest_path = tmp_path / 'manifest.json'
    manifest_path.write_text(json.dumps(api._panel_provider_spec()['manifest']))
    probe = tmp_path / 'bootstrap_probe.py'
    probe.write_text(textwrap.dedent('''
        import importlib.util, json, sys, threading
        from pathlib import Path
        from hermes_cli import kanban_session_activity as activity
        from hermes_cli import kanban_annotation_host as host
        path, manifest_path = map(Path, sys.argv[1:3])
        factory = sys.argv[3] == 'True'
        if factory:
            path = path.with_name('producer_provider.py')
        spec = importlib.util.spec_from_file_location('_bootstrap_probe', path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        before = set(sys.modules)
        original_start = threading.Thread.start
        def no_threads(thread, *a, **kw):
            # The API's existing launch-profile resolver uses one joined helper;
            # no registrar or producer lane may start a thread in this probe.
            if factory and sys._getframe(1).f_code.co_name == '_launch_profile':
                return original_start(thread, *a, **kw)
            raise AssertionError('registrar started a thread')
        threading.Thread.start = no_threads
        forbidden = {'register', 'waiting_source', 'waiting_inputs', 'needs_attention', 'start_thread'}
        def no_calls(frame, event, arg):
            if event == 'call' and str(frame.f_globals.get('__name__', '')).startswith('_panel_safe_'):
                assert frame.f_code.co_name not in forbidden, frame.f_code.co_name
        sys.setprofile(no_calls)
        try:
            manifest = json.loads(manifest_path.read_text())
            if factory:
                provider = mod.open_provider({'provider': {'partitioned': True, 'manifest': manifest}})
                assert isinstance(provider, mod.PanelProducerSet)
                provider.close()
            else:
                mod.bootstrap(manifest)
        finally:
            sys.setprofile(None)
        loaded = set(sys.modules) - before
        assert not any('owner_wake' in n or n in {'_panel_safe_chat_closeout.routing', '_panel_safe_kanban_honest_done.routing'} or n.startswith('coach')
                       or n == 'run_agent' or n.startswith('gateway.run') for n in loaded), sorted(loaded)
        assert set(activity._done_checks) == {'kanban-honest-done'}
        assert set(activity._waiting_sources) == {'chat-closeout'}
        assert host._declared['chat-closeout'][0] is activity._waiting_sources['chat-closeout']
        for prefix, files in (('_panel_safe_chat_closeout', manifest['callback_bootstrap']['chat-closeout']['files']),
                              ('_panel_safe_kanban_honest_done', manifest['callback_bootstrap']['kanban-honest-done']['files'])):
            expected = {prefix} | {prefix + '.' + Path(file).stem for file in files if file != '__init__.py'}
            actual = {name for name in sys.modules if name == prefix or name.startswith(prefix + '.')}
            assert actual == expected, (actual, expected)
        from hermes_cli.serve_producer_callbacks import verify
        verify(manifest['callback_registry'])
        print('registered-only exact inventory OK')
    '''))
    env = dict(os.environ, PYTHONPATH=os.pathsep.join(p for p in sys.path if p))
    result = subprocess.run([sys.executable, str(probe), str(api._callback_bootstrap_mod().__file__), str(manifest_path), str(factory)],
                            env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'registered-only exact inventory OK' in result.stdout
