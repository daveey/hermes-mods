"""Bounded single-flight view delivery, never a classified-card cache."""
import threading
import time


class SharedViews:
    def __init__(self, ttl=10., clock=time.monotonic):
        self.ttl, self.clock = ttl, clock
        self.condition = threading.Condition()
        self.busy = set()
        self.values = {}  # legal (hours, window, expanded) keys only: at most 32

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
