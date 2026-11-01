"""Exercise guard delivery through real pytest teardown, including xdist workers."""
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


@pytest.mark.parametrize('workers', [0, 2])
@pytest.mark.parametrize('leak', [False, True])
def test_late_swallowed_violation_fails_pytest(tmp_path, workers, leak):
    suite = tmp_path / 'repo/plugins/coach/tests'
    suite.mkdir(parents=True)
    for name in ('conftest.py', 'home_guard.py', 'gateway_reaper.py'):
        shutil.copyfile(Path(__file__).with_name(name), suite / name)
    if leak:
        with (suite / 'conftest.py').open('a') as out:
            out.write('''
@pytest.fixture(scope='session', autouse=True)
def _late_leak():
    yield
    # Session fixtures tear down after the function audit check. The guard must
    # reject before opening the real file AND propagate the caught violation.
    try:
        (_LIVE_HOME / 'state.db').read_bytes()
    except AssertionError:
        pass
''')
    (suite / 'test_probe.py').write_text('def test_ok():\n    assert True\n')
    args = [sys.executable, '-m', 'pytest', '-q', '-p', 'no:cacheprovider', '--confcutdir='+str(suite)]
    if workers:
        args += ['-p', 'xdist.plugin', '-n', str(workers)]
    env = dict(os.environ, PYTEST_DISABLE_PLUGIN_AUTOLOAD='1')
    result = subprocess.run([*args, str(suite)], env=env, capture_output=True, text=True, timeout=120)
    text = result.stdout + result.stderr
    assert result.returncode == (1 if leak else 0), text
    if leak:
        assert 'coach late/collection isolation violations' in text
        assert 'state.db' in text
    else:
        assert '1 passed' in text
