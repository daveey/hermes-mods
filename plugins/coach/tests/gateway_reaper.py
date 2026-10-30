"""Stop gateway daemons that test subprocesses auto-started under a test home.

Core clients (tui_gateway, `hermes chat`) start an unsupervised
`gateway run --idle-exit` daemon that detaches from the child (reparented to
init/systemd), so terminating the child leaves it running on a tmp home pytest
later deletes. It then holds deleted state.db WAL/SHM sidecars until its idle
exit, which stranded_sqlite_watch reports as a stranded process.
"""
import json
import os
from pathlib import Path
import signal
import time


def _under(path, root):
    try:
        return Path(path).resolve().is_relative_to(Path(root).resolve())
    except (OSError, ValueError):
        return False


def _alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:
        return Path(f'/proc/{pid}/stat').read_text().rsplit(') ', 1)[-1][:1] != 'Z'
    except OSError:
        return True


def gateway_pids_under(root):
    """Pids of gateway processes whose HERMES_HOME is inside *root*."""
    pids = set()
    proc = Path('/proc')
    if proc.is_dir():
        for entry in proc.iterdir():
            if not entry.name.isdigit() or int(entry.name) == os.getpid():
                continue
            try:
                argv = (entry / 'cmdline').read_bytes().split(b'\0')
                if b'gateway' not in argv:
                    continue
                env = (entry / 'environ').read_bytes().split(b'\0')
            except OSError:
                continue
            home = next((v[len(b'HERMES_HOME='):] for v in env if v.startswith(b'HERMES_HOME=')), None)
            if home and _under(os.fsdecode(home), root):
                pids.add(int(entry.name))
        return pids
    # No /proc (macOS): fall back to the pid records gateways write in their home.
    for record in Path(root).rglob('gateway.pid'):
        try:
            raw = record.read_text().strip()
            pid = int(json.loads(raw)['pid'] if raw.startswith('{') else raw)
        except (OSError, ValueError, KeyError, TypeError):
            continue
        if pid != os.getpid():
            pids.add(pid)
    return pids


def stop_gateways_under(root, timeout=15):
    """SIGTERM (then SIGKILL) every gateway under *root*; returns the pids found."""
    pids = gateway_pids_under(root)
    for sig in (signal.SIGTERM, signal.SIGKILL):
        live = [p for p in pids if _alive(p)]
        if not live:
            break
        for pid in live:
            try:
                os.kill(pid, sig)
            except ProcessLookupError:
                pass
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and any(_alive(p) for p in live):
            time.sleep(0.2)
    return pids
