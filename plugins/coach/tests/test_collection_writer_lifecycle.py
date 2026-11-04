"""A gateway auto-started under the session's pytest root must stop before collection cleanup."""
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

pytestmark = pytest.mark.skipif(not Path('/proc').is_dir(), reason='needs /proc')


def _alive(pid):
    try:
        return Path(f'/proc/{pid}/stat').read_text().rsplit(') ', 1)[-1][:1] != 'Z'
    except OSError:
        return False


def test_session_stops_gateway_homed_at_pytest_root(tmp_path):
    suite = tmp_path / 'suite'
    suite.mkdir()
    for name in ('conftest.py', 'gateway_reaper.py', 'home_guard.py'):
        shutil.copyfile(Path(__file__).with_name(name), suite / name)
    pidfile = tmp_path / 'pid'
    stopped = tmp_path / 'stopped'
    homefile = tmp_path / 'home'
    # A real gateway writes shutdown state under its home. Record outside that home
    # whether it still existed when SIGTERM arrived, and write inside it without
    # recreating it: cleanup-before-stop must fail rather than silently hide the race.
    writer = f'''
import os, signal, sys, time
from pathlib import Path
home = Path(os.environ['HERMES_HOME'])
def shutdown(*_):
    (home / 'shutdown-state').write_text('stopped')
    Path({str(stopped)!r}).write_text('home existed at shutdown')
    sys.exit(0)
signal.signal(signal.SIGTERM, shutdown)
print('ready', flush=True)
while True:
    time.sleep(1)
'''
    # Home is the pytest basetemp itself (as in the failing run), not a per-test tmp_path,
    # so the per-test reaper never covers it. Reparented like the real detached daemon.
    (suite / 'test_probe.py').write_text(f'''
import os, subprocess, sys

def test_spawn(tmp_path_factory):
    home = str(tmp_path_factory.getbasetemp())
    env = dict(os.environ, HERMES_HOME=home)
    p = subprocess.Popen([sys.executable, '-c', {writer!r}, 'gateway', 'run'],
                         env=env, start_new_session=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    assert p.stdout.readline().strip() == b'ready'
    open({str(pidfile)!r}, 'w').write(str(p.pid))
    open({str(homefile)!r}, 'w').write(home)
''')
    env = dict(os.environ, PYTEST_DISABLE_PLUGIN_AUTOLOAD='1')
    result = subprocess.run([sys.executable, '-m', 'pytest', '-q', '-p', 'no:cacheprovider', str(suite)],
                            env=env, capture_output=True, text=True, timeout=180)
    pid = int(pidfile.read_text())
    try:
        assert result.returncode == 0, result.stdout + result.stderr
        assert 'Traceback' not in result.stderr
        assert not _alive(pid), 'gateway outlived the pytest session and its collection home'
        assert stopped.read_text() == 'home existed at shutdown'
        assert not Path(homefile.read_text()).exists(), 'collection home was not cleaned'
    finally:
        if _alive(pid):
            os.kill(pid, 9)


@pytest.mark.skipif(importlib.util.find_spec('hermes_cli') is None, reason='needs Hermes core')
def test_core_writer_finishes_before_test_home_restore(tmp_path):
    suite = tmp_path / 'suite'
    suite.mkdir()
    for name in ('conftest.py', 'gateway_reaper.py', 'home_guard.py'):
        shutil.copyfile(Path(__file__).with_name(name), suite / name)
    (suite / 'test_probe.py').write_text('''
import threading
import time
from hermes_cli import kanban_annotation_refresh as refresh
from hermes_cli.kanban_session_activity_origin_snapshot import remote_snapshot

finished = threading.Event()
written = []

def test_01_start(tmp_path):
    written.append(tmp_path)
    lock = threading.Lock()
    lock.acquire()
    def write():
        time.sleep(.2)
        remote_snapshot([])
        finished.set()
        return ()
    assert refresh.refresh_bounded(write, lock, None, 0) is None

def test_02_drained():
    assert finished.is_set(), 'core writer outlived test-owned home/environment'
    assert (written[0] / 'origin-work.db').is_file()
''')
    env = dict(os.environ, PYTEST_DISABLE_PLUGIN_AUTOLOAD='1')
    result = subprocess.run([sys.executable, '-m', 'pytest', '-q', '-p', 'no:cacheprovider', str(suite)],
                            env=env, capture_output=True, text=True, timeout=180)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'Traceback' not in result.stderr


@pytest.mark.parametrize('name', ['kanban-annotation-refresh', 'kanban-remote-links', 'kanban-annotation-prewarm'])
def test_collection_writer_is_joined_before_home_cleanup(tmp_path, name):
    suite = tmp_path / 'suite'
    suite.mkdir()
    for filename in ('conftest.py', 'gateway_reaper.py', 'home_guard.py'):
        shutil.copyfile(Path(__file__).with_name(filename), suite / filename)
    receipt = tmp_path / 'receipt'
    homefile = tmp_path / 'home'
    # Start outside the per-test fixture, as collection imports can do. Release at
    # sessionfinish; joining only per-test threads cannot satisfy this contract.
    (suite / 'lifecycle_probe.py').write_text(f'''
import os, threading, time
from pathlib import Path
import pytest
release = threading.Event()
def pytest_collection_finish(session):
    home = Path(os.environ['HERMES_HOME'])
    Path({str(homefile)!r}).write_text(str(home))
    def write():
        assert release.wait(30)
        time.sleep(.2)
        (home / 'origin-work.db').write_text('snapshot')
        Path({str(receipt)!r}).write_text('written before cleanup')
    threading.Thread(target=write, daemon=True, name={name!r}).start()
@pytest.hookimpl(tryfirst=True)
def pytest_sessionfinish(session, exitstatus):
    release.set()
''')
    (suite / 'test_probe.py').write_text('def test_ok(): pass\n')
    env = dict(os.environ, PYTEST_DISABLE_PLUGIN_AUTOLOAD='1')
    env['PYTHONPATH'] = os.pathsep.join([str(suite), env.get('PYTHONPATH', '')])
    result = subprocess.run([sys.executable, '-m', 'pytest', '-q', '-p', 'no:cacheprovider',
                             '-p', 'lifecycle_probe', str(suite)],
                            env=env, capture_output=True, text=True, timeout=180)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'Traceback' not in result.stderr
    assert receipt.read_text() == 'written before cleanup'
    assert not Path(homefile.read_text()).exists()

