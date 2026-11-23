"""Process-owned coalescing outbox workers, shared across dashboard router mounts.

The operations database is custody. Events are lossy hints, never a queue.
"""
import contextvars
import logging
import threading
import weakref


class Token:
    pass


_LOCK = threading.Lock()
_WORKERS = {}


class Worker:
    def __init__(self, key, recover, retry, interval):
        self.key = key
        self.wake = threading.Event()
        self.owners = weakref.WeakSet()
        self.configure(recover, retry, interval)
        self.thread = threading.Thread(target=self.run, name='user-steps-retrier', daemon=True)

    def configure(self, recover, retry, interval):
        self.context = contextvars.copy_context()
        self.recover = recover
        self.retry = retry
        # Even disabled automatic retry needs lost-hint/crash recovery. Never
        # busy-loop if configured with zero; scan no faster than once a second.
        self.interval = max(1.0, interval) if interval > 0 else 15.0

    def call(self, fn, ctx):
        try:
            ctx.run(fn)
        except Exception:
            logging.getLogger('user-steps').exception('Decision outbox pass deferred')

    def run(self):
        self.call(self.recover, self.context)
        while True:
            self.wake.wait(self.interval)
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
        new = worker is None
        if new:
            worker = _WORKERS[key] = Worker(key, recover, retry, interval)
        else:
            worker.configure(recover, retry, interval)
        worker.owners.add(token)
        if new:
            worker.thread.start()
        return worker


def release(worker, token):
    with _LOCK:
        worker.owners.discard(token)
        worker.wake.set()
        last = not worker.owners
    if last:
        worker.thread.join(timeout=5)
    return not worker.thread.is_alive()
