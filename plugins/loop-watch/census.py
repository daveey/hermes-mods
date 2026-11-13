"""Opt-in, one-shot cache counts. Shallow container bytes are NOT retained heap bytes."""
from __future__ import annotations

from collections import OrderedDict
import json
import os
from pathlib import Path
import secrets
import stat
import sys
import threading
import time
import types
import weakref

MAX_AGENTS = 128
MAX_CONTAINERS = 10000
DEADLINE_S = 0.100
MAX_OUTPUT = 4096
COMMAND = b"cache-census-v1\n"
FIELDS = ("_session_messages", "conversation_history")
LOCK_TYPE = type(threading.Lock())


def storage(obj):
    """Read instance storage, never an overridden attribute/property."""
    for cls in type.__getattribute__(type(obj), "__mro__"):
        descriptor = type.__getattribute__(cls, "__dict__").get("__dict__")
        if type(descriptor) is types.GetSetDescriptorType:
            value = descriptor.__get__(obj, type(obj))
            return value if type(value) is dict else {}
        if descriptor is not None:
            break
    return {}


def runner_reference():
    """Only the core's existing weak registry; do not import or search the heap."""
    module = sys.modules.get("gateway.run")
    if type(module) is not types.ModuleType:
        return None
    ref = vars(module).get("_gateway_runner_ref")
    cls = vars(module).get("GatewayRunner")
    if type(ref) is not weakref.ReferenceType:
        return None
    runner = ref()
    return runner if type(runner) is cls else None


def scan(runner=None, *, clock=time.monotonic):
    start = clock()
    end = start + DEADLINE_S
    report = dict(schema=1, kind="cache_census", status="unknown", agents=0,
                  active=0, idle=0, activity_unknown=0, containers=0,
                  shallow_container_bytes=0, visited_values=0,
                  buffer_lengths={field: 0 for field in FIELDS},
                  truncated=False, deadline_exceeded=False, retained_bytes=None)

    def budget():
        if clock() >= end:
            report["deadline_exceeded"] = report["truncated"] = True
            return False
        return True

    data = storage(runner)
    cache = data.get("_agent_cache")
    lock = data.get("_agent_cache_lock")
    sessions = data.get("_sessions")
    if type(cache) not in (dict, OrderedDict) or type(lock) is not LOCK_TYPE:
        return report
    if not lock.acquire(False):
        return report
    seen = set()

    def container(value, depth=0):
        if not budget():
            return
        if report["visited_values"] >= MAX_CONTAINERS:
            report["truncated"] = True
            return
        report["visited_values"] += 1
        typ = type(value)
        if typ not in (list, dict, tuple) or id(value) in seen:
            return
        seen.add(id(value))
        report["containers"] += 1
        # Exact builtins only: no user __sizeof__, repr, keys or string conversion.
        report["shallow_container_bytes"] += sys.getsizeof(value)
        if depth >= 3:
            if len(value):
                report["truncated"] = True
            return
        iterator = iter(value.values()) if typ is dict else iter(value)
        for child in iterator:
            if not budget() or report["visited_values"] >= MAX_CONTAINERS:
                report["truncated"] = True
                break
            container(child, depth + 1)

    try:
        report["status"] = "ok"
        report["cached_entries"] = len(cache)
        for index, (key, entry) in enumerate(cache.items()):
            if index >= MAX_AGENTS or not budget():
                report["truncated"] = True
                break
            if type(entry) is not tuple or not entry:
                report["status"] = "partial"
                continue
            agent = entry[0]
            attrs = storage(agent)
            report["agents"] += 1
            # Same-key SessionState/TurnState raw storage, not legacy dict properties.
            if type(sessions) is dict and type(key) is str:
                state = storage(sessions.get(key))
                turn = storage(state.get("turn"))
                if "agent" in turn:
                    category = "active" if turn["agent"] is not None else "idle"
                elif key not in sessions:
                    category = "idle"
                else:
                    category = "activity_unknown"
            else:
                category = "activity_unknown"
            report[category] += 1
            for field in FIELDS:
                value = attrs.get(field)
                if type(value) in (list, tuple):
                    report["buffer_lengths"][field] += len(value)
                    container(value)
    except RuntimeError:  # concurrent message mutation; no snapshot/copy of private buffers
        report["status"] = "partial"
        report["truncated"] = True
    finally:
        lock.release()
    budget()
    return report


def private_dir(path):
    """Open final directory without following a symlink, then validate its inode."""
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    st = os.fstat(fd)
    if st.st_uid != os.getuid() or stat.S_IMODE(st.st_mode) != 0o700:
        os.close(fd)
        raise OSError("census directory is not owner-only")
    return fd


class Probe:
    """A random process-generation directory grants exactly one request, ever."""
    def __init__(self, home, *, runner=runner_reference, command=COMMAND, scanner=None, max_output=MAX_OUTPUT,
                 subdir="loop-watch-census"):
        root = Path(home) / subdir
        root.mkdir(mode=0o700, exist_ok=True)
        parent = private_dir(root)
        try:
            name = f"{os.getpid()}-{secrets.token_hex(16)}"
            os.mkdir(name, mode=0o700, dir_fd=parent)
            self.fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        finally:
            os.close(parent)
        self.path = root / name
        self.runner = runner
        self.command = command
        self.scanner = scanner
        self.max_output = max_output
        self.used = False
        self.guard = threading.Lock()
        self.created_ns = time.time_ns()
        self.write("ready.json", {"schema": 1, "pid": os.getpid(), "command": command.decode("ascii").strip(),
                                  "request": "request", "output": "result.json"})

    def write(self, name, report):
        payload = json.dumps(report, separators=(",", ":")).encode("ascii")
        if len(payload) > self.max_output:
            payload = b'{"schema":1,"status":"unknown","truncated":true,"output_truncated":true}'
        fd = os.open(name + ".tmp", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=self.fd)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(payload)
            os.rename(name + ".tmp", name, src_dir_fd=self.fd, dst_dir_fd=self.fd)
        finally:
            try:
                os.unlink(name + ".tmp", dir_fd=self.fd)
            except FileNotFoundError:
                pass

    def tick(self):
        if not self.guard.acquire(False):
            return
        try:
            if self.used:
                return
            try:
                # Rename is the claim; only this process-generation directory is considered.
                os.rename("request", "consumed", src_dir_fd=self.fd, dst_dir_fd=self.fd)
            except FileNotFoundError:
                return
            self.used = True  # invalid requests consume the opportunity too; no retry/replay
            fd = os.open("consumed", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=self.fd)
            try:
                st = os.fstat(fd)
                if (not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid()
                        or stat.S_IMODE(st.st_mode) != 0o600 or st.st_nlink != 1
                        or st.st_mtime_ns < self.created_ns or st.st_size != len(self.command)):
                    return
                if os.read(fd, len(self.command) + 1) != self.command:
                    return
            finally:
                os.close(fd)
            self.write("result.json", self.scanner() if self.scanner else scan(self.runner()))
        except OSError:
            pass  # never log private request data or paths
        finally:
            self.guard.release()

    def close(self):
        os.close(self.fd)


def start(home, *, env=None):
    env = os.environ if env is None else env
    if env.get("HERMES_LOOP_WATCH_CACHE_CENSUS") != "1":
        return None
    # Process-wide sentinel survives plugin rediscovery. No forced activation/reload required.
    if getattr(sys, "_hermes_loop_watch_census", False):
        return None
    setattr(sys, "_hermes_loop_watch_census", True)
    try:
        probe = Probe(home)
    except OSError:
        return None

    def run():
        try:
            while not probe.used:
                time.sleep(1)
                probe.tick()
        finally:
            probe.close()
    thread = threading.Thread(target=run, name="loop-watch-census", daemon=True)
    thread.start()
    return thread
