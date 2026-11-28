"""Minute collector, singleton advisory flock and explicit shutdown ownership."""
import fcntl
import json
import logging
import os
from pathlib import Path
import socket
import sys
import threading
import time

from . import collectors, github
from .registry import registry
from .store import Store

_log = logging.getLogger(__name__)
_lock = threading.Lock()
_worker = None


def home():
    try:
        from hermes_constants import get_hermes_home
        return Path(get_hermes_home())
    except ImportError:
        return Path(os.environ.get('HERMES_HOME', str(Path.home() / '.hermes')))


def cycle(root, store=None, now=None, include_github=True):
    root = Path(root)
    now = time.time() if now is None else now
    store = store or Store(root/'cache/latency/series.sqlite')
    definitions = registry(root)
    for d in definitions:
        key = 'measured_since:' + d['id']
        if store.meta_get(key) is None:
            store.meta_set(key, now)
    config_path = root/'latency/sources.json'
    config = json.loads(config_path.read_text()) if config_path.exists() else {}
    host = socket.gethostname()
    gateway = config.get('gateway', host)
    # No home/path-derived navigation owners: deployments opt in explicitly.
    profile = config.get('profile')
    errors, counts = {}, {}

    def collect(name, fn):
        try:
            counts[name] = fn()
        except Exception as error:
            # Never persist bodies, queries, credentials or source exceptions.
            errors[name] = type(error).__name__
            _log.warning('latency source %s failed (%s)', name, type(error).__name__)
    since = now-30*86400
    selected = [d for d in definitions if d['source'] == 'state']
    if (root/'state.db').exists() and selected:
        collect('state', lambda: collectors.collect_state(root/'state.db', store, selected, since, now, host, config.get('gateway', 'unknown'), profile))
    boards = config.get('boards')
    if boards is None:
        boards = [root/'kanban.db']
    paths = {}
    for entry in boards:
        settings = entry if isinstance(entry, dict) else {'path': entry}
        path = Path(settings['path']).expanduser().resolve()
        if path.exists():
            paths.setdefault(path, settings)
    selected_board = [d for d in definitions if d['source'] == 'kanban']
    from . import decisions
    selected_decisions = decisions.definitions_for(definitions)
    for i, (path, settings) in enumerate(sorted(paths.items())):
        # Arbitrary external databases do not inherit this home's ownership.
        local = path == (root/'kanban.db').resolve()
        owner_gateway = settings.get('gateway', config.get('gateway', 'unknown') if local else 'unknown')
        owner_profile = settings.get('profile', profile if local else None)
        board = settings.get('board')
        collect('kanban.'+str(i), lambda p=path, g=owner_gateway, pr=owner_profile, b=board:
                collectors.collect_board(p, store, selected_board, since, now, host, g, pr, b))
        decision_board = board or ('default' if local else None)
        if selected_decisions and decision_board:
            collect('decisions.'+str(i), lambda p=path, g=owner_gateway, pr=owner_profile, b=decision_board:
                    decisions.collect(root/'user-steps/actions.sqlite3', p, b, store,
                                      selected_decisions, now, host, g, pr))
    selected_journal = [d for d in definitions if d['source'] in ('request-stages', 'desktop') or d['kind'] == 'span']
    paths = [*root.glob('cache/latency/spans-*.jsonl')]
    if any(d['source'] == 'request-stages' for d in selected_journal):
        paths += list(root.glob('logs/request-stages-*.jsonl*'))
    collect('journals', lambda: collectors.collect_journals(paths, store, selected_journal, now, host, gateway))
    if include_github and config.get('github'):
        collect('github', lambda: github.collect_github(root, store, definitions, config['github'], since, now, host, gateway))
    store.prune(now)
    result = dict(at=now, counts=counts, errors=errors)
    store.meta_set('last_cycle', result)
    return result


class Worker:
    lock_retry_seconds = 60
    def __init__(self, root):
        self.root = Path(root)
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self.run, name='latency-collector', daemon=True)

    def run(self):
        folder = self.root/'cache/latency'
        folder.mkdir(parents=True, exist_ok=True)
        with (folder/'collector.lock').open('a+') as lock:
            while not self.stop_event.is_set():
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    self.stop_event.wait(self.lock_retry_seconds)
            else:
                return
            store = Store(folder/'series.sqlite')
            last_github = float('-inf')
            while not self.stop_event.is_set():
                try:
                    due = time.monotonic()-last_github >= 600
                    if due:
                        # Bound failed attempts too; a source error must not
                        # turn the ten-minute remote scan into a minute loop.
                        last_github = time.monotonic()
                    result = cycle(self.root, store, include_github=due)
                except Exception:
                    _log.exception('latency collector cycle failed')
                self.stop_event.wait(60)

    def stop(self):
        self.stop_event.set()
        if self.thread is not threading.current_thread():
            self.thread.join(timeout=3)


def eligible_process():
    return bool({'serve', 'gateway', 'dashboard'} & set(sys.argv)) or 'tui_gateway.server' in sys.modules


def start(root=None):
    global _worker
    with _lock:
        if _worker is None or not _worker.thread.is_alive():
            _worker = Worker(root or home())
            _worker.thread.start()


def stop():
    global _worker
    with _lock:
        worker, _worker = _worker, None
    if worker:
        worker.stop()
