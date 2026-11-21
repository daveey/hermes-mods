"""Owned, bounded slots refresh: at most one flight per sampler minute, only while a client is interested.

No database work runs on a throttle request thread. A subprocess, not a timed-out future, owns the SQLite
connections. Timeout/unload kills and reaps that process before its single-flight coordinator is released.

Cadence (2026-11-20; the Desktop's 15 s ``/v1/throttle`` polls used to keep a 10 s refresh loop warm for good, and
a hub under load timed the worker out and respawned it every ~1.3 s):

* The share window the advisory measures only moves when a minute ends (``slots.collect``: ``until`` is the last
  minute boundary; the sampler writes that minute ~2 s after it). So each owner computes once per minute:
  ``TICK_OFFSET_S`` after the boundary, and at most once for that minute however many reads arrive and whether the
  flight succeeded or failed (a failure waits for the next minute; it is never retried within one). Nothing starts
  in a minute's first ``TICK_OFFSET_S`` (the sampler has not written the minute that just ended).
* Reads do not drive refreshes. A read records interest (``read(..., interest=True)``); a read that finds no usable
  snapshot and no flight yet for its minute starts that minute's flight (cold start). The scheduler thread runs only
  while some read recorded interest in the last ``INTEREST_S``; with none it exits and nothing is computed.
* ``subscribe(fn)``: ``fn(root, ok)`` after every finished flight (the dashboard pushes the new snapshot).
"""
from __future__ import annotations

import atexit
import json
import subprocess
import sys
import threading
import time
from pathlib import Path

REFRESH_TIMEOUT_S = 15.0  # once a minute: a loaded hub needs several seconds just to start the worker
TICK_OFFSET_S = 3.0  # after the sampler's write (``sampler._loop`` wakes 2 s after each minute)
INTEREST_S = 300.0  # compute only while a client read the advisory (or registered interest) in the last 5 min
ACTIVE_S = INTEREST_S
_lock = threading.Lock()
_lifecycle = threading.Lock()
_owners = {}
_listeners = []
_stopped = False


def minute_of(request) -> int:
    return int(float(request["now"]) // 60)


class Owner:
    def __init__(self, root=""):
        self.root = root
        self.lock = threading.Lock()
        self.cancel = threading.Event()
        self.thread = None
        self.snapshot = None
        self.attempted = None  # the minute of the last flight started (success or not): one flight per minute
        self.flights = 0
        self.last_read = float("-inf")
        self.prepare = None
        self.scheduler = None
        self.wake = threading.Event()

    def interested(self) -> bool:
        return time.monotonic() < self.last_read + INTEREST_S

    def launch(self, request, minute=None):
        """Called under lock; at most one child, including during kill/reap, and one per minute (``minute``: the
        minute this flight stands for; default the request's)."""
        minute = minute_of(request) if minute is None else minute
        if (self.cancel.is_set() or (self.attempted is not None and self.attempted >= minute)
                or (self.thread is not None and self.thread.is_alive())):
            return False
        self.attempted = minute
        self.flights += 1
        self.thread = threading.Thread(target=self.refresh, args=(request,),
                                       name="throughput-slots", daemon=True)
        self.thread.start()
        return True

    def read(self, request, fresh, prepare=None, interest=True):
        with self.lock:
            hit = self.snapshot
            if self.cancel.is_set():
                return hit
            if interest:
                self.last_read = time.monotonic()
            if prepare is not None:
                self.prepare = prepare
            if not self.interested():
                return hit  # nobody asked lately: never compute (the pusher's own reads record no interest)
            # Cold start; a no-op once this minute had its flight. Never in the first TICK_OFFSET_S of a minute:
            # the sampler has not written the minute that just ended, and the flight would spend the minute on it.
            if not fresh(hit) and float(request["now"]) % 60 >= TICK_OFFSET_S:
                self.launch(request)
            if self.prepare is not None and (self.scheduler is None or not self.scheduler.is_alive()):
                self.scheduler = threading.Thread(target=self.schedule,
                                                   name="throughput-slots-schedule", daemon=True)
                self.scheduler.start()
            self.wake.set()
            return hit

    def schedule(self):
        while not self.cancel.is_set():
            self.wake.clear()
            with self.lock:
                if not self.interested():
                    # Clear under the read lock so a simultaneous read can restart us.
                    self.scheduler = None
                    return
                wall = time.time()
                due = int((wall - TICK_OFFSET_S) // 60)  # the minute whose tick has passed
                delay = (due + 1) * 60 + TICK_OFFSET_S - wall
                delay = min(delay, self.last_read + INTEREST_S - time.monotonic())
                prepare = self.prepare
                running = self.thread is not None and self.thread.is_alive()
                # Only the current minute's tick, once its offset has passed (a cold start in the first
                # TICK_OFFSET_S waits for this minute's tick instead of computing the last one early).
                pending = due == int(wall // 60) and (self.attempted is None or self.attempted < due)
            if pending and not running and prepare is not None:
                # Preparation is restricted to clocks and the nonblocking Cards snapshot.
                # Never retain a request's old attribution or timestamp for a future flight.
                request = prepare()
                with self.lock:
                    # The flight stands for the due tick, whatever clock the request carries: a request whose
                    # minute lags the wall clock must not leave the tick pending (and this loop spinning).
                    launched = self.interested() and self.launch(request, due)
                if launched:
                    continue
                self.wake.wait(max(0.01, delay))
                continue
            self.wake.wait(max(0.01, delay) if not running else 0.05)

    def refresh(self, request):
        process = None
        success = False
        try:
            started = time.monotonic()
            process = subprocess.Popen([sys.executable, str(Path(__file__).with_name("slots_worker.py"))],
                                       stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                       text=True)
            data = json.dumps(request)
            while not self.cancel.is_set():
                remaining = REFRESH_TIMEOUT_S - (time.monotonic() - started)
                if remaining <= 0:
                    break
                try:
                    output, _ = process.communicate(data, timeout=min(0.05, remaining))
                    if process.returncode == 0:
                        value = json.loads(output)
                        # Published once; never mutated. Callers construct their own response.
                        with self.lock:
                            if not self.cancel.is_set():
                                self.snapshot = (started, value)
                                success = True
                    break
                except subprocess.TimeoutExpired:
                    data = None
        except (OSError, ValueError):
            pass  # missing advisory, never a zero measurement
        finally:
            if process is not None:
                if process.poll() is None:
                    process.kill()
                process.communicate()  # reap and close every pipe before allowing another flight
            with self.lock:
                if not success:
                    self.snapshot = None
                cancelled = self.cancel.is_set()
            self.wake.set()
            if not cancelled:
                _notify(self.root, success)

    def close(self):
        with self.lock:
            self.cancel.set()
            thread = self.thread
            scheduler = self.scheduler
            self.wake.set()
        if scheduler is not None:
            scheduler.join()
        if thread is not None:
            thread.join()  # refresh owns and reaps its killable DB worker


def _notify(root, ok):
    with _lock:
        listeners = list(_listeners)
    for fn in listeners:
        try:
            fn(root, ok)
        except Exception:  # a listener never breaks the refresh
            pass


def subscribe(fn):
    """Call ``fn(root, ok)`` after each finished flight; returns the unsubscribe function."""
    with _lock:
        _listeners.append(fn)

    def off():
        with _lock:
            if fn in _listeners:
                _listeners.remove(fn)
    return off


def read(root, request, fresh, prepare=None, interest=True):
    with _lock:
        if _stopped:
            return None
        owner = _owners.setdefault(str(root), Owner(str(root)))
        return owner.read(request, fresh, prepare, interest)


def start():
    global _stopped
    with _lifecycle:
        with _lock:
            _stopped = False


def stop():
    global _stopped
    # Requests continue to return unavailable while lifecycle operations join.
    # Reload may start a new generation only after every old child is reaped.
    with _lifecycle:
        with _lock:
            _stopped = True
            owners = list(_owners.values())
        for owner in owners:
            owner.close()
        with _lock:
            _owners.clear()


atexit.register(stop)
