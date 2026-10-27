"""Management broker server: Unix socket, kernel peer credentials, one request per connection.

Runs as the dedicated ``hermes-runners-broker`` OS identity from root-owned code.
Only the ``management`` API is routed here. The workflow-facing execution-claim
API (phase G) must be a separate listener/module with its own authentication;
it never shares this dispatcher, socket or the management credentials.
"""
import argparse
import hashlib
import json
import os
import pwd
import signal
import socket
import stat
import struct
import sys
import threading

if __package__:
    from .. import operations
else:  # executed as a script from the installed package root
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    from runners import operations  # type: ignore

MAX_REQUEST = 65536
MAX_CONCURRENT = 4
ROUTES = {'management': 'handle'}


def peer_uid(conn):
    raw = conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize('3i'))
    _pid, uid, _gid = struct.unpack('3i', raw)
    return uid


def read_policy(path, *, require_root_owner=True):
    path = os.fspath(path)
    st = os.lstat(path)
    if not stat.S_ISREG(st.st_mode):
        raise PermissionError('policy_not_regular')
    if stat.S_IMODE(st.st_mode) & 0o022:
        raise PermissionError('policy_writable_by_others')
    if require_root_owner and st.st_uid != 0:
        raise PermissionError('policy_not_root_owned')
    with open(path, encoding='utf-8') as f:
        return operations.load_policy(json.load(f))


class ManagementServer:
    def __init__(self, path, engine, *, socket_mode=0o660):
        self.path, self.engine = path, engine
        if os.path.exists(path):
            os.unlink(path)
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        old = os.umask(0o177)
        try:
            self.sock.bind(path)
        finally:
            os.umask(old)
        os.chmod(path, socket_mode)
        self.sock.listen(16)
        self.sock.settimeout(0.5)
        self._stop = threading.Event()
        self._slots = threading.BoundedSemaphore(MAX_CONCURRENT)

    def _reply(self, conn, result):
        try:
            conn.sendall(json.dumps(result).encode())
        except OSError:
            pass

    def _serve_one(self, conn):
        try:
            conn.settimeout(10)
            uid = peer_uid(conn)
            data = b''
            while len(data) <= MAX_REQUEST:
                chunk = conn.recv(65536)
                if not chunk:
                    break
                data += chunk
            if len(data) > MAX_REQUEST:
                return self._reply(conn, dict(ok=False, refusal='request_too_large', detail=''))
            try:
                body = json.loads(data)
            except ValueError:
                return self._reply(conn, dict(ok=False, refusal='invalid_request', detail='malformed json'))
            if not isinstance(body, dict):
                return self._reply(conn, dict(ok=False, refusal='invalid_request', detail='object required'))
            if body.get('api') not in ROUTES:
                return self._reply(conn, dict(ok=False, refusal='unknown_api', detail='only the management api is served here'))
            conn.settimeout(120)
            self._reply(conn, getattr(self.engine, ROUTES[body['api']])(body, peer_uid=uid))
        except (OSError, socket.timeout):
            pass
        finally:
            conn.close()
            self._slots.release()

    def serve_forever(self):
        while not self._stop.is_set():
            try:
                conn, _ = self.sock.accept()
            except socket.timeout:
                continue
            except OSError:
                if self._stop.is_set():
                    break
                raise
            if not self._slots.acquire(blocking=False):
                self._reply(conn, dict(ok=False, refusal='broker_busy', detail=''))
                conn.close()
                continue
            threading.Thread(target=self._serve_one, args=(conn,), daemon=True).start()

    def shutdown(self):
        self._stop.set()
        try:
            self.sock.close()
        finally:
            if os.path.exists(self.path):
                os.unlink(self.path)


def _audit_writer(path):
    lock = threading.Lock()

    def write(record):
        line = json.dumps(record, sort_keys=True) + '\n'
        with lock:
            fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_CLOEXEC, 0o600)
            try:
                os.write(fd, line.encode())
            finally:
                os.close(fd)
    return write


def main(argv=None):
    ap = argparse.ArgumentParser(description='Hermes runners management broker')
    ap.add_argument('--policy', default='/etc/hermes-runners-broker/policy.json')
    ap.add_argument('--credentials', default='/etc/hermes-runners-broker/credentials')
    ap.add_argument('--state-dir', default='/var/lib/hermes-runners-broker')
    ap.add_argument('--socket', default=operations.DEFAULT_SOCKET)
    ap.add_argument('--socket-mode', default='0666',
                    help='peers are authenticated by SO_PEERCRED uid allowlist, not by file mode')
    args = ap.parse_args(argv)
    if os.geteuid() == 0:
        raise SystemExit('refusing to run as root; run as the dedicated broker identity')
    me = pwd.getpwuid(os.geteuid()).pw_name
    policy = read_policy(args.policy, require_root_owner=True)
    if any(c['uid'] == os.geteuid() for c in policy['clients']):
        raise SystemExit('broker identity must differ from every client identity')
    if 'machine_id_sha256' in policy:
        with open('/etc/machine-id', 'rb') as f:
            if hashlib.sha256(f.read().strip()).hexdigest() != policy['machine_id_sha256']:
                raise SystemExit('policy belongs to a different host')
    engine = operations.Engine(policy, systemd=operations.SystemctlPort(), processes=operations.CgroupProcessPort(),
                               github=operations.GitHubPort(args.credentials, expected_uid=os.geteuid()),
                               locks=operations.FlockLocks(os.path.join(args.state_dir, 'locks')),
                               audit=_audit_writer(os.path.join(args.state_dir, 'audit.jsonl')))
    srv = ManagementServer(args.socket, engine, socket_mode=int(args.socket_mode, 8))
    signal.signal(signal.SIGTERM, lambda *_: srv.shutdown())
    print(json.dumps(dict(event='broker_ready', user=me, socket=args.socket, targets=len(policy['targets']),
                          management_operations=policy['management_operations'])), flush=True)
    srv.serve_forever()


if __name__ == '__main__':
    main()
