"""Owner-verified browser-harness IPC cleanup, without process-name killing."""
import json
import os
from pathlib import Path
import socket
import stat
import time

from chrome_agent_process import fingerprint, is_zombie


def exchange(path, payload, deadline=None, identity=None):
    deadline = deadline if deadline is not None else time.monotonic() + 2
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(_remaining(deadline))
        client.connect(path)
        if identity:
            # Check AFTER connect, BEFORE sending authority. A replacement between
            # ping and shutdown must not receive the old generation's command.
            info = Path(path).lstat()
            if (info.st_ino != identity['inode'] or not stat.S_ISSOCK(info.st_mode)
                    or (identity.get('socket_stamp') is not None
                        and [info.st_dev, info.st_ino, info.st_ctime_ns] != identity['socket_stamp'])
                    or info.st_uid != os.getuid() or info.st_mode & 0o077
                    or _fingerprint(identity['pid'], deadline) != identity['started']):
                raise ValueError('harness endpoint changed')
            if hasattr(socket, 'SO_PEERCRED'):
                import struct
                pid, uid, _ = struct.unpack('3i', client.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
                if pid != identity['pid'] or uid != os.getuid():
                    raise ValueError('harness peer changed')
        client.sendall(json.dumps(payload).encode() + b'\n')
        data = b''
        while not data.endswith(b'\n') and len(data) <= 65536:
            client.settimeout(_remaining(deadline))
            part = client.recv(4096)
            if not part:
                break
            data += part
        result = json.loads(data)
        if not isinstance(result, dict):
            raise ValueError('invalid harness IPC reply')
        return result


def _remaining(deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError('daemon retirement deadline reached')
    return min(2, remaining)


def _fingerprint(pid, deadline):
    return fingerprint(pid, timeout=_remaining(deadline)) if deadline is not None else fingerprint(pid)


def _retired(identity, deadline):
    current = _fingerprint(identity['pid'], deadline)
    if current is not None:
        return current != identity['started']
    # A failed/timeout ps probe is NOT proof of exit. Zombies are retired, but
    # otherwise require the OS to report that the exact PID no longer exists.
    if is_zombie(identity['pid'], timeout=_remaining(deadline)):
        return True
    try:
        os.kill(identity['pid'], 0)
    except ProcessLookupError:
        return True
    except (OSError, IndexError):
        return False
    return False


def inspect(path, capability, owned_targets, deadline=None):
    """Pin a registered private capability socket to its live harness process."""
    path = Path(path)
    if not path.is_absolute() or path.name != 'bu-lease-' + capability + '.sock':
        return None
    try:
        info = path.lstat()
        if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            return None
        ping = exchange(str(path), {'meta': 'ping'}, deadline)
        pid = ping.get('pid')
        if not ping.get('pong') or type(pid) is not int or pid <= 0:
            return None
        started = _fingerprint(pid, deadline)
        if started is None:
            return None
        identity = dict(path=str(path), pid=pid, started=started, inode=info.st_ino,
                        socket_stamp=[info.st_dev, info.st_ino, info.st_ctime_ns])
        current = exchange(str(path), {'meta': 'current_tab'}, deadline, identity=identity)
        empty = (ping.get('browser_kind') == 'cdp'
                 and current.get('error') in ('not_attached', 'cdp_disconnected'))
        # A closed last tab must not make the registered nonce-named daemon
        # immortal. A live foreign target still refuses attribution.
        if current.get('targetId') not in owned_targets and not empty:
            return None
        if _fingerprint(pid, deadline) != started or path.lstat().st_ino != info.st_ino:
            return None
        return identity
    except (OSError, ValueError, KeyError, TypeError):
        return None


def shutdown(identity, deadline=None):
    """Never signal a PID: recheck the socket/process and ask that daemon to exit."""
    deadline = deadline if deadline is not None else time.monotonic() + 3
    try:
        if _retired(identity, deadline):
            return True
        if _fingerprint(identity['pid'], deadline) != identity['started']:
            return False
        info = Path(identity['path']).lstat()
        if info.st_ino != identity['inode']:
            return False  # replacement socket is not authority over the old live process
        ping = exchange(identity['path'], {'meta': 'ping'}, deadline, identity=identity)
        if ping.get('pid') != identity['pid'] or not ping.get('pong'):
            return False
        result = exchange(identity['path'], {'meta': 'shutdown'}, deadline, identity=identity)
        if result.get('error'):
            return False
        while not _retired(identity, deadline):
            time.sleep(min(.05, _remaining(deadline)))
        return True
    except FileNotFoundError:
        # Missing IPC alone does not prove process retirement.
        try:
            return _retired(identity, deadline)
        except TimeoutError:
            return False
    except (OSError, ValueError, KeyError, TypeError):
        return False


def sweep(ledger, force=False, deadline=None):
    deadline = deadline if deadline is not None else time.monotonic() + 5
    if not ledger._daemon_lock.acquire(timeout=max(0, deadline - time.monotonic())):
        return
    try:
        _sweep(ledger, force, deadline)
    finally:
        ledger._daemon_lock.release()


def _endpoint_stamp(path):
    try:
        info = Path(path).lstat()
        return [info.st_dev, info.st_ino, info.st_ctime_ns]
    except FileNotFoundError:
        return None


def _vacant(path, deadline, identity=None):
    """Missing endpoint or refused socket of a proven retired generation only."""
    try:
        before = Path(path).lstat()
        if (not identity or identity.get('socket_stamp') != [before.st_dev, before.st_ino, before.st_ctime_ns]
                or not stat.S_ISSOCK(before.st_mode) or before.st_uid != os.getuid() or before.st_mode & 0o077):
            return False
        try:
            exchange(path, {'meta': 'ping'}, deadline)
        except ConnectionRefusedError:
            after = Path(path).lstat()
            return (before.st_dev, before.st_ino) == (after.st_dev, after.st_ino)
        return False
    except FileNotFoundError:
        return not os.path.lexists(path)
    except (OSError, ValueError, TypeError):
        return False


def _sweep(ledger, force, deadline):
    with ledger.transaction(readonly=True) as data:
        candidates = sorted((cap, dict(lease)) for cap, lease in data['leases'].items() if lease.get('daemon_socket'))
        owned = {cap: {tid for tid, target in data['targets'].items() if target['capability'] == cap}
                 for cap, _ in candidates}
    # A capability cursor survives successive sweeps, even if that record was
    # removed. A bounded slice alone would repeatedly starve a large ledger.
    cursor = ledger._daemon_cursor
    candidates = [c for c in candidates if c[0] > cursor] + [c for c in candidates if c[0] <= cursor]
    for index, (cap, snapshot) in enumerate(candidates):
        remaining = deadline - time.monotonic()
        if remaining <= 0 or (index and remaining < .1):
            return
        ledger._daemon_cursor = cap
        # Reserve a fair share for later owners, with enough minimum time for
        # real IPC when there are more owners than fit into one sweep.
        budget = min(.5, max(.1, remaining / (len(candidates) - index)))
        try:
            _candidate(ledger, force, cap, snapshot, owned[cap], min(deadline, time.monotonic() + budget))
        except (OSError, ValueError, KeyError, TypeError):
            # Timeout/probe failure is unresolved, never proof of retirement.
            continue


def _candidate(ledger, force, cap, snapshot, owned, deadline):
    identity = snapshot.get('daemon')
    if not identity or _retired(identity, deadline):
        # Keep a live old generation until its exit is proven. Only then may a
        # replacement inherit tracking, and only via complete fresh inspection.
        identity = inspect(snapshot['daemon_socket'], cap, owned, deadline) or identity
    elif 'socket_stamp' not in identity:
        # Upgrade legacy live identities without replacing a still-live PID.
        current = inspect(snapshot['daemon_socket'], cap, owned, deadline)
        if current and (current['pid'], current['started']) == (identity['pid'], identity['started']):
            identity = current
    with ledger.transaction() as data:
        lease = data['leases'].get(cap)
        if not lease or lease.get('daemon_socket') != snapshot['daemon_socket']:
            return
        if identity:
            lease['daemon'] = identity
        if not force and not _eligible(ledger, data, cap, lease):
            return
        ledger._retiring.add(cap)
    try:
        retired = not identity or shutdown(identity, deadline)
        # Process retirement does not establish endpoint retirement: a harness
        # reload can replace it during either inspection or shutdown.
        stamp = _endpoint_stamp(snapshot['daemon_socket'])
        if retired and _vacant(snapshot['daemon_socket'], deadline, identity):
            with ledger.transaction() as data:
                lease = data['leases'].get(cap)
                if (lease and lease.get('daemon_socket') == snapshot['daemon_socket']
                        and _endpoint_stamp(snapshot['daemon_socket']) == stamp):
                    lease.pop('daemon_socket', None)
                    lease.pop('daemon', None)
    finally:
        with ledger.lock:
            ledger._retiring.discard(cap)


def _eligible(ledger, data, cap, lease):
    now = ledger.clock()
    expiry = (lease['released'] + ledger.config.orphan_s if lease['released'] is not None
              else lease.get('restart_until', lease['activity'] + ledger.config.idle_lease_s))
    if now < expiry or ledger._holds.get(cap) or any(d > now for d in lease['requests'].values()):
        return False
    targets = {tid: target for tid, target in data['targets'].items() if target['capability'] == cap}
    if any(p['until'] > now for target in targets.values() for p in target['protections'].values()):
        return False
    return not any(d['until'] > now and (d.get('capability') == cap or d.get('target') in targets)
                   for d in data['downloads'].values())
