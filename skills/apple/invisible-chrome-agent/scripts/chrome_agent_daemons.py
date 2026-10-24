"""Owner-verified browser-harness IPC cleanup, without process-name killing."""
import json
import os
from pathlib import Path
import socket
import stat

from chrome_agent_process import fingerprint


def exchange(path, payload):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(2)
        client.connect(path)
        client.sendall(json.dumps(payload).encode() + b'\n')
        data = b''
        while not data.endswith(b'\n') and len(data) <= 65536:
            part = client.recv(4096)
            if not part:
                break
            data += part
        result = json.loads(data)
        if not isinstance(result, dict):
            raise ValueError('invalid harness IPC reply')
        return result


def inspect(path, capability, owned_targets):
    """Pin a registered private capability socket to its live harness process."""
    path = Path(path)
    if not path.is_absolute() or path.name != 'bu-lease-' + capability + '.sock':
        return None
    try:
        info = path.lstat()
        if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            return None
        ping = exchange(str(path), {'meta': 'ping'})
        pid = ping.get('pid')
        if not ping.get('pong') or type(pid) is not int or pid <= 0:
            return None
        current = exchange(str(path), {'meta': 'current_tab'})
        empty = (ping.get('browser_kind') == 'cdp'
                 and current.get('error') in ('not_attached', 'cdp_disconnected'))
        # A closed last tab must not make the registered nonce-named daemon
        # immortal. A live foreign target still refuses attribution.
        if current.get('targetId') not in owned_targets and not empty:
            return None
        started = fingerprint(pid)
        if started is None or path.lstat().st_ino != info.st_ino:
            return None
        return dict(path=str(path), pid=pid, started=started, inode=info.st_ino)
    except (OSError, ValueError, KeyError, TypeError):
        return None


def shutdown(identity):
    """Never signal a PID: recheck the socket/process and ask that daemon to exit."""
    try:
        info = Path(identity['path']).lstat()
        if info.st_ino != identity['inode'] or fingerprint(identity['pid']) != identity['started']:
            return True  # this recorded daemon is already gone/replaced
        ping = exchange(identity['path'], {'meta': 'ping'})
        if ping.get('pid') != identity['pid'] or not ping.get('pong'):
            return False
        result = exchange(identity['path'], {'meta': 'shutdown'})
        return not result.get('error')
    except FileNotFoundError:
        return True
    except (OSError, ValueError, KeyError, TypeError):
        return False


def sweep(ledger):
    with ledger.transaction(readonly=True) as data:
        candidates = [(cap, dict(lease)) for cap, lease in data['leases'].items() if lease.get('daemon_socket')]
        owned = {cap: {tid for tid, target in data['targets'].items() if target['capability'] == cap}
                 for cap, _ in candidates}
    for cap, snapshot in candidates:
        identity = snapshot.get('daemon')
        if identity and fingerprint(identity['pid']) != identity['started']:
            identity = None
        identity = identity or inspect(snapshot['daemon_socket'], cap, owned[cap])
        with ledger.transaction() as data:
            lease = data['leases'].get(cap)
            if not lease or lease.get('daemon_socket') != snapshot['daemon_socket']:
                continue
            if identity:
                lease['daemon'] = identity
            if not _eligible(ledger, data, cap, lease):
                continue
            ledger._retiring.add(cap)
        try:
            done = shutdown(identity) if identity else not Path(snapshot['daemon_socket']).exists()
            if done:
                with ledger.transaction() as data:
                    lease = data['leases'].get(cap)
                    if lease:
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
