"""Opt-in OTLP/HTTP exporter: bounded, lossy, no retry, never on the request path."""
import json
import queue
import threading
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler

from .otel import to_otlp

_queue = queue.Queue(maxsize=32)
_lock = threading.Lock()
_worker = None


def configured_url():
    try:
        from hermes_cli.config import load_config
        value = (load_config().get('latency') or {}).get('otlp_export_url', '')
    except (ImportError, AttributeError, OSError, ValueError):
        return ''
    if not isinstance(value, str) or len(value) > 2048 or any(ord(c) < 33 for c in value):
        return ''
    parsed = urlsplit(value)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        return ''
    return value


class NoRedirect(HTTPRedirectHandler):
    # Do not let a configured collector redirect private identifiers elsewhere.
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _run():
    opener = build_opener(NoRedirect)
    while True:
        url, body = _queue.get()
        try:
            request = Request(url, data=body, headers={'Content-Type': 'application/json'}, method='POST')
            with opener.open(request, timeout=2) as response:
                response.read(1024)
        except Exception:
            pass  # no payload, URL, error string or credentials in logs
        finally:
            _queue.task_done()


def enqueue(samples):
    global _worker
    url = configured_url()
    if not url:
        return False
    payload = json.dumps(to_otlp(samples), separators=(',', ':'), allow_nan=False).encode()
    if len(payload) > 256*1024:
        return False
    with _lock:
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_run, name='latency-otlp', daemon=True)
            _worker.start()
    try:
        _queue.put_nowait((url, payload))
        return True
    except queue.Full:
        return False
