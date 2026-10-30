"""gateway_reaper stops only gateways homed under the given test root."""
import os
from pathlib import Path
import subprocess
import sys

import pytest

from gateway_reaper import gateway_pids_under, stop_gateways_under

pytestmark = pytest.mark.skipif(not Path('/proc').is_dir(), reason='needs /proc')

# A stand-in whose argv carries the `gateway` token, like `hermes_cli.main gateway run`.
SLEEPER = [sys.executable, '-c', 'import time; time.sleep(120)', 'gateway', 'run']


def spawn(home, argv=SLEEPER):
    env = dict(os.environ, HERMES_HOME=str(home))
    # start_new_session: like the real daemon, not tied to this test's process group.
    return subprocess.Popen(argv, env=env, start_new_session=True)


def test_reaps_gateway_under_root_and_spares_others(tmp_path, tmp_path_factory):
    inside = spawn(tmp_path / 'home')
    outside_root = tmp_path_factory.mktemp('other')
    outside = spawn(outside_root / 'home')
    not_gateway = spawn(tmp_path / 'home', [sys.executable, '-c', 'import time; time.sleep(120)'])
    try:
        assert gateway_pids_under(tmp_path) == {inside.pid}
        assert stop_gateways_under(tmp_path, timeout=10) == {inside.pid}
        assert inside.wait(timeout=10) is not None
        assert outside.poll() is None and not_gateway.poll() is None
    finally:
        for p in (inside, outside, not_gateway):
            if p.poll() is None:
                p.kill()
                p.wait(timeout=10)


def test_kills_gateway_that_ignores_sigterm(tmp_path):
    stubborn = spawn(tmp_path / 'home', [sys.executable, '-c',
                     'import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); '
                     'print("ready", flush=True); time.sleep(120)', 'gateway'])
    try:
        import time
        time.sleep(1.0)  # let it install the SIG_IGN handler
        assert stop_gateways_under(tmp_path, timeout=2) == {stubborn.pid}
        assert stubborn.wait(timeout=10) == -9
    finally:
        if stubborn.poll() is None:
            stubborn.kill()
            stubborn.wait(timeout=10)
