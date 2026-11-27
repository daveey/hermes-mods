"""Review regressions: non-exec broker pipe ownership and nonregular pins."""
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def test_nonexec_wrapper_timeout_does_not_drain_grandchild_pipes(tmp_path, monkeypatch):
    from providers.runpod import BrokerCollector
    real = subprocess.Popen
    children = []
    worker = tmp_path / 'worker.py'
    worker.write_text('import time; time.sleep(3)')

    class Wrapped(BrokerCollector):
        provider = 'runpod'

        def worker_argv(self):
            # Shell deliberately waits rather than execs; its child retains the pipes.
            return ['/bin/sh', '-c', '"$@"; :', 'broker', sys.executable, str(worker)]

    class Spy(real):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            children.append(self)

        def communicate(self, timeout=None):
            return super().communicate(timeout=0.2 if timeout == 70 else timeout)

    monkeypatch.setattr(subprocess, 'Popen', Spy)
    start = time.monotonic()
    out = Wrapped().broker_collect()
    elapsed = time.monotonic() - start
    assert out['status'] == 'error'
    assert children[0].poll() is not None
    assert elapsed < 1.5, f'collector drained orphan pipes for {elapsed:.2f}s'
    assert children[0].stdout.closed and children[0].stderr.closed


def test_pinned_systemexit_is_a_provider_error(tmp_path):
    from providers import load_extra_providers
    source = b'raise SystemExit("bad provider")'
    (tmp_path / 'exits.py').write_bytes(source)
    collectors, _ = load_extra_providers(tmp_path, pins={'exits.py': hashlib.sha256(source).hexdigest()})
    assert collectors['exits']()['status'] == 'error'


@pytest.mark.skipif(not hasattr(os, 'mkfifo'), reason='requires FIFO')
def test_pinned_fifo_is_rejected_without_blocking(tmp_path):
    fifo = tmp_path / 'fifo.py'
    os.mkfifo(fifo)
    plugin = Path(__file__).resolve().parents[1]
    code = ('import sys; sys.path.insert(0, sys.argv[1]); '
            'from providers import load_extra_providers; '
            'c, _ = load_extra_providers(sys.argv[2], pins={"fifo.py": sys.argv[3]}); '
            'assert c["fifo"]()["status"] == "error"')
    proc = subprocess.run([sys.executable, '-I', '-c', code, str(plugin), str(tmp_path),
                           hashlib.sha256(b'').hexdigest()], capture_output=True, text=True, timeout=1.5)
    assert proc.returncode == 0, proc.stderr
