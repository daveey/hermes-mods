"""Bounded single-flight view delivery, never a classified-card cache."""
import threading
import time


class SharedViews:
    def __init__(self, ttl=10., clock=time.monotonic):
        self.ttl, self.clock = ttl, clock
        self.condition = threading.Condition()
        self.busy = set()
        self.values = {}  # legal (hours, window, expanded) keys only: at most 32

    def get_stale(self, key, epoch, compute):
        """Return a completed view immediately; refresh one exact key off-request.

        A key with no previous view uses the normal single-flight cold path.
        Age is measured from completion, never from refresh start, and response
        metadata is copied so readers cannot mutate the stored view.
        """
        with self.condition:
            while True:
                old = self.values.get(key)
                if old is not None:
                    age = max(0., self.clock() - old[1])
                    if (old[0] != epoch or age >= self.ttl) and key not in self.busy:
                        self.busy.add(key)
                        try:
                            threading.Thread(target=self._refresh, args=(key, epoch, compute),
                                             name='lanes-view-refresh', daemon=True).start()
                        except RuntimeError:
                            self.busy.remove(key)
                            self.condition.notify_all()
                    return dict(old[2], stale_seconds=age)
                if key not in self.busy:
                    self.busy.add(key)
                    break
                # A cold waiter accepts the first completed exact-key view,
                # even if it belongs to another board epoch. Never fall into
                # the strict synchronous get() path after dropping this lock.
                self.condition.wait()
        try:
            value = compute()
            with self.condition:
                self.values[key] = (epoch, self.clock(), value)
            return dict(value, stale_seconds=0.)
        finally:
            with self.condition:
                self.busy.remove(key)
                self.condition.notify_all()

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
