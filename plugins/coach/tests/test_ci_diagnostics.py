"""CI receipts must precede the final summary, which an outer suite kill loses."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


def test_worker_crash_preserves_existing_violation_receipts():
    import conftest
    from types import SimpleNamespace
    config = SimpleNamespace(_coach_worker_violations=['earlier violation'])
    # Fatal native exit has no workeroutput at all, unlike graceful shutdown.
    conftest.pytest_testnodedown(SimpleNamespace(config=config), 'fatal watchdog exit')
    assert config._coach_worker_violations == ['earlier violation']
    conftest.pytest_testnodedown(SimpleNamespace(config=config, workeroutput={
        'coach_isolation_violations': ['later violation']}), None)
    assert config._coach_worker_violations == ['earlier violation', 'later violation']


@pytest.mark.parametrize('global_timeout, marker, expected', [
    (2, None, 2),       # plugin has already resolved CLI > env > ini
    (0, None, 0),       # explicit --timeout=0 disables the native timer too
    (2, ((.4,), {}), .4),
    (2, ((), {'timeout': .6}), .6),
    (2, ((0,), {}), 0),
    (None, None, 120),
])
def test_native_watchdog_uses_effective_deadline(global_timeout, marker, expected):
    import conftest
    from types import SimpleNamespace
    config = SimpleNamespace(_env_timeout=global_timeout)
    mark = SimpleNamespace(args=marker[0], kwargs=marker[1]) if marker else None
    item = SimpleNamespace(get_closest_marker=lambda name: mark)
    assert conftest._watchdog_seconds(config, item) == expected


def test_ci_receipts_name_runtime_and_slow_test_before_summary(tmp_path):
    suite = tmp_path / 'suite'
    suite.mkdir()
    for name in ('conftest.py', 'home_guard.py', 'gateway_reaper.py', 'collection_home.py'):
        shutil.copyfile(Path(__file__).with_name(name), suite / name)
    (suite / 'test_probe.py').write_text('import time\ndef test_slow(): time.sleep(1.05)\n')
    env = dict(os.environ, PYTEST_DISABLE_PLUGIN_AUTOLOAD='1')
    env.pop('PYTEST_ADDOPTS', None)
    child = subprocess.run([sys.executable, '-m', 'pytest', '-q', '-p', 'no:cacheprovider', str(suite)],
                           cwd=suite, env=env, capture_output=True, text=True, timeout=30)
    assert child.returncode == 0, child.stdout + child.stderr
    runtime = [line for line in child.stdout.splitlines() if line.startswith('COACH_RUNTIME ')]
    assert len(runtime) == 1, child.stdout
    receipt = json.loads(runtime[0].split(' ', 1)[1])
    assert receipt['cpu_count'] >= 1
    assert receipt['worker_count'] == 0
    assert receipt['python']
    assert 'COACH_PHASE ' in child.stdout, child.stdout
    assert 'test_probe.py::test_slow' in child.stdout
    assert child.stdout.index('COACH_PHASE ') < child.stdout.index('1 passed')
    assert child.stdout.index('COACH_START ') < child.stdout.index('COACH_PHASE ')


@pytest.mark.parametrize('boundary', ['call', 'sessionfinish', 'atexit'])
def test_fatal_watchdog_survives_capture_and_cancelled_alarm(tmp_path, boundary):
    suite = tmp_path / 'blocked'
    suite.mkdir()
    for name in ('conftest.py', 'home_guard.py', 'gateway_reaper.py', 'collection_home.py'):
        shutil.copyfile(Path(__file__).with_name(name), suite / name)
    # Disable pytest-timeout only in this child, to exercise the independent C
    # watchdog against code that cancels SIGALRM or never returns to Python.
    with (suite / 'conftest.py').open('a') as config:
        config.write('\ndef pytest_addoption(parser):\n'
                     '    parser.addini("timeout", "watchdog test deadline", default="0.4")\n')
        if boundary == 'sessionfinish':
            config.write('\n@pytest.hookimpl(tryfirst=True)\n'
                         'def pytest_sessionfinish(session, exitstatus):\n'
                         '    import threading\n'
                         '    threading.Event().wait()\n')
    (suite / 'test_blocked.py').write_text(
        'import atexit, signal, threading\n'
        'def blocked_boundary():\n'
        '    signal.alarm(0)\n'
        '    threading.Event().wait()\n'
        'def test_named_hang():\n' +
        ('    blocked_boundary()\n' if boundary == 'call' else
         '    atexit.register(blocked_boundary)\n' if boundary == 'atexit' else '    pass\n'))
    env = dict(os.environ, PYTEST_DISABLE_PLUGIN_AUTOLOAD='1', PYTEST_ADDOPTS='')
    try:
        child = subprocess.run([sys.executable, '-m', 'pytest', '-q', '-p', 'no:cacheprovider'],
                               cwd=suite, env=env, capture_output=True, text=True, timeout=8)
    except subprocess.TimeoutExpired as exc:
        pytest.fail(f'watchdog did not bound {boundary}; output={exc.stdout!r} stderr={exc.stderr!r}')
    output = child.stdout + child.stderr
    assert child.returncode != 0, output
    assert 'Timeout' in output and 'threading.py' in output, output
    assert 'COACH_START ' in output and 'test_named_hang' in output, output
    if boundary == 'atexit':
        assert '1 passed' in output, output
        assert 'blocked_boundary' in output, output
