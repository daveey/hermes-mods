"""Own the default REST view's idle refresh, without extending its validity."""
import logging
import threading


class DefaultWarm:
    """One serial default-key producer; close retains a blocked computation."""

    def __init__(self, cache, epoch, compute, *, interval=60.):
        self.stop = threading.Event()
        self.cache, self.epoch, self.compute = cache, epoch, compute
        self.interval = interval
        self.worker = threading.Thread(target=self._run, name='lanes-default-warm', daemon=True)
        self.worker.start()

    def _run(self):
        while not self.stop.is_set():
            try:
                # Share exact-key serialization with REST and stream callers.
                # Completion, not wakeup, owns the new snapshot timestamp.
                self.cache.get((6., '6h', False), self.epoch(), self.compute)
            except Exception:
                logging.getLogger(__name__).exception('default lanes warm refresh failed')
            if self.stop.wait(self.interval):
                return

    def close(self):
        self.stop.set()
        self.worker.join(timeout=4.)
        if self.worker.is_alive():
            raise RuntimeError('lanes warm compute still draining')
