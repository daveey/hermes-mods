"""The coach timeout must be active even without caller-supplied pytest options."""
import os
from pathlib import Path
import subprocess
import sys
import pytest


def test_coach_has_a_bounded_default_test_timeout(tmp_path):
    suite = tmp_path / 'nested'
    suite.mkdir()
    config = Path(__file__).parents[1] / 'pytest.ini'
    (suite / 'pytest.ini').write_text(config.read_text() if config.exists() else '[pytest]\n')
    (suite / 'test_probe.py').write_text('def test_guard_is_active(pytestconfig):\n'
        '    assert 0 < float(pytestconfig.getini("timeout") or 0) <= 120, "coach has no per-test deadline"\n')
    env = dict(os.environ, PYTEST_ADDOPTS='')
    result = subprocess.run([sys.executable, '-m', 'pytest', '-q', '-p', 'no:cacheprovider'],
        cwd=suite, env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=15)
    assert result.returncode == 0, result.stdout


def test_coach_stops_before_later_work_hides_first_failure(tmp_path):
    suite = tmp_path / 'failfast'
    suite.mkdir()
    (suite / 'pytest.ini').write_text((Path(__file__).parents[1] / 'pytest.ini').read_text())
    (suite / 'test_probe.py').write_text(
        'from pathlib import Path\n'
        'def test_01_first_failure():\n'
        '    assert False, "first_failure_must_remain_visible"\n'
        'def test_02_later_work():\n'
        '    Path("later-work").write_text("ran")\n')
    result = subprocess.run([sys.executable, '-m', 'pytest', '-q', '-p', 'no:cacheprovider'],
        cwd=suite, env=dict(os.environ, PYTEST_ADDOPTS=''), text=True,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=15)
    assert result.returncode == 1, result.stdout
    assert 'test_01_first_failure' in result.stdout, result.stdout
    assert 'first_failure_must_remain_visible' in result.stdout, result.stdout
    assert not (suite / 'later-work').exists(), 'later tests can consume the suite budget and hide the first failure'


@pytest.mark.parametrize('phase', ['setup', 'call', 'teardown'])
def test_coach_timeout_names_blocked_phase(tmp_path, phase):
    suite = tmp_path / 'blocked'
    suite.mkdir()
    (suite / 'pytest.ini').write_text((Path(__file__).parents[1] / 'pytest.ini').read_text())
    before = 'time.sleep(1)' if phase == 'setup' else 'pass'
    during = 'time.sleep(1)' if phase == 'call' else 'pass'
    after = 'time.sleep(1)' if phase == 'teardown' else 'pass'
    (suite / 'test_blocked.py').write_text(
        'import time, pytest\n'
        '@pytest.fixture\n'
        'def boundary():\n'
        f'    {before}\n    yield\n    {after}\n'
        f'def test_blocked_{phase}(boundary):\n    {during}\n')
    result = subprocess.run([sys.executable, '-m', 'pytest', '-q', '-p', 'no:cacheprovider',
                             '-o', 'timeout=0.2'], cwd=suite, env=dict(os.environ, PYTEST_ADDOPTS=''),
        text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=15)
    assert result.returncode == 1, result.stdout
    assert 'Timeout' in result.stdout and f'test_blocked_{phase}' in result.stdout, result.stdout
