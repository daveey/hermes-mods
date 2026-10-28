"""Transport children must inherit dependency paths, not worker identity."""
import os
import subprocess
import sys

from test_goal_disposition_transport import isolated_env


def test_child_can_import_managed_dependency_path(tmp_path, monkeypatch):
    packages = tmp_path / 'managed' / 'site-packages'
    packages.mkdir(parents=True)
    (packages / 'transport_dependency_probe.py').write_text('VALUE = "isolated-dependency"\n')
    monkeypatch.syspath_prepend(str(packages))
    monkeypatch.setenv('HERMES_DELEGATED_CHILD_CONTEXT', '1')
    monkeypatch.setenv('HERMES_KANBAN_TASK', 'outer-worker')
    env = isolated_env(tmp_path / 'home')
    assert str(packages) in env['PYTHONPATH'].split(os.pathsep)
    assert env['HERMES_DISABLE_LAZY_INSTALLS'] == '1'
    assert 'HERMES_DELEGATED_CHILD_CONTEXT' not in env
    assert 'HERMES_KANBAN_TASK' not in env
    result = subprocess.run([sys.executable, '-c',
                             'import transport_dependency_probe as p; print(p.VALUE)'],
                            env=env, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == 'isolated-dependency'
