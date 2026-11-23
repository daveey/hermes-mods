"""Bounded single-flight view delivery, never a classified-card cache."""
import threading
import time


class SharedViews:
    def __init__(self, ttl=10., clock=time.monotonic):
        self.ttl, self.clock = ttl, clock
        self.condition = threading.Condition()
        self.busy = set()
        self.values = {}  # legal (hours, window, expanded) keys only: at most 32

    def warm(self, key, epoch, compute):
        """Start one exact-key compute without lending the caller's thread."""
        with self.condition:
            if key in self.busy:
                return
            self.busy.add(key)
            try:
                worker = threading.Thread(target=self._refresh, args=(key, epoch, compute),
                                          name='lanes-view-refresh', daemon=True)
                worker.start()
                return worker
            except RuntimeError:
                import logging
                logging.getLogger(__name__).exception('lanes view refresh could not start')
                self.busy.remove(key)
                self.condition.notify_all()

    def get_stale(self, key, epoch, compute, *, max_age=120., cold_wait=1.):
        """Return a completed view immediately; refresh one exact key off-request.

        Cold/over-age keys wait at most cold_wait seconds, then expose only a
        warming marker. Never serve an arbitrarily old value behind a blocked
        refresh, and never start duplicate workers to replace an uncancellable
        Python thread. Use a real monotonic deadline even with an injected clock.
        """
        deadline = time.monotonic() + cold_wait
        with self.condition:
            old = self.values.get(key)
            age = max(0., self.clock() - old[1]) if old is not None else float('inf')
            if old is None or old[0] != epoch or age >= min(self.ttl, max_age):
                self.warm(key, epoch, compute)
            while True:
                old = self.values.get(key)
                if old is not None:
                    age = max(0., self.clock() - old[1])
                    if age < max_age:
                        return dict(old[2], stale_seconds=age)
                remaining = deadline - time.monotonic()
                if remaining <= 0 or key not in self.busy:
                    return {'configured': True, 'warming': True}
                self.condition.wait(remaining)

    def _refresh(self, key, epoch, compute):
        try:
            value = compute()
            with self.condition:
                self.values[key] = (epoch, self.clock(), value)
        except Exception:
            # Failed refreshes retain the previous completed view. A later GET
            # can retry; no failure may leave the single-flight marker stuck.
            import logging
            logging.getLogger(__name__).exception('lanes view refresh failed')
        finally:
            with self.condition:
                self.busy.remove(key)
                self.condition.notify_all()

    def get(self, key, epoch, compute):
        with self.condition:
            while True:
                old = self.values.get(key)
                if old is not None and old[0] == epoch and self.clock() - old[1] < self.ttl:
                    return old[2]
                if key not in self.busy:
                    self.busy.add(key)
                    break
                self.condition.wait()
        try:
            value = compute()
            with self.condition:
                self.values[key] = (epoch, self.clock(), value)
            # Return the snapshot we started, even if the board churned during
            # compute. Next epoch recomputes; retry-until-clean starves forever.
            return value
        finally:
            with self.condition:
                self.busy.remove(key)
                self.condition.notify_all()
