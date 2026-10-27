"""Runtime packaging tests: no live config, credentials, or service changes."""
import builtins
import json
from pathlib import Path
import subprocess
import sys
from unittest.mock import Mock

import pytest
from plugins.runners.scripts import install_service as installer
from plugins.runners.scripts import run_collector as collector


def test_runtime_interpreter_discovery():
    runner = Mock(return_value=subprocess.CompletedProcess([], 0, json.dumps([sys.executable, '-I', '-c', 'trusted launcher']), ''))
    assert installer.runtime_python('/example/hermes', runner=runner) == sys.executable
    assert runner.call_args.args[0] == ['/example/hermes', '--print-runtime-command']
    assert runner.call_args.kwargs['timeout'] == 30


@pytest.mark.parametrize('output', ['{}', '[]', '["relative"]', '[1]', 'not json'])
def test_runtime_discovery_fails_closed(output):
    runner = Mock(return_value=subprocess.CompletedProcess([], 0, output, ''))
    with pytest.raises(ValueError, match='invalid_hermes_runtime'):
        installer.runtime_python('/example/hermes', runner=runner)


def test_core_dependency_error_is_safe(tmp_path, monkeypatch):
    for name in ('hermes_state.py', 'tui_gateway/session_start_requests.py', 'hermes_cli/profiles.py'):
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.touch()
    original = builtins.__import__
    def missing(name, *args, **kwargs):
        if name == 'tui_gateway':
            raise ModuleNotFoundError('SECRET arbitrary error', name='ruamel')
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, '__import__', missing)
    with pytest.raises(RuntimeError) as caught:
        collector.configure_core(tmp_path)
    assert str(caught.value) == 'repair_core_unavailable:ModuleNotFoundError:ruamel'


def test_runtime_installer_uses_isolation_and_bootstrap(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(installer, 'install', lambda platform, command: calls.append(command))
    monkeypatch.setattr(installer, 'runtime_python', lambda launcher: sys.executable)
    installer.main(['--install', '--hermes-launcher', '/example/hermes', '--core-root', str(tmp_path),
        '--config', str(tmp_path / 'config'), '--runtime', str(tmp_path / 'runtime'),
        '--state-dir', str(tmp_path / 'state'), '--owner-gateway-id', 'example',
        '--owner-profile', 'default', '--owner-epoch', '1'])
    assert calls[0][:2] == [sys.executable, '-I']
    assert Path(calls[0][2]).name == 'runtime_collector.py'
    assert calls[0][-2:] == ['--core-root', str(tmp_path)]


def test_runtime_bootstrap_without_core_fails_before_collection(tmp_path):
    script = Path(collector.__file__).with_name('runtime_collector.py')
    result = subprocess.run([sys.executable, '-I', str(script), '--core-root', str(tmp_path), '--check-core'], capture_output=True, text=True)
    assert result.returncode == 1
    assert result.stderr.strip() == 'repair_core_unavailable'
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize('exception', ['RuntimeError', 'SystemExit'])
def test_activation_failure_never_leaks_output_or_runs_full_bootstrap(tmp_path, exception):
    (tmp_path / 'pm').mkdir()
    (tmp_path / 'pm/__init__.py').touch()
    (tmp_path / 'pm/environments.py').write_text(
        "import sys\ndef activate_dependencies(root):\n"
        "    print('SECRET', file=sys.stderr)\n    print('SECRET')\n"
        f"    raise {exception}('SECRET')\n")
    (tmp_path / 'hermes_bootstrap.py').write_text("raise AssertionError('full bootstrap must not run')")
    script = Path(collector.__file__).with_name('runtime_collector.py')
    result = subprocess.run([sys.executable, '-I', str(script), '--core-root', str(tmp_path), '--check-core'], capture_output=True, text=True)
    assert result.returncode == 1
    assert result.stderr.strip() == 'repair_core_unavailable'
    assert result.stdout == ''
