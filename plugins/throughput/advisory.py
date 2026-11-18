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

    def read(self, request, fresh):
        with self.lock:
            hit = self.snapshot
            if not self.cancel.is_set() and not fresh(hit) and (
                    self.thread is None or not self.thread.is_alive()) and time.monotonic() >= self.retry_at:
                self.thread = threading.Thread(target=self.refresh, args=(request,),
                                               name="throughput-slots", daemon=True)
                self.thread.start()
            return hit

    def refresh(self, request):
        process = None
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
                self.retry_at = time.monotonic() + RETRY_S

    def close(self):
        with self.lock:
            self.cancel.set()
            thread = self.thread
        if thread is not None:
            thread.join()  # refresh owns and reaps its killable DB worker


def read(root, request, fresh):
    with _lock:
        if _stopped:
            return None
        owner = _owners.setdefault(str(root), Owner())
        return owner.read(request, fresh)


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
