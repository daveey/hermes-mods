"""Hold one of the host's shared test slots while a heavy smoke runs.

The core-lease smoke starts headless Chrome, a proxy and a core-tools subprocess. Agents started it freely,
beside capped pytest runs, so on a laptop with ``tests.host_slots`` set the smoke ran outside the cap and
added bursts of processes to the load average. The smoke now takes one slot of the same pool
(hermes-agent ``scripts/run_tests_host_slots.py``), so smoke + tests together stay under the host cap.
"""
import importlib.util
from pathlib import Path


def acquire(core, on_wait=None):
    """Block until a host slot is held; return an fd to close when done (None: no cap applies / no slot module)."""
    path = Path(core) / 'scripts' / 'run_tests_host_slots.py'
    if not path.is_file():
        return None
    spec = importlib.util.spec_from_file_location('smoke_host_slots', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.acquire_host_slot(on_wait)


def release(fd):
    if fd is not None:
        import os
        try:
            os.close(fd)
        except OSError:
            pass
