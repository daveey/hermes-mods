"""Opt-in browser leases. Stdlib only; raw CDP clients remain unowned.

The ledger is a lifecycle boundary, not a security boundary against the local user
(who can access Chrome's private CDP port and the installation files).
"""
import contextlib
import copy
import fcntl
import json
import math
import os
from pathlib import Path
import secrets
import sys
import tempfile
import threading
import time

MAX_LEDGER_BYTES = 16 * 1024 * 1024

class LifecycleError(Exception):
    pass


class Config:
    DEFAULTS = dict(idle_lease_s=1800, orphan_s=300, idle_browser_s=120,
                    handoff_s=1200, request_grace_s=30, download_stall_s=120,
                    download_max_s=3600)

    def __init__(self, **values):
        if set(values) - set(self.DEFAULTS):
            raise LifecycleError('unknown lifecycle setting')
        for key, default in self.DEFAULTS.items():
            value = values.get(key, default)
            if type(value) not in (int, float) or not math.isfinite(value) or not 1 <= value <= 86400:
                raise LifecycleError('invalid lifecycle setting: ' + key)
            setattr(self, key, value)


def atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, tmp = tempfile.mkstemp(prefix=path.name + '.', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(data, stream, allow_nan=False)
            if stream.tell() > MAX_LEDGER_BYTES:
                raise LifecycleError('ledger capacity reached; refusing mutation')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


class ExclusiveLock:
    """Lifetime process lock; also used for the short ledger transaction lock."""
    def __init__(self, path, blocking=False):
        Path(path).parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
        os.fchmod(self.fd, 0o600)
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        except OSError:
            os.close(self.fd)
            raise LifecycleError('another supervisor or writer holds the lock')

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class Ledger:
    def __init__(self, path, config=None, clock=None):
        from chrome_agent_clock import RuntimeClock
        self.path = Path(path)
        self.config = config or Config()
        # An injected clock is already a logical clock (deterministic tests).
        try:
            self.runtime_clock = RuntimeClock() if clock is None else None
        except RuntimeError as exc:
            raise LifecycleError(str(exc)) from exc
        self.clock = clock if clock is not None else self.runtime_clock
        self.lock = threading.RLock()
        self._data = None
        self._stamp = None
        self._dirty = False
        self._progress_saved = {}
        self._holds = {}
        self._closing = set()
        self._retiring = set()
        self._daemon_lock = threading.Lock()
        self._daemon_cursor = ''

    @staticmethod
    def validate(data):
        def number(value):
            if type(value) not in (int, float) or not math.isfinite(value):
                raise ValueError('invalid timestamp')
        if data['version'] != 1 or not isinstance(data['generation'], str):
            raise ValueError('invalid schema')
        if 'clock' in data:
            clock = data['clock']
            number(clock['logical'])
            number(clock['monotonic'])
            if not isinstance(clock['boot'], str) or not clock['boot'] or clock['monotonic'] < 0:
                raise ValueError('invalid clock anchor')
        if not isinstance(data['leases'], dict) or not isinstance(data['targets'], dict):
            raise ValueError('invalid maps')
        for cap, lease in data['leases'].items():
            if not isinstance(cap, str) or not isinstance(lease['owner'], list) or len(lease['owner']) != 3:
                raise ValueError('invalid lease')
            number(lease['activity'])
            if lease['released'] is not None:
                number(lease['released'])
            for deadline in lease['requests'].values():
                number(deadline)
            if 'restart_until' in lease:
                number(lease['restart_until'])
            for pid, started in lease.get('workers', {}).items():
                if not isinstance(pid, str) or not pid.isdigit() or not isinstance(started, str):
                    raise ValueError('invalid worker identity')
        for record in data['targets'].values():
            if record['capability'] not in data['leases'] or not isinstance(record['generation'], str):
                raise ValueError('invalid target binding')
            for protection in list(record['protections'].values()) + list(record.get('downloads', {}).values()):
                number(protection['until'])
                if 'max_until' in protection:
                    number(protection['max_until'])
                    number(protection['bytes'])
        for download in data.get('downloads', {}).values():
            number(download['until'])
            number(download['max_until'])
            number(download['bytes'])

    @contextlib.contextmanager
    def transaction(self, readonly=False, persist=True, track=True):
        # The supervisor lifetime lock makes this the sole writer. Event reads
        # use validated memory, never full-file parsing/serialization under flock.
        with self.lock:
            stamp = self._file_stamp()
            if self._data is not None and stamp != self._stamp:
                raise LifecycleError('lease ledger changed externally; refusing destructive operations')
            if self._data is None:
                try:
                    if stamp is None:
                        data = dict(version=1, generation=secrets.token_hex(16), leases={}, targets={}, downloads={})
                    else:
                        if self.path.stat().st_size > MAX_LEDGER_BYTES:
                            raise ValueError('ledger size limit')
                        with self.path.open() as stream:
                            data = json.load(stream)
                        self.validate(data)
                    downloads = data.setdefault('downloads', {})
                    for tid, record in data['targets'].items():
                        for guid, download in record.pop('downloads', {}).items():
                            downloads[guid] = dict(download, target=tid)
                    if self.runtime_clock and self.runtime_clock.restore(data, self.config):
                        # Reconciliation is durable even when the first operation
                        # is readonly: a crash must not renew migration/reboot grace.
                        data['clock'] = self.runtime_clock.snapshot()
                        atomic_json(self.path, data)
                        stamp = self._file_stamp()
                    self._data, self._stamp = data, stamp
                except (ValueError, KeyError, TypeError, AttributeError) as exc:
                    raise LifecycleError('corrupt lease ledger; refusing destructive operations') from exc
            before = None if readonly or not track else copy.deepcopy(self._data)
            try:
                yield self._data
                if not readonly:
                    self._dirty = self._dirty or not track or before != self._data or self._stamp is None
                    if persist and self._dirty:
                        if self.runtime_clock:
                            self._data['clock'] = self.runtime_clock.snapshot()
                        atomic_json(self.path, self._data)
                        self._stamp = self._file_stamp()
                        self._dirty = False
            except BaseException:
                if not readonly:
                    self._data = before
                    self._dirty = False
                raise

    def _file_stamp(self):
        try:
            stat = self.path.stat()
            return stat.st_ino, stat.st_mtime_ns, stat.st_size
        except FileNotFoundError:
            return None

    def _prune(self, data):
        now = self.clock()
        bound = {r['capability'] for r in data['targets'].values()}
        for cap, lease in list(data['leases'].items()):
            lease['requests'] = {k: v for k, v in lease['requests'].items() if v > now}
            expiry = (lease['released'] if lease['released'] is not None
                      else lease['activity'] + self.config.idle_lease_s)
            if cap not in bound and not lease['requests'] and not lease.get('daemon_socket') and now >= expiry + self.config.orphan_s:
                del data['leases'][cap]
        for guid, download in list(data['downloads'].items()):
            if download['until'] <= now:
                del data['downloads'][guid]
                self._progress_saved.pop(guid, None)

    def _lease(self, data, cap):
        lease = data['leases'].get(cap)
        if not lease or lease.get('released') is not None or cap in self._retiring:
            raise LifecycleError('invalid or released capability')
        now = self.clock()
        active = any(deadline > now for deadline in lease['requests'].values())
        if not active and now >= lease.get('restart_until', lease['activity'] + self.config.idle_lease_s):
            raise LifecycleError('expired capability')
        return lease

    def valid(self, cap):
        with self.transaction(readonly=True) as data:
            self._lease(data, cap)
            return True

    @contextlib.contextmanager
    def hold(self, cap, generation=None):
        with self.transaction(readonly=True) as data:
            self._lease(data, cap)
            current = data['generation']
            if generation is not None and current != generation:
                raise LifecycleError('stale browser generation; reconnect required')
            if self._holds.get(cap, 0) >= 256:
                raise LifecycleError('too many in-flight commands')
            self._holds[cap] = self._holds.get(cap, 0) + 1
        try:
            yield current
            with self.transaction(readonly=True) as data:
                if data['generation'] != current:
                    raise LifecycleError('stale browser generation; reconnect required')
        finally:
            with self.lock:
                self._holds[cap] -= 1
                if not self._holds[cap]:
                    del self._holds[cap]

    def acquire(self, owner, timeout_s, worker_pid=None, daemon_dir=None):
        if not isinstance(owner, list) or len(owner) != 3 or any(not isinstance(x, str) or not x or len(x) > 4096 for x in owner):
            raise LifecycleError('owner must be [profile_home, task_id, session]')
        if type(timeout_s) not in (int, float) or not math.isfinite(timeout_s) or not 1 <= timeout_s <= 86400:
            raise LifecycleError('timeout_s must be between 1 and 86400')
        worker = None
        if daemon_dir is not None and (not isinstance(daemon_dir, str) or len(daemon_dir) > 4096 or not os.path.isabs(daemon_dir)):
            raise LifecycleError('invalid daemon directory')
        if worker_pid is not None:
            from chrome_agent_process import fingerprint
            if type(worker_pid) is not int or worker_pid <= 0:
                raise LifecycleError('invalid worker process')
            worker = fingerprint(worker_pid)
            if worker is None:
                raise LifecycleError('worker process identity unavailable')
        with self.transaction() as data:
            self._prune(data)
            cap = None
            for key, lease in data['leases'].items():
                if lease['owner'] == owner:
                    try:
                        self._lease(data, key)
                        cap = key
                        break
                    except LifecycleError:
                        continue
            if cap is None:
                if len(data['leases']) >= 8192:
                    raise LifecycleError('active lease capacity reached')
                cap = secrets.token_urlsafe(32)
                data['leases'][cap] = dict(owner=owner, activity=self.clock(), requests={}, released=None)
            lease = data['leases'][cap]
            if len(lease['requests']) >= 256:
                raise LifecycleError('owner request capacity reached')
            lease['activity'] = self.clock()
            request = secrets.token_urlsafe(24)
            lease.pop('restart_until', None)
            if daemon_dir:
                path = str(Path(daemon_dir) / ('bu-lease-' + cap + '.sock'))
                # Reserve the terminating NUL in sockaddr_un.sun_path. Reject
                # before committing the lease or waking Chrome; never relocate
                # the endpoint away from the harness's configured runtime dir.
                maximum = 107 if sys.platform == 'linux' else 103
                if '\0' in path or len(os.fsencode(path)) > maximum:
                    raise LifecycleError('daemon socket path exceeds OS limit; configure a shorter harness runtime directory')
                lease['daemon_socket'] = path
            if worker:
                workers = lease.setdefault('workers', {})
                if str(worker_pid) not in workers and len(workers) >= 256:
                    raise LifecycleError('worker capacity reached')
                workers[str(worker_pid)] = worker
            lease['requests'][request] = self.clock() + timeout_s + self.config.request_grace_s
            return dict(capability=cap, request_id=request, daemon_name='lease-' + cap)

    def complete(self, cap, request):
        with self.transaction() as data:
            lease = data['leases'].get(cap)
            if not lease or lease['released'] is not None or request not in lease['requests']:
                return False
            del lease['requests'][request]
            lease['activity'] = self.clock()
            return True

    def release(self, cap):
        with self.transaction() as data:
            lease = data['leases'].get(cap)
            if lease and lease['released'] is None:
                lease['released'] = self.clock()

    def bind(self, cap, target, generation=None):
        with self.transaction() as data:
            if generation is None or not self._holds.get(cap):
                self._lease(data, cap)
            if generation is not None and data['generation'] != generation:
                raise LifecycleError('stale browser generation; refusing target bind')
            self._bind_record(data, cap, target)

    def _bind_record(self, data, cap, target):
        if target in self._closing:
            raise LifecycleError('target is closing')
        if target not in data['targets'] and len(data['targets']) >= 16384:
            raise LifecycleError('target capacity reached')
        old = data['targets'].get(target)
        if old and (old['capability'] != cap or old['generation'] != data['generation']):
            raise LifecycleError('target already bound')
        data['targets'].setdefault(target, dict(capability=cap, generation=data['generation'], protections={}))

    def owns(self, cap, target):
        with self.transaction(readonly=True) as data:
            try:
                self._lease(data, cap)
            except LifecycleError:
                return False
            record = data['targets'].get(target, {})
            return (target not in self._closing and record.get('capability') == cap
                    and record.get('generation') == data['generation'])

    def new_generation(self):
        with self.transaction() as data:
            data['generation'] = secrets.token_hex(16)
            data['targets'] = {}
            data['downloads'] = {}
            self._progress_saved.clear()

    def reconcile(self):
        """One bounded grace per orphan episode, preserving protected deadlines."""
        with self.transaction() as data:
            until = self.clock() + self.config.orphan_s
            for lease in data['leases'].values():
                if lease['released'] is None:
                    lease.setdefault('restart_until', until)

    def reap_workers(self):
        from chrome_agent_process import fingerprint
        with self.transaction(readonly=True) as data:
            workers = {pid: started for lease in data['leases'].values()
                       for pid, started in lease.get('workers', {}).items()}
        # Process probes may invoke ps on macOS; never run them under the ledger lock.
        current = {pid: fingerprint(int(pid)) for pid in workers}
        with self.transaction() as data:
            for lease in data['leases'].values():
                owned = lease.get('workers')
                if not owned:
                    continue
                for pid, started in list(owned.items()):
                    if pid in current and workers[pid] == started and current[pid] != started:
                        del owned[pid]
                if not owned:
                    lease.setdefault('restart_until', self.clock() + self.config.orphan_s)

    def sweep_daemons(self, force=False, deadline=None):
        from chrome_agent_daemons import sweep
        sweep(self, force=force, deadline=deadline)

    def popup(self, info):
        """Only Chrome-reported opener identity confers ownership, never URLs."""
        if info.get('type') not in ('page', 'webview') or not info.get('targetId') or not info.get('openerId'):
            return False
        with self.transaction() as data:
            parent = data['targets'].get(info.get('openerId'))
            if not parent or parent['generation'] != data['generation']:
                return False
            cap = parent['capability']
            handoff = parent['protections'].get('handoff')
            try:
                self._lease(data, cap)
            except LifecycleError:
                if not handoff or handoff['until'] <= self.clock():
                    return False
            self._bind_record(data, cap, info['targetId'])
            if handoff and handoff['until'] > self.clock():
                data['targets'][info['targetId']]['protections'].setdefault('handoff', dict(handoff))
            return True

    def protect(self, target, kind, progress=None, episode=None, until=None):
        if kind not in ('handoff', 'download'):
            raise LifecycleError('invalid protection')
        with self.transaction() as data:
            record = data['targets'].get(target)
            if not record or record['generation'] != data['generation'] or target in self._closing:
                return
            protections = record['protections']
            now = self.clock()
            old = protections.get(kind)
            if kind == 'handoff':
                # Explicit episode identity, not polling or deadline expiry, renews.
                if old is None or (episode is not None and old.get('episode') != episode):
                    deadline = min(until, now + self.config.handoff_s) if until is not None else now + self.config.handoff_s
                    protections[kind] = dict(until=deadline, episode=episode)
            elif progress is None:
                protections.pop(kind, None)
            elif old is None:
                protections[kind] = dict(until=now + self.config.download_stall_s,
                                         max_until=now + self.config.download_max_s, bytes=progress)
            elif progress > old['bytes']:
                old.update(bytes=progress, until=min(old['max_until'], now + self.config.download_stall_s))

    def download(self, event, params, target=None):
        guid = params.get('guid')
        if not isinstance(guid, str) or len(guid) > 256:
            return
        received = params.get('receivedBytes', 0)
        if type(received) not in (int, float) or not math.isfinite(received) or received < 0:
            return
        with self.transaction(readonly=True) as data:
            if event == 'downloadWillBegin':
                record = data['targets'].get(target)
                if not record or record['generation'] != data['generation']:
                    return
            elif guid not in data['downloads']:
                return
        now = self.clock()
        terminal = params.get('state') in ('completed', 'canceled')
        persist = event == 'downloadWillBegin' or terminal or now - self._progress_saved.get(guid, 0) >= 5
        with self.transaction(persist=persist, track=False) as data:
            now = self.clock()
            if event == 'downloadWillBegin':
                record = data['targets'].get(target)
                if record and record['generation'] == data['generation']:
                    if len(data['downloads']) >= 4096:
                        raise LifecycleError('download capacity reached')
                    data['downloads'].setdefault(guid, dict(bytes=0, target=target, capability=record['capability'],
                        until=now + self.config.download_stall_s, max_until=now + self.config.download_max_s))
            else:
                download = data['downloads'].get(guid)
                if download and terminal:
                    del data['downloads'][guid]
                elif download and params.get('receivedBytes', 0) > download['bytes']:
                    download.update(bytes=params['receivedBytes'],
                                    until=min(download['max_until'], now + self.config.download_stall_s))
            if terminal:
                self._progress_saved.pop(guid, None)
            elif persist:
                self._progress_saved[guid] = now

    def reap(self, live_targets, close):
        closed = []
        candidates = []
        with self.transaction() as data:
            now = self.clock()
            generation = data['generation']
            for tid, record in list(data['targets'].items()):
                if record['generation'] != generation or self._holds.get(record['capability']):
                    continue
                lease = data['leases'][record['capability']]
                base = (lease['released'] + self.config.orphan_s if lease['released'] is not None
                        else lease.get('restart_until', lease['activity'] + self.config.idle_lease_s))
                expiry = max([base, lease.get('restart_until', 0)] + list(lease['requests'].values()))
                protections = list(record['protections'].values()) + [
                    d for d in data['downloads'].values() if d.get('target') == tid]
                protected = any(p['until'] > now for p in protections)
                if tid not in live_targets and now >= expiry and not protected:
                    del data['targets'][tid]
                    continue
                if now >= expiry and not protected:
                    self._closing.add(tid)
                    candidates.append(tid)
        # Reserve expired targets before I/O. Fresh bindings and commands in
        # flight are never removed by a stale target snapshot.
        for tid in candidates:
            try:
                success = tid not in live_targets or close(tid)
                with self.transaction() as data:
                    if success and data['generation'] == generation:
                        data['targets'].pop(tid, None)
                        closed.append(tid)
            finally:
                with self.lock:
                    self._closing.discard(tid)
        return closed

    def can_idle(self, live_targets):
        # Never infer ownership of startup tabs, popups or legacy clients' tabs.
        if live_targets:
            return False
        with self.transaction(readonly=True) as data:
            if self._holds or data['targets']:
                return False
            if any(d['until'] > self.clock() for d in data['downloads'].values()):
                return False
            for cap in data['leases']:
                try:
                    self._lease(data, cap)
                    return False
                except LifecycleError:
                    continue
            return True


class Lifecycle:
    """Serializes acquire/wake and sweep/shutdown; the listener survives idle."""
    def __init__(self, ledger, installation_id, wake, stop=lambda: None):
        self.ledger, self.installation_id = ledger, installation_id
        self.wake, self.stop = wake, stop
        self.lock = threading.RLock()
        self.transition = threading.RLock()
        self.running = False
        self.disabled = False
        self.empty_since = None
        self.connections = set()
        self.daemon_sweep = None
        self.require_browser_identity = False

    def revoke_connections(self):
        with self.lock:
            connections = list(self.connections)
        for connection in connections:
            with self.ledger.transaction(readonly=True) as data:
                lease = data['leases'].get(connection.cap, {})
                protected = self.ledger._holds.get(connection.cap) or any(
                    d > self.ledger.clock() for d in lease.get('requests', {}).values())
                stale = data['generation'] != connection.generation
                try:
                    self.ledger._lease(data, connection.cap)
                    invalid = False
                except LifecycleError:
                    invalid = True
            if stale or (invalid and not protected):
                connection.close()

    def control(self, action, payload, endpoint):
        if not isinstance(payload, dict) or payload.get('installation_id') != self.installation_id:
            raise LifecycleError('installation identity mismatch')
        with self.transition:
            if self.disabled and action == 'acquire':
                raise LifecycleError('installation stopped')
            if action == 'acquire':
                result = self.ledger.acquire(payload.get('owner'), payload.get('timeout_s'), payload.get('worker_pid'), payload.get('daemon_dir'))
                try:
                    if not self.running:
                        self.wake()
                        self.running = True
                        self.revoke_connections()
                        # Startup must not consume the caller's execution budget.
                        self.ledger.complete(result['capability'], result['request_id'])
                        result = self.ledger.acquire(payload['owner'], payload['timeout_s'], payload.get('worker_pid'), payload.get('daemon_dir'))
                except BaseException:
                    self.ledger.release(result['capability'])
                    raise
                self.empty_since = None
                result['endpoint'] = endpoint + '/lease/' + result['capability']
                return result
            if action == 'complete':
                return dict(completed=self.ledger.complete(payload.get('capability'), payload.get('request_id')))
            if action == 'release':
                self.ledger.release(payload.get('capability'))
                self.revoke_connections()
                return dict(released=True)
            raise LifecycleError('unknown lifecycle action')

    def drain(self, timeout=30):
        """Refuse new work immediately; allow completions during a bounded drain."""
        self.disabled = True
        deadline = time.monotonic() + timeout
        waiter = threading.Event()
        while True:
            with self.ledger.transaction(readonly=True) as data:
                now = self.ledger.clock()
                protected = bool(self.ledger._holds) or any(
                    d > now for lease in data['leases'].values() for d in lease['requests'].values())
                protected = protected or any(d['until'] > now for d in data['downloads'].values())
                protected = protected or any(p['until'] > now for target in data['targets'].values()
                                              for p in target['protections'].values())
            if not protected:
                return True
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            waiter.wait(min(.1, remaining))

    def maintain_daemons(self):
        with self.lock:
            if self.daemon_sweep is None or not self.daemon_sweep.is_alive():
                self.daemon_sweep = threading.Thread(target=self.ledger.sweep_daemons, daemon=True)
                self.daemon_sweep.start()

    def retire_daemons(self, timeout=10):
        """Only after drain: explicit stop wins, but unknown processes never do."""
        deadline = time.monotonic() + timeout
        with self.ledger.transaction(readonly=True) as data:
            remaining = sum(bool(lease.get('daemon_socket')) for lease in data['leases'].values())
        while time.monotonic() < deadline:
            self.ledger.sweep_daemons(force=True, deadline=deadline)
            with self.ledger.transaction(readonly=True) as data:
                remaining = sum(bool(lease.get('daemon_socket')) for lease in data['leases'].values())
            if not remaining:
                break
            threading.Event().wait(min(.1, max(0, deadline - time.monotonic())))
        return remaining

    def tick(self, targets, close):
        self.maintain_daemons()
        with self.transition:
            self.ledger.reap_workers()
            self.revoke_connections()
            closed = self.ledger.reap(targets, close)
            remaining = set(targets) - set(closed)
            if not self.running or not self.ledger.can_idle(remaining):
                self.empty_since = None
                return closed
            now = self.ledger.clock()
            if self.empty_since is None:
                self.empty_since = now
            if now - self.empty_since >= self.ledger.config.idle_browser_s:
                self.stop()
                self.running = False
                self.empty_since = None
            return closed

    def route(self, proxy, c, reader, method, target, headers, body, api):
        """Managed requests never fall back to the unscoped HTTP/WS tunnel."""
        if not target.startswith(('/lifecycle/', '/lease/')):
            proxy._respond(c, 403, 'Forbidden', json.dumps({'error': 'managed Chrome requires an owner lease'}), 'application/json')
            return True
        try:
            if target.startswith('/lifecycle/'):
                if method != 'POST':
                    raise LifecycleError('lifecycle requires POST')
                result = self.control(target[len('/lifecycle/'):], json.loads(body),
                                      'http://127.0.0.1:%d' % proxy.port)
            else:
                _, _, cap, path = target.split('/', 3)
                self.ledger.valid(cap)
                path = '/' + path
                if dict((k.lower(), v.lower()) for k, v in headers).get('upgrade') == 'websocket':
                    self.websocket(proxy, c, reader, cap, path, headers, api)
                    return True
                result = self.discovery(proxy, cap, method, path, api)
            proxy._respond(c, 200, 'OK', json.dumps(result), 'application/json')
        except (LifecycleError, ValueError, KeyError, TypeError) as exc:
            proxy._respond(c, 403, 'Forbidden', json.dumps({'error': str(exc)}), 'application/json')
        except (OSError, api.CDPError) as exc:
            proxy._respond(c, 503, 'Unavailable', json.dumps({'error': str(exc)}), 'application/json')
        return True

    def browser_version(self, port, read_version):
        """A replaced internal listener is not authority for an existing scope."""
        from chrome_agent_process import owns_listener
        with self.ledger.transaction(readonly=True) as data:
            browser = dict(data.get('browser') or {})
        if self.require_browser_identity and not browser:
            raise LifecycleError('owned browser identity has not been established')
        if browser and not owns_listener(browser, port):
            raise LifecycleError('recorded browser no longer owns upstream listener')
        version = read_version(port=port)
        if browser and (not version or version.get('webSocketDebuggerUrl') != browser.get('websocket')
                        or not owns_listener(browser, port)):
            raise LifecycleError('recorded browser endpoint changed')
        return version

    def discovery(self, proxy, cap, method, path, api):
        import urllib.request
        import urllib.parse
        prefix = 'ws://127.0.0.1:%d/lease/%s' % (proxy.port, cap)
        if method == 'GET' and path == '/json/version':
            version = self.browser_version(proxy.upstream, api.cdp_version)
            if not version:
                raise LifecycleError('browser is idle or unavailable')
            version['webSocketDebuggerUrl'] = prefix + '/devtools/browser/scoped'
            return version
        if method == 'GET' and path in ('/json', '/json/list', '/json/'):
            self.browser_version(proxy.upstream, api.cdp_version)
            with urllib.request.urlopen('http://127.0.0.1:%d/json/list' % proxy.upstream, timeout=5) as response:
                targets = json.load(response)
            self.browser_version(proxy.upstream, api.cdp_version)
            owned = [t for t in targets if self.ledger.owns(cap, t['id'])]
            for t in owned:
                t['webSocketDebuggerUrl'] = prefix + '/devtools/page/' + t['id']
                t.pop('devtoolsFrontendUrl', None)
            return owned
        if method == 'PUT' and path.partition('?')[0] == '/json/new':
            # The create response, not a global target-list diff, establishes ownership.
            with self.ledger.hold(cap) as generation:
                target = proxy._new_background(urllib.parse.unquote(path.partition('?')[2]))
                self.ledger.bind(cap, target['id'], generation)
                target['webSocketDebuggerUrl'] = prefix + '/devtools/page/' + target['id']
                return target
        if method == 'GET' and path.startswith(('/json/close/', '/json/activate/')):
            tid = path.rsplit('/', 1)[1]
            if not self.ledger.owns(cap, tid):
                raise LifecycleError('unowned target')
            connection = ScopedCDP(self, proxy, cap, api)
            try:
                command = 'Target.closeTarget' if path.startswith('/json/close/') else 'Target.activateTarget'
                return connection.call(command, {'targetId': tid})
            finally:
                connection.close()
        raise LifecycleError('unsupported scoped endpoint')

    def websocket(self, proxy, c, reader, cap, path, headers, api):
        import base64
        import hashlib
        page = None
        if path.startswith('/devtools/page/'):
            page = path[len('/devtools/page/'):]
            if not self.ledger.owns(cap, page):
                raise LifecycleError('unowned target')
        elif path != '/devtools/browser/scoped':
            raise LifecycleError('invalid scoped websocket')
        key = dict((k.lower(), v) for k, v in headers).get('sec-websocket-key')
        if not key:
            raise LifecycleError('websocket key missing')
        send_lock = threading.Lock()

        def send(message):
            with send_lock:
                c.sendall(api.encode_frame(1, json.dumps(message).encode(), mask=False))

        connection = ScopedCDP(self, proxy, cap, api, send)
        slots = threading.BoundedSemaphore(128)
        upgraded = False

        def dispatch(message):
            try:
                reply = {'id': message.get('id')}
                if 'sessionId' in message:
                    reply['sessionId'] = message['sessionId']
                try:
                    reply['result'] = connection.call(message['method'], message.get('params', {}), message.get('sessionId'))
                except (LifecycleError, api.CDPError, ValueError, KeyError, TypeError, OSError) as exc:
                    reply['error'] = dict(code=-32000, message=str(exc))
                send(reply)
            except OSError:
                connection.close()
            finally:
                slots.release()

        def close_client():
            import socket
            with contextlib.suppress(OSError):
                # Do not wait for a blocked sender or write another frame to a
                # peer that stopped reading. Revocation must interrupt both ends.
                c.shutdown(socket.SHUT_RDWR)

        connection.close_client = close_client
        try:
            if page:
                connection.page_session = connection.attach(page)['sessionId']
            accept = base64.b64encode(hashlib.sha1((key + api.WS_GUID).encode()).digest()).decode()
            c.sendall(('HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n'
                       'Sec-WebSocket-Accept: %s\r\n\r\n' % accept).encode())
            upgraded = True
            payload = b''
            while True:
                _, fin, op, chunk = api.read_frame(reader)
                if op == 8:
                    return
                if op == 9:
                    with send_lock:
                        c.sendall(api.encode_frame(10, chunk, mask=False))
                    continue
                if op not in (0, 1) or len(payload) + len(chunk) > 16 * 1024 * 1024:
                    raise LifecycleError('invalid websocket message')
                payload += chunk
                if not fin:
                    continue
                message = json.loads(payload)
                payload = b''
                if not isinstance(message, dict) or not slots.acquire(blocking=False):
                    raise LifecycleError('invalid message or scoped request capacity reached')
                threading.Thread(target=dispatch, args=(message,), daemon=True).start()
        except (EOFError, OSError):
            return
        except (LifecycleError, api.CDPError, ValueError, KeyError, TypeError):
            if not upgraded:
                raise
            close_client()
        finally:
            connection.close()


class ScopedCDP:
    """Terminate scoped CDP rather than forwarding opaque browser/session envelopes."""
    PAGE_DOMAINS = frozenset(('Page', 'Runtime', 'DOM', 'DOMSnapshot', 'CSS', 'Accessibility',
                              'Network', 'Fetch', 'Input', 'Emulation', 'Log', 'Performance',
                              'Debugger', 'Profiler', 'HeapProfiler', 'Overlay', 'WebAuthn'))

    def __init__(self, lifecycle, proxy, capability, api, send_event=lambda m: None):
        self.lifecycle, self.proxy, self.cap, self.api = lifecycle, proxy, capability, api
        with lifecycle.ledger.transaction(readonly=True) as data:
            self.generation = data['generation']
        self.sessions = {}
        self.session_lock = threading.RLock()
        self.closed = False
        self.close_client = lambda: None
        self.page_session = None
        self.auto_attach = False
        self.send_event = send_event
        version = lifecycle.browser_version(proxy.upstream, api.cdp_version)
        if not version:
            raise LifecycleError('browser unavailable')
        self.cdp = api.CDP(version['webSocketDebuggerUrl'], on_event=self.event)
        with lifecycle.lock:
            lifecycle.connections.add(self)

    def close(self):
        with self.lifecycle.lock:
            if self.closed:
                return
            self.closed = True
            self.lifecycle.connections.discard(self)
        try:
            self.close_client()
        finally:
            self.cdp.close()

    def owned(self, tid):
        if not self.lifecycle.ledger.owns(self.cap, tid):
            raise LifecycleError('unowned or expired target')

    def attach(self, tid):
        self.owned(tid)
        result = self.cdp.call('Target.attachToTarget', {'targetId': tid, 'flatten': True})
        with self.session_lock:
            self.sessions[result['sessionId']] = tid
        return result

    def event(self, message):
        if self.closed:
            return
        with self.lifecycle.ledger.transaction(readonly=True) as data:
            if data['generation'] != self.generation:
                return
        method = message.get('method')
        params = message.get('params', {})
        if not message.get('sessionId') and method in ('Target.targetCreated', 'Target.targetInfoChanged'):
            info = params.get('targetInfo', {})
            self.lifecycle.ledger.popup(info)
            if self.lifecycle.ledger.owns(self.cap, info.get('targetId')):
                self.send_event(message)
                if method == 'Target.targetCreated' and self.auto_attach:
                    threading.Thread(target=self._attach_popup, args=(info,), daemon=True).start()
            return
        if not message.get('sessionId') and method in ('Target.targetDestroyed', 'Target.targetCrashed'):
            if self.lifecycle.ledger.owns(self.cap, params.get('targetId')):
                self.send_event(message)
            return
        sid = message.get('sessionId')
        if not sid and method == 'Target.detachedFromTarget':
            sid = params.get('sessionId')
        with self.session_lock:
            target = self.sessions.get(sid)
        if not target or not self.lifecycle.ledger.owns(self.cap, target):
            return
        if message.get('method') == 'Target.attachedToTarget':
            params = message.get('params', {})
            # A nested attachment delivered on an owned page session proves
            # ancestry. It does not confer ownership of another top-level tab.
            with self.session_lock:
                self.sessions[params['sessionId']] = target
        elif message.get('method') == 'Target.detachedFromTarget':
            with self.session_lock:
                self.sessions.pop(message.get('params', {}).get('sessionId'), None)
        elif message.get('method', '').startswith('Target.'):
            return  # no implicit ownership from browser-wide discovery/popups
        if sid == self.page_session:
            message = dict(message)
            message.pop('sessionId', None)
        self.send_event(message)

    def _attach_popup(self, info):
        try:
            with self.lifecycle.ledger.hold(self.cap, self.generation):
                attached = self.attach(info['targetId'])
                self.send_event({'method': 'Target.attachedToTarget',
                                 'params': dict(attached, targetInfo=info, waitingForDebugger=False)})
        except (LifecycleError, self.api.CDPError, OSError):
            return

    def call(self, method, params, session=None):
        with self.lifecycle.ledger.hold(self.cap, self.generation):
            return self._call(method, params, session)

    def _call(self, method, params, session=None):
        with self.lifecycle.ledger.transaction(readonly=True) as data:
            if data['generation'] != self.generation:
                raise LifecycleError('stale browser generation; reconnect required')
        self.lifecycle.ledger.valid(self.cap)
        if not isinstance(params, dict):
            raise LifecycleError('params must be an object')
        session = session or self.page_session
        if session:
            with self.session_lock:
                target = self.sessions.get(session)
            if not target:
                raise LifecycleError('unowned session')
            self.owned(target)
            if method == 'Target.setAutoAttach':
                # Nested auto-attach is scoped by Chrome to this owned page;
                # child sessions inherit its revocation, never a new tab lease.
                return self.cdp.call(method, params, session=session)
            if method.partition('.')[0] not in self.PAGE_DOMAINS or 'targetId' in params or 'sessionId' in params:
                raise LifecycleError('unsupported scoped session method')
            rule = self.api.filter_cdp_message(dict(id=0, method=method, params=params), self.proxy.show())
            if rule and rule[0] == 'reply':
                return {}
            return self.cdp.call(method, params, session=session, timeout=60)
        if method in ('Browser.getVersion', 'Schema.getDomains'):
            return self.cdp.call(method, params)
        if method == 'Target.getBrowserContexts':
            return {'browserContextIds': []}
        if method in ('Target.setDiscoverTargets', 'Target.setAutoAttach'):
            self.cdp.call('Target.setDiscoverTargets', {'discover': True})
            if method == 'Target.setAutoAttach':
                self.auto_attach = bool(params.get('autoAttach'))
            targets = self.call('Target.getTargets', {})['targetInfos']
            for info in targets:
                if method == 'Target.setDiscoverTargets' and params.get('discover'):
                    self.send_event({'method': 'Target.targetCreated', 'params': {'targetInfo': info}})
                elif method == 'Target.setAutoAttach' and self.auto_attach:
                    with self.session_lock:
                        attached = info['targetId'] in self.sessions.values()
                    if not attached:
                        result = self.attach(info['targetId'])
                        self.send_event({'method': 'Target.attachedToTarget',
                                         'params': dict(result, targetInfo=info, waitingForDebugger=False)})
            return {}
        if method == 'Target.getTargets':
            result = self.cdp.call(method, params)
            return {'targetInfos': [t for t in result.get('targetInfos', [])
                                    if self.lifecycle.ledger.owns(self.cap, t['targetId'])]}
        if method == 'Target.createTarget':
            if set(params) - {'url', 'width', 'height', 'newWindow', 'background', 'focus'}:
                raise LifecycleError('unsupported create parameters')
            rule = self.api.filter_cdp_message(dict(id=0, method=method, params=params), self.proxy.show())
            result = self.cdp.call(method, rule[1]['params'] if rule else params)
            tid = result['targetId']
            self.lifecycle.ledger.bind(self.cap, tid, self.generation)
            info = {'targetId': tid, 'type': 'page', 'title': '', 'url': params.get('url', ''), 'attached': False}
            self.send_event({'method': 'Target.targetCreated', 'params': {'targetInfo': info}})
            if self.auto_attach:
                attached = self.attach(tid)
                self.send_event({'method': 'Target.attachedToTarget', 'params': dict(attached, targetInfo=info, waitingForDebugger=False)})
            return result
        if method in ('Target.attachToTarget', 'Target.closeTarget', 'Target.activateTarget', 'Target.getTargetInfo'):
            self.owned(params.get('targetId'))
            if method == 'Target.attachToTarget':
                return self.attach(params['targetId'])
            if method == 'Target.activateTarget' and not self.proxy.show():
                return {}
            return self.cdp.call(method, params)
        if method == 'Target.detachFromTarget':
            sid = params.get('sessionId')
            with self.session_lock:
                target = self.sessions.get(sid)
            if not target:
                raise LifecycleError('unowned session')
            self.owned(target)
            result = self.cdp.call(method, {'sessionId': sid})
            with self.session_lock:
                self.sessions.pop(sid, None)
            return result
        raise LifecycleError('unsupported scoped browser method: ' + str(method))
