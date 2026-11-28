"""The dormant panel may register waiting callbacks without activating closeout."""
import os
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('package', [False, True], ids=['pytest-flat', 'package'])
def test_import_and_waiting_registration_are_dormant(package, tmp_path):
    pytest.importorskip('hermes_cli.kanban_session_activity')
    code = textwrap.dedent('''
        import importlib.abc
        import importlib.util
        from pathlib import Path
        import sys
        import threading
        from hermes_cli import kanban_session_activity as activity
        from hermes_cli import kanban_annotation_host as host

        root = Path(sys.argv[1])
        package = sys.argv[2] == 'True'
        forbidden = {'owner_wake', 'routing', 'coach', 'gateway', 'tui_gateway', 'run_agent'}

        class DormantOnly(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path=None, target=None):
                assert not (set(fullname.split('.')) & forbidden), 'forbidden import: ' + fullname

        sys.meta_path.insert(0, DormantOnly())
        original_spec = importlib.util.spec_from_file_location
        def guarded_spec(name, location, *args, **kwargs):
            path = Path(location)
            assert path.stem not in forbidden and 'coach' not in path.parts, 'forbidden file import: ' + str(path)
            return original_spec(name, location, *args, **kwargs)
        importlib.util.spec_from_file_location = guarded_spec
        def no_thread(*args, **kwargs):
            raise AssertionError('thread started during dormant registration')
        threading.Thread.start = no_thread
        before = set(sys.modules)
        spec = original_spec('closeout_registration_probe', root / '__init__.py')
        plugin = importlib.util.module_from_spec(spec)
        if package:
            sys.modules[spec.name] = plugin
        else:
            plugin.__package__ = ''
        spec.loader.exec_module(plugin)
        assert plugin.core.__file__.endswith('core.py')
        assert plugin.wi.__file__.endswith('waiting_inputs.py')

        forbidden_calls = {'register', 'start_thread', 'waiting_source', 'waiting_inputs',
                           'post_llm_call', 'pre_llm_call', 'pre_final_gate'}
        def no_activation(frame, event, arg):
            if event == 'call' and frame.f_globals is plugin.__dict__:
                assert frame.f_code.co_name not in forbidden_calls, 'callback invoked: ' + frame.f_code.co_name
        sys.setprofile(no_activation)
        try:
            plugin.register_waiting_with(activity)
            assert activity._waiting_sources['chat-closeout'] is plugin.waiting_source
            assert host._declared['chat-closeout'][0] is plugin.waiting_source
            assert host._declared['chat-closeout'][1] is plugin.waiting_inputs
            # The legacy wrapper uses the same public seam and identical callbacks.
            calls = []
            original_registrar = plugin.register_waiting_with
            def registrar(target):
                calls.append(target)
                return original_registrar(target)
            plugin.register_waiting_with = registrar
            plugin._register_waiting_source()
            assert calls == [activity]
            assert activity._waiting_sources['chat-closeout'] is plugin.waiting_source
            assert host._declared['chat-closeout'][1] is plugin.waiting_inputs
        finally:
            sys.setprofile(None)
        imported = set(sys.modules) - before
        assert not any(set(name.split('.')) & forbidden for name in imported), sorted(imported)
        assert not any(name.startswith('chat_closeout_owner_wake') or name.startswith('chat_closeout_routing')
                       for name in imported), sorted(imported)
        print('dormant registration: callback and inputs identity verified')
    ''')
    env = dict(os.environ, HERMES_HOME=str(tmp_path), PYTHONDONTWRITEBYTECODE='1')
    result = subprocess.run([sys.executable, '-B', '-c', code, str(ROOT), str(package)],
                            env=env, text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'callback and inputs identity verified' in result.stdout


@pytest.mark.parametrize('package', [False, True], ids=['pytest-flat', 'package'])
@pytest.mark.parametrize('name, attribute', [('owner_wake', 'tick'), ('routing', 'moved_owner')])
def test_lazy_dependencies_preserve_module_patches_and_retry_failed_imports(package, name, attribute, tmp_path):
    code = textwrap.dedent('''
        import importlib.machinery
        import importlib.util
        from pathlib import Path
        import sys

        root, package, name, attribute = Path(sys.argv[1]), sys.argv[2] == 'True', sys.argv[3], sys.argv[4]
        spec = importlib.util.spec_from_file_location('closeout_lazy_probe', root / '__init__.py')
        plugin = importlib.util.module_from_spec(spec)
        if package:
            sys.modules[spec.name] = plugin
        else:
            plugin.__package__ = ''
        spec.loader.exec_module(plugin)
        module_name = spec.name + '.' + name if package else 'chat_closeout_' + name
        assert module_name not in sys.modules, 'wake dependency was eagerly loaded'
        proxy = getattr(plugin, name)
        original_exec = importlib.machinery.SourceFileLoader.exec_module
        attempts = []
        def fail_once(loader, module):
            if module.__name__ == module_name:
                attempts.append(module)
                assert module_name not in sys.modules or package
                raise RuntimeError('injected dependency import failure')
            return original_exec(loader, module)
        importlib.machinery.SourceFileLoader.exec_module = fail_once
        try:
            try:
                getattr(proxy, attribute)
            except RuntimeError as exc:
                assert str(exc) == 'injected dependency import failure'
            else:
                raise AssertionError('failed dependency import was swallowed')
            assert len(attempts) == 1
            assert module_name not in sys.modules, 'partially initialized dependency was published'
        finally:
            importlib.machinery.SourceFileLoader.exec_module = original_exec
        original = getattr(proxy, attribute)
        actual = sys.modules[module_name]
        assert original is getattr(actual, attribute)
        sentinel = object()
        setattr(proxy, attribute, sentinel)
        assert getattr(actual, attribute) is sentinel
        assert getattr(getattr(plugin, name), attribute) is sentinel
        setattr(proxy, attribute, original)
        assert getattr(actual, attribute) is original
        setattr(proxy, '_registration_test_marker', sentinel)
        assert actual._registration_test_marker is sentinel
        delattr(proxy, '_registration_test_marker')
        assert not hasattr(actual, '_registration_test_marker')
    ''')
    env = dict(os.environ, HERMES_HOME=str(tmp_path), PYTHONDONTWRITEBYTECODE='1')
    result = subprocess.run([sys.executable, '-B', '-c', code, str(ROOT), str(package), name, attribute],
                            env=env, text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
