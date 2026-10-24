"""Opt-in browser leases. Stdlib only; raw CDP clients remain unowned.

The ledger is a lifecycle boundary, not a security boundary against the local user
(who can access Chrome's private CDP port and the installation files).
"""
import contextlib
import fcntl
import json
import math
import os
from pathlib import Path
import secrets
import tempfile
import threading
import time


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
    def __init__(self, path, config=None, clock=time.time):
        self.path = Path(path)
        self.config = config or Config()
        self.clock = clock
        self.lock = threading.RLock()

    @staticmethod
    def validate(data):
        def number(value):
            if type(value) not in (int, float) or not math.isfinite(value):
                raise ValueError('invalid timestamp')
        if data['version'] != 1 or not isinstance(data['generation'], str):
            raise ValueError('invalid schema')
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
        for record in data['targets'].values():
            if record['capability'] not in data['leases'] or not isinstance(record['generation'], str):
                raise ValueError('invalid target binding')
            for protection in list(record['protections'].values()) + list(record.get('downloads', {}).values()):
                number(protection['until'])
                if 'max_until' in protection:
                    number(protection['max_until'])
                    number(protection['bytes'])

    @contextlib.contextmanager
    def transaction(self):
        with self.lock, ExclusiveLock(str(self.path) + '.lock', blocking=True):
            try:
                with self.path.open() as stream:
                    data = json.load(stream)
                self.validate(data)
            except FileNotFoundError:
                data = dict(version=1, generation=secrets.token_hex(16), leases={}, targets={})
            except (ValueError, KeyError, TypeError, AttributeError) as exc:
                raise LifecycleError('corrupt lease ledger; refusing destructive operations') from exc
            before = json.dumps(data, sort_keys=True)
            yield data
            if not self.path.exists() or json.dumps(data, sort_keys=True) != before:
                atomic_json(self.path, data)

    def _lease(self, data, cap):
        lease = data['leases'].get(cap)
        if not lease or lease.get('released') is not None:
            raise LifecycleError('invalid or released capability')
        now = self.clock()
        active = any(deadline > now for deadline in lease['requests'].values())
        if not active and now >= lease['activity'] + self.config.idle_lease_s:
            raise LifecycleError('expired capability')
        return lease

    def valid(self, cap):
        with self.transaction() as data:
            self._lease(data, cap)
            return True

    def acquire(self, owner, timeout_s):
        if not isinstance(owner, list) or len(owner) != 3 or any(not isinstance(x, str) or not x or len(x) > 4096 for x in owner):
            raise LifecycleError('owner must be [profile_home, task_id, session]')
        if type(timeout_s) not in (int, float) or not math.isfinite(timeout_s) or not 1 <= timeout_s <= 86400:
            raise LifecycleError('timeout_s must be between 1 and 86400')
        with self.transaction() as data:
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
                cap = secrets.token_urlsafe(32)
                data['leases'][cap] = dict(owner=owner, activity=self.clock(), requests={}, released=None)
            lease = data['leases'][cap]
            lease['activity'] = self.clock()
            request = secrets.token_urlsafe(24)
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
                lease['requests'].clear()

    def bind(self, cap, target):
        with self.transaction() as data:
            self._lease(data, cap)
            old = data['targets'].get(target)
            if old and (old['capability'] != cap or old['generation'] != data['generation']):
                raise LifecycleError('target already bound')
            data['targets'].setdefault(target, dict(capability=cap, generation=data['generation'], protections={}))

    def owns(self, cap, target):
        with self.transaction() as data:
            try:
                self._lease(data, cap)
            except LifecycleError:
                return False
            record = data['targets'].get(target, {})
            return record.get('capability') == cap and record.get('generation') == data['generation']

    def new_generation(self):
        with self.transaction() as data:
            data['generation'] = secrets.token_hex(16)
            data['targets'] = {}

    def protect(self, target, kind, progress=None):
        if kind not in ('handoff', 'download'):
            raise LifecycleError('invalid protection')
        with self.transaction() as data:
            record = data['targets'].get(target)
            if not record or record['generation'] != data['generation']:
                return
            protections = record['protections']
            now = self.clock()
            old = protections.get(kind)
            if kind == 'handoff':
                # Repeat polls/reveals do not extend an existing handoff.
                if old is None:
                    protections[kind] = dict(until=now + self.config.handoff_s)
            elif progress is None:
                protections.pop(kind, None)
            elif old is None:
                protections[kind] = dict(until=now + self.config.download_stall_s,
                                         max_until=now + self.config.download_max_s, bytes=progress)
            elif progress > old['bytes']:
                old.update(bytes=progress, until=min(old['max_until'], now + self.config.download_stall_s))

    def download(self, event, params, target=None):
        guid = params.get('guid')
        if not isinstance(guid, str):
            return
        with self.transaction() as data:
            now = self.clock()
            if event == 'downloadWillBegin':
                record = data['targets'].get(target)
                if record and record['generation'] == data['generation']:
                    record.setdefault('downloads', {}).setdefault(guid, dict(bytes=0,
                        until=now + self.config.download_stall_s, max_until=now + self.config.download_max_s))
                return
            for record in data['targets'].values():
                download = record.get('downloads', {}).get(guid)
                if not download:
                    continue
                if params.get('state') in ('completed', 'canceled'):
                    del record['downloads'][guid]
                elif params.get('receivedBytes', 0) > download['bytes']:
                    download.update(bytes=params['receivedBytes'],
                                    until=min(download['max_until'], now + self.config.download_stall_s))

    def reap(self, live_targets, close):
        closed = []
        with self.transaction() as data:
            now = self.clock()
            for tid, record in list(data['targets'].items()):
                if record['generation'] != data['generation']:
                    continue
                if tid not in live_targets:
                    del data['targets'][tid]
                    continue
                lease = data['leases'][record['capability']]
                expiry = (lease['released'] + self.config.orphan_s if lease['released'] is not None
                          else max([lease['activity'] + self.config.idle_lease_s] + list(lease['requests'].values())))
                protections = list(record['protections'].values()) + list(record.get('downloads', {}).values())
                protected = any(p['until'] > now for p in protections)
                if now >= expiry and not protected and close(tid):
                    closed.append(tid)
                    del data['targets'][tid]
            return closed

    def can_idle(self, live_targets):
        # Never infer ownership of startup tabs, popups or legacy clients' tabs.
        if live_targets:
            return False
        with self.transaction() as data:
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
        self.running = False
        self.disabled = False
        self.empty_since = None

    def control(self, action, payload, endpoint):
        if not isinstance(payload, dict) or payload.get('installation_id') != self.installation_id:
            raise LifecycleError('installation identity mismatch')
        with self.lock:
            if self.disabled:
                raise LifecycleError('installation stopped')
            if action == 'acquire':
                result = self.ledger.acquire(payload.get('owner'), payload.get('timeout_s'))
                try:
                    if not self.running:
                        self.wake()
                        self.running = True
                        # Startup must not consume the caller's execution budget.
                        self.ledger.complete(result['capability'], result['request_id'])
                        result = self.ledger.acquire(payload['owner'], payload['timeout_s'])
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
                return dict(released=True)
            raise LifecycleError('unknown lifecycle action')

    def tick(self, targets, close):
        with self.lock:
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
        """Return false only for legacy raw endpoints. Scoped failures never fall back."""
        if not (target.startswith('/lifecycle/') or target.startswith('/lease/')):
            return False
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

    def discovery(self, proxy, cap, method, path, api):
        import urllib.request
        import urllib.parse
        prefix = 'ws://127.0.0.1:%d/lease/%s' % (proxy.port, cap)
        if method == 'GET' and path == '/json/version':
            version = api.cdp_version(port=proxy.upstream)
            if not version:
                raise LifecycleError('browser is idle or unavailable')
            version['webSocketDebuggerUrl'] = prefix + '/devtools/browser/scoped'
            return version
        if method == 'GET' and path in ('/json', '/json/list', '/json/'):
            with urllib.request.urlopen('http://127.0.0.1:%d/json/list' % proxy.upstream, timeout=5) as response:
                targets = json.load(response)
            owned = [t for t in targets if self.ledger.owns(cap, t['id'])]
            for t in owned:
                t['webSocketDebuggerUrl'] = prefix + '/devtools/page/' + t['id']
                t.pop('devtoolsFrontendUrl', None)
            return owned
        if method == 'PUT' and path.partition('?')[0] == '/json/new':
            # The create response, not a global target-list diff, establishes ownership.
            with self.lock:
                self.ledger.valid(cap)
                target = proxy._new_background(urllib.parse.unquote(path.partition('?')[2]))
                self.ledger.bind(cap, target['id'])
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
        try:
            if page:
                connection.page_session = connection.attach(page)['sessionId']
            accept = base64.b64encode(hashlib.sha1((key + api.WS_GUID).encode()).digest()).decode()
            c.sendall(('HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n'
                       'Sec-WebSocket-Accept: %s\r\n\r\n' % accept).encode())
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
                reply = {'id': message.get('id')}
                if 'sessionId' in message:
                    reply['sessionId'] = message['sessionId']
                try:
                    reply['result'] = connection.call(message['method'], message.get('params', {}), message.get('sessionId'))
                except (LifecycleError, api.CDPError) as exc:
                    reply['error'] = dict(code=-32000, message=str(exc))
                send(reply)
        except (EOFError, OSError):
            return
        finally:
            connection.close()


class ScopedCDP:
    """Terminate scoped CDP rather than forwarding opaque browser/session envelopes."""
    PAGE_DOMAINS = frozenset(('Page', 'Runtime', 'DOM', 'DOMSnapshot', 'CSS', 'Accessibility',
                              'Network', 'Fetch', 'Input', 'Emulation', 'Log', 'Performance',
                              'Debugger', 'Profiler', 'HeapProfiler', 'Overlay', 'WebAuthn'))

    def __init__(self, lifecycle, proxy, capability, api, send_event=lambda m: None):
        self.lifecycle, self.proxy, self.cap, self.api = lifecycle, proxy, capability, api
        with lifecycle.ledger.transaction() as data:
            self.generation = data['generation']
        self.sessions = {}
        self.page_session = None
        self.auto_attach = False
        self.send_event = send_event
        version = api.cdp_version(port=proxy.upstream)
        if not version:
            raise LifecycleError('browser unavailable')
        self.cdp = api.CDP(version['webSocketDebuggerUrl'], on_event=self.event)

    def close(self):
        self.cdp.close()

    def owned(self, tid):
        if not self.lifecycle.ledger.owns(self.cap, tid):
            raise LifecycleError('unowned or expired target')

    def attach(self, tid):
        self.owned(tid)
        result = self.cdp.call('Target.attachToTarget', {'targetId': tid, 'flatten': True})
        self.sessions[result['sessionId']] = tid
        return result

    def event(self, message):
        with self.lifecycle.ledger.transaction() as data:
            if data['generation'] != self.generation:
                return
        sid = message.get('sessionId')
        if sid not in self.sessions or not self.lifecycle.ledger.owns(self.cap, self.sessions[sid]):
            return
        if message.get('method') == 'Target.attachedToTarget':
            params = message.get('params', {})
            # A nested attachment delivered on an owned page session proves
            # ancestry. It does not confer ownership of another top-level tab.
            if params.get('targetInfo', {}).get('type') not in ('iframe', 'worker'):
                return
            self.sessions[params['sessionId']] = self.sessions[sid]
        elif message.get('method') == 'Target.detachedFromTarget':
            self.sessions.pop(message.get('params', {}).get('sessionId'), None)
        elif message.get('method', '').startswith('Target.'):
            return  # no implicit ownership from browser-wide discovery/popups
        if sid == self.page_session:
            message = dict(message)
            message.pop('sessionId', None)
        self.send_event(message)

    def call(self, method, params, session=None):
        with self.lifecycle.lock:
            return self._call(method, params, session)

    def _call(self, method, params, session=None):
        with self.lifecycle.ledger.transaction() as data:
            if data['generation'] != self.generation:
                raise LifecycleError('stale browser generation; reconnect required')
        self.lifecycle.ledger.valid(self.cap)
        if not isinstance(params, dict):
            raise LifecycleError('params must be an object')
        session = session or self.page_session
        if session:
            if session not in self.sessions:
                raise LifecycleError('unowned session')
            self.owned(self.sessions[session])
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
            if method == 'Target.setAutoAttach':
                self.auto_attach = bool(params.get('autoAttach'))
            targets = self.call('Target.getTargets', {})['targetInfos']
            for info in targets:
                if method == 'Target.setDiscoverTargets' and params.get('discover'):
                    self.send_event({'method': 'Target.targetCreated', 'params': {'targetInfo': info}})
                elif method == 'Target.setAutoAttach' and self.auto_attach and info['targetId'] not in self.sessions.values():
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
            with self.lifecycle.lock:
                self.lifecycle.ledger.valid(self.cap)
                rule = self.api.filter_cdp_message(dict(id=0, method=method, params=params), self.proxy.show())
                result = self.cdp.call(method, rule[1]['params'] if rule else params)
                tid = result['targetId']
                self.lifecycle.ledger.bind(self.cap, tid)
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
            if sid not in self.sessions:
                raise LifecycleError('unowned session')
            self.owned(self.sessions[sid])
            result = self.cdp.call(method, {'sessionId': sid})
            del self.sessions[sid]
            return result
        raise LifecycleError('unsupported scoped browser method: ' + str(method))
