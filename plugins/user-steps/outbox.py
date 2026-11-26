"""Process-owned coalescing outbox workers, shared across dashboard router mounts.

The operations database is custody. Events are lossy hints, never a queue.
"""
import contextvars
import logging
import threading
import weakref
import sys
from types import ModuleType


class Token:
    pass


# API hot reload may execute this module again while an older worker is in I/O.
# Keep custody in a process-stable, loader-name-scoped registry. Existing workers
# accept the current mount's callbacks without creating a second executor.
_candidate = ModuleType(__name__ + '_registry')
_candidate.__dict__.update(lock=threading.Lock(), workers={})
_state = sys.modules.setdefault(_candidate.__name__, _candidate)
_LOCK = _state.__dict__['lock']
_WORKERS = _state.__dict__['workers']


class Worker:
    def __init__(self, key, recover, retry, interval):
        self.key = key
        self.wake = threading.Event()
        self.owners = weakref.WeakSet()
        self.configure(recover, retry, interval)
        self.thread = threading.Thread(target=self.run, name='user-steps-retrier', daemon=True)

    def _proceed(self):
        if self.live():
            return True
        with _LOCK:
            if _WORKERS.get(self.key) is self:
                _WORKERS.pop(self.key, None)
        return False

    def configure(self, recover, retry, interval):
        self.context = contextvars.copy_context()
        self.recover = recover
        self.retry = retry
        # Even disabled automatic retry needs lost-hint/crash recovery. Never
        # busy-loop if configured with zero; scan no faster than once a second.
        self.interval = max(1.0, interval) if interval > 0 else 15.0

    def live(self):
        """False once this worker's pinned home is a deleted/missing named profile (never recreate it)."""
        def check():
            try:
                from hermes_constants import assert_named_profile_home_live, get_hermes_home
            except ImportError:  # standalone plugin run: no core fence
                return True
            try:
                assert_named_profile_home_live(get_hermes_home())
            except FileNotFoundError:
                return False
            return True
        return self.context.run(check)

    def call(self, fn, ctx):
        try:
            ctx.run(fn)
        except Exception:
            logging.getLogger('user-steps').exception('Decision outbox pass deferred')

    def run(self):
        if not self._proceed():
            return
        self.call(self.recover, self.context)
        while True:
            self.wake.wait(self.interval)
            if not self._proceed():
                return
            with _LOCK:
                if not self.owners:
                    _WORKERS.pop(self.key, None)
                    return
                self.wake.clear()  # a concurrent commit after capture stays set
                fn, ctx = self.retry, self.context
            self.call(fn, ctx)


def acquire(key, token, recover, retry, interval):
    with _LOCK:
        worker = _WORKERS.get(key)
        new = worker is None or not worker.thread.is_alive()
        if new:
            prior_owners = list(worker.owners) if worker is not None else []
            worker = _WORKERS[key] = Worker(key, recover, retry, interval)
            worker.owners.update(prior_owners)
        else:
            assert worker is not None
            worker.configure(recover, retry, interval)
            # A hot mount can discover newly durable work after startup recovery.
            # Reconfigure must admit a scan now, not wait the previous interval.
            worker.wake.set()
        worker.owners.add(token)
        if new:
            try:
                worker.thread.start()
            except BaseException:
                _WORKERS.pop(key, None)
                raise
        return worker


def release(worker, token):
    with _LOCK:
        worker.owners.discard(token)
        worker.wake.set()
        last = not worker.owners
    if last:
        worker.thread.join(timeout=5)
    return not worker.thread.is_alive()
