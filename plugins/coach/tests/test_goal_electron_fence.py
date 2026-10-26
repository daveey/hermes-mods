"""Test-only POSIX process/network fence; no production process is targeted."""
import os
from pathlib import Path
import subprocess
import sys
import pytest


@pytest.mark.skipif(sys.platform != 'linux', reason='Linux Electron fixture fence')
def test_electron_fence_rejects_foreign_signals_and_external_connect(tmp_path):
    library = tmp_path / 'fence.so'
    subprocess.run(['cc', '-shared', '-fPIC', '-O2',
        str(Path(__file__).with_name('electron_loopback_fence.c')),
        '-o', str(library), '-ldl'], check=True)
    foreign_env = dict(os.environ, HERMES_HOME=str(tmp_path / 'foreign'))
    foreign = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'], env=foreign_env)
    try:
        probe = (
            'import errno, os, socket\n'
            'try: os.kill(' + str(foreign.pid) + ', 15)\n'
            'except PermissionError: pass\n'
            'else: raise AssertionError("foreign signal escaped")\n'
            's = socket.socket(); assert s.connect_ex(("192.0.2.1", 443)) == errno.EACCES\n'
        )
        env = dict(os.environ, HERMES_HOME=str(tmp_path / 'owned'), LD_PRELOAD=str(library))
        subprocess.run([sys.executable, '-c', probe], env=env, check=True, timeout=10)
        assert foreign.poll() is None
    finally:
        foreign.terminate()
        foreign.wait(timeout=10)
