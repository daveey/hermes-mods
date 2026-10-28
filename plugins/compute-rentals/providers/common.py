"""Read-only transports. Never expose provider response bodies in errors."""
import json
import os
import selectors
import subprocess
import time
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from types import SimpleNamespace


def now():
    return datetime.now(timezone.utc).isoformat()


def money(value):
    try:
        result = Decimal(str(value))
        return format(result, 'f') if result.is_finite() and result >= 0 else None
    except (InvalidOperation, ValueError):
        return None


def timestamp(value):
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(float(value), timezone.utc).isoformat()
    except (ValueError, TypeError, OverflowError):
        return str(value) if isinstance(value, str) and 'T' in value else None


def state(value):
    return {'running': 'running', 'active': 'running', 'loading': 'starting', 'pending': 'starting',
            'starting': 'starting', 'awaiting_allocation': 'starting', 'stopped': 'stopped',
            'stopping': 'stopped', 'failed': 'error', 'error': 'error'}.get(value, 'unknown')


def machine(provider, ident, account='', region='', **fields):
    row = dict(provider=provider, provider_id=str(ident), account=account, region=region,
               display_name=None, status='unknown', hardware='unknown', started_at=None,
               expires_at=None, hourly_rate=None, currency='USD', rate_basis='unknown',
               console_url=None, ssh=None, observed_at=now())
    row.update(fields)
    row['region'] = row['region'] or 'unknown'
    row['display_name'] = row['display_name'] or str(ident)
    if isinstance(row.get('state_detail'), dict):
        row['state_detail'] = json.dumps(row['state_detail'], sort_keys=True)
    return row


def result(provider, machines, scope, errors=(), unconfigured=False):
    unique = {(m['account'], m['region'], m['provider_id']): m for m in machines}
    return dict(provider=provider, machines=list(unique.values()), scope=scope, observed_at=now(),
                status='unconfigured' if unconfigured else ('partial' if machines else 'error') if errors else 'ok',
                error='; '.join(errors) if errors else None)


MAX_RESPONSE_BYTES = 8 * 1024 * 1024


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward authenticated requests, even to a same-host downgrade.
        return None


# Keep the existing injectable urlopen seam, but bind it to a private safe
# opener. Never install an opener globally: Hermes shares urllib with plugins
# and concurrent tools, whose redirect policies must remain independent.
urllib = SimpleNamespace(
    parse=urllib.parse,
    error=urllib.error,
    request=SimpleNamespace(
        Request=urllib.request.Request,
        urlopen=urllib.request.build_opener(_NoRedirect()).open,
    ),
)


class HttpGet:
    def __init__(self, base, token, interval=0):
        self.base, self.token, self.interval, self.last = base, token, interval, 0

    def __call__(self, path, params=None):
        for attempt in range(3):
            time.sleep(max(0, self.interval - (time.monotonic() - self.last)))
            request = urllib.request.Request(self.base + path + ('?' + urllib.parse.urlencode(params) if params else ''),
                                             headers={'Authorization': 'Bearer ' + self.token, 'Accept': 'application/json'})
            self.last = time.monotonic()
            try:
                with urllib.request.urlopen(request, timeout=25) as response:
                    body = response.read(MAX_RESPONSE_BYTES + 1)
                    if len(body) > MAX_RESPONSE_BYTES:
                        raise ValueError('Response too large')
                    return json.loads(body)
            except urllib.error.HTTPError as exc:
                if exc.code in (429, 500, 502, 503, 504) and attempt < 2:
                    try:
                        delay = float(exc.headers.get('Retry-After', 2 ** attempt))
                    except ValueError:
                        delay = 2 ** attempt
                    time.sleep(min(30, max(0, delay)))
                    continue
                raise RuntimeError('HTTP %s' % exc.code) from None
            except Exception:
                raise RuntimeError('HTTP read failed') from None


def spawn_argv(argv):
    """``argv`` with its program resolved to an absolute path, for ``subprocess`` with ``close_fds=False`` and no
    ``cwd``. With all three, CPython on macOS starts the child with posix_spawn; otherwise it fork()s the whole
    calling process while holding the GIL. Inside a Desktop backend (~2 GB, ~170 threads) each inventory refresh's
    fork held the GIL ~1.6 s per 20 s (2026-10-27). Python opens fds non-inheritable, so ``close_fds=False`` passes
    the child nothing but its stdio. [daveey]"""
    argv = list(argv)
    if argv and not os.path.dirname(argv[0]):
        import shutil
        argv[0] = shutil.which(argv[0]) or argv[0]
    return argv


def command_json(argv, timeout=40, env=None):
    """Bound stdout in memory; discard stderr without logging or disk spooling."""
    proc = None
    try:
        deadline = time.monotonic() + timeout
        proc = subprocess.Popen(spawn_argv(argv), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env,
                                close_fds=False)
        body = bytearray()
        with selectors.DefaultSelector() as selector:
            selector.register(proc.stdout, selectors.EVENT_READ)
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not selector.select(remaining):
                    raise TimeoutError()
                chunk = os.read(proc.stdout.fileno(), min(65536, MAX_RESPONSE_BYTES + 1 - len(body)))
                if not chunk:
                    break
                body.extend(chunk)
                if len(body) > MAX_RESPONSE_BYTES:
                    raise ValueError('Response too large')
        if proc.wait(timeout=max(0, deadline - time.monotonic())):
            raise RuntimeError()
        return json.loads(body)
    except Exception:
        raise RuntimeError('read command failed or timed out') from None
    finally:
        if proc is not None:
            if proc.poll() is None:
                proc.kill()
            proc.wait()
            if proc.stdout is not None:
                proc.stdout.close()

