"""CI receipts must precede the final summary, which an outer suite kill loses."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


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
