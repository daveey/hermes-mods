"""Bound foreground waits without weakening strict projection or ledger fences.

A lane serializes one session's stamps. A timed-out caller leaves its work owned
by the daemon, not by an abandoned executor context (whose shutdown would wait).
ContextVars, including profile/home authority, belong to the submitting turn.
"""
from __future__ import annotations

import contextvars
import threading
import time
from collections import deque
from concurrent.futures import Future, TimeoutError


class Lanes:
    def __init__(self):
        self._lock = threading.Lock()
        self._lanes = {}

    def submit(self, key, call, *, reuse=False, signature=()):
        with self._lock:
            lane = self._lanes.get(key)
            if (lane is not None and reuse and not lane[-1][0].done()
                    and lane[-1][3] == signature):
                return lane[-1][0]
            future = Future()
            job = (future, contextvars.copy_context(), call, signature)
            if lane is not None:
                lane.append(job)
            else:
                lane = deque([job])
                self._lanes[key] = lane
                threading.Thread(target=self._run, args=(key, lane), daemon=True,
                                 name='closeout-hook-work').start()
            return future

    def _run(self, key, lane):
        while True:
            with self._lock:
                future, context, call, _signature = lane[0]
            try:
                result = context.run(call)
            except BaseException as exc:
                future.set_exception(exc)
            else:
                future.set_result(result)
            with self._lock:
                lane.popleft()
                if not lane:
                    del self._lanes[key]
                    return

    def flush(self, key, timeout):
        deadline = time.monotonic() + timeout
        while True:
            with self._lock:
                lane = self._lanes.get(key)
                future = lane[-1][0] if lane else None
            if future is None:
                return True
            try:
                future.result(timeout=max(0, deadline - time.monotonic()))
            except TimeoutError:
                return False
            if time.monotonic() >= deadline:
                with self._lock:
                    return key not in self._lanes
