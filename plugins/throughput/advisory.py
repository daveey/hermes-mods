"""Owned, bounded slots refresh. No database work runs on a throttle request thread.

A subprocess, not a timed-out future, owns the SQLite connections. Timeout/unload
kills and reaps that process before its single-flight coordinator is released.
"""
from __future__ import annotations

import atexit
import json
import subprocess
import sys
import threading
import time
from pathlib import Path

REFRESH_TIMEOUT_S = 2.0
RETRY_S = 1.0
REFRESH_INTERVAL_S = 10.0
ACTIVE_S = 45.0  # covers the Desktop's 30-second polling cadence
_lock = threading.Lock()
_lifecycle = threading.Lock()
_owners = {}
_stopped = False


class Owner:
    def __init__(self):
        self.lock = threading.Lock()
        self.cancel = threading.Event()
        self.thread = None
        self.snapshot = None
        self.retry_at = 0.0
        self.next_at = float("inf")
        self.last_read = 0.0
        self.prepare = None
        self.scheduler = None
        self.wake = threading.Event()

    def launch(self, request):
        """Called under lock; at most one child, including during kill/reap."""
        if (self.cancel.is_set() or time.monotonic() < self.retry_at
                or (self.thread is not None and self.thread.is_alive())):
            return
        self.next_at = float("inf")
        self.thread = threading.Thread(target=self.refresh, args=(request,),
                                       name="throughput-slots", daemon=True)
        self.thread.start()

    def read(self, request, fresh, prepare=None):
        with self.lock:
            hit = self.snapshot
            if self.cancel.is_set():
                return hit
            self.last_read = time.monotonic()
            self.prepare = prepare
            if not fresh(hit):
                self.launch(request)
            if prepare is not None and (self.scheduler is None or not self.scheduler.is_alive()):
                self.scheduler = threading.Thread(target=self.schedule,
                                                   name="throughput-slots-schedule", daemon=True)
                self.scheduler.start()
            self.wake.set()
            return hit

    def schedule(self):
        while not self.cancel.is_set():
            self.wake.clear()
            with self.lock:
                now = time.monotonic()
                active_until = self.last_read + ACTIVE_S
                if now >= active_until:
                    # Clear under the read lock so a simultaneous read can restart us.
                    self.scheduler = None
                    return
                delay = min(max(self.next_at, self.retry_at), active_until) - now
                prepare = self.prepare
                running = self.thread is not None and self.thread.is_alive()
            if delay <= 0 and not running and prepare is not None:
                # Preparation is restricted to clocks and the nonblocking Cards snapshot.
                # Never retain a request's old attribution or timestamp for a future flight.
                request = prepare()
                with self.lock:
                    if time.monotonic() < self.last_read + ACTIVE_S:
                        self.launch(request)
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
                self.retry_at = time.monotonic() + RETRY_S
                # Refresh ahead of 30s expiry and promptly after a minute changes.
                interval = min(REFRESH_INTERVAL_S, 60 - time.time() % 60)
                self.next_at = time.monotonic() + (interval if success else RETRY_S)
            self.wake.set()

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


def read(root, request, fresh, prepare=None):
    with _lock:
        if _stopped:
            return None
        owner = _owners.setdefault(str(root), Owner())
        return owner.read(request, fresh, prepare)


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
