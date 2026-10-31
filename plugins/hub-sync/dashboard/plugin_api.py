"""/api/plugins/hub-sync — the local hermes-hub replica, for the Desktop (see ../hubview.py, ../README.md).

All reads are local (hub.db on this gateway) and work with the hub down. Without hermes-hub installed every
GET answers 200 with ``available: false`` and empty lists.

GET  /status                               {available, gateway, synced_at, stale, gateways:[{gateway, local, seen_at,
                                            synced_at, profiles, boards}], last_error, requests_pending, ...}
GET  /sessions[?gateway=g&exclude=a,b&archived=1&limit=N]   {sessions:[row + gateway/key/version/local/stale/
                                            synced_at/pending]}
GET  /inbox[?gateway=g&exclude=a,b&all=1]   {items:[...]}   (open/queuing cards unless all=1)
GET  /tasks[?gateway=g&exclude=a,b&board=b&all=1]           {tasks:[...]}
GET  /where/{session_id}                   {hits:[{gateway, profile, session, ref, title, local, stale, synced_at}]}
GET  /session-links/{session_id}[?ids=a,b] {tasks:[{id, title, status, board, via, gateway, local, stale, ...}]}
GET  /requests                             {requests:[...]}  (what this gateway asked others to change)
POST /request  {gateway, coll, key, action, args?, base_version?} -> {id, queued_locally, state}
                                            400 bad action/args, 404 unknown gateway, 503 hermes-hub missing
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import importlib.util
import json
import logging
import os
import select
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import Response

router = APIRouter()
_VIEW = Path(__file__).resolve().parents[1] / "hubview.py"
_WORKER = Path(__file__).resolve().parents[1] / "hubview_worker.py"


def _hv():
    key = "_hermes_hub_sync_view_%s" % int(_VIEW.stat().st_mtime_ns)
    mod = sys.modules.get(key)
    if mod is None:
        spec = importlib.util.spec_from_file_location(key, _VIEW)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod = sys.modules.setdefault(key, mod)
    return mod


def _split(v: Optional[str]):
    return [x.strip() for x in (v or "").split(",") if x.strip()]


# Desktop polls these every few seconds and times out at 4s. One slow sqlite+JSON
# decode must not make every overlapping poll wait it out: return the last snapshot
# for this key while a refresh is in flight.
_snapshots: Dict[Any, Any] = {}
_flights: Dict[Any, asyncio.Task] = {}
_POLL_WAIT_S = 0.75
# Longest a poll with no snapshot waits for its first read (Desktop times out at 4 s).
_COLD_WAIT_S = 2.0
WARMING: Dict[str, Any] = {"available": False, "warming": True}

# The reads run on hub-sync's own pool, not asyncio's shared default executor. At backend boot
# other work fills that executor (min(32, cpu+4) threads, ~370 core call sites), and the
# Desktop's first cold polls, which have no snapshot to fall back on, queued past the 4 s timeout
# even though each read takes under 0.1 s.
#
# The pool threads only relay: each read runs in a hubview_worker.py child (see _Reader) and the thread waits on its
# pipe with the GIL released. One thread per child, so a thread entering always finds its own child free.
_CHILDREN = 2
_EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=_CHILDREN, thread_name_prefix="hub-sync")
_SLOW_LOG_S = 1.0
_BOOT_LOG_S = 60.0
_LOADED_AT = time.monotonic()
log = logging.getLogger(__name__)


class ChildUnavailable(RuntimeError):
    """The read child could not answer (spawn failed, died, timed out, or reported an error)."""


class ChildReadError(ChildUnavailable):
    """The child is healthy but hubview raised; the in-process retry raises the same error as before."""


# The child answers over a socketpair whose buffers hold a whole answer, not a pipe. A macOS pipe holds 64 KB, so a
# 1.35 MB sessions(limit=2000) answer (3.2 MB for tasks) crossed in 21-50 reads, and each read had to win the
# gateway's GIL back from its busy loop thread: with two spinning threads in the parent, sessions-2000 took 0.8-1.7 s
# and tasks 1.8-2.6 s for ~0.06 s of child CPU, while status (one read) stayed at 0.2 s. That is why only the large
# keys logged "slow poll" on 2026-10-30. With an 8 MB buffer the child writes its answer in one go and the relay
# thread takes it in one or two reads. [daveey]
_SOCK_BUF = 8 << 20


def _answer_channel():
    """(parent end, child end) of a stream socketpair with large buffers; buffer sizes are best effort."""
    parent, child = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    for s, opt in ((child, socket.SO_SNDBUF), (parent, socket.SO_RCVBUF)):
        try:
            s.setsockopt(socket.SOL_SOCKET, opt, _SOCK_BUF)
        except OSError:
            pass  # capped by kern.ipc.maxsockbuf / net.core.*mem_max: smaller buffer, more reads, same answer
    return parent, child


class _Child:
    """One hubview_worker.py process, used by one thread at a time (``lock``)."""

    def __init__(self, argv: List[str]):
        self.lock = threading.Lock()
        self.argv = argv
        self.proc: Optional[subprocess.Popen] = None
        self.sock: Optional[socket.socket] = None
        self.sig: Any = None
        self.buf = b""
        self.reads = 0  # os.read calls of the last answer (tests; diagnostics)

    def close(self) -> None:
        p, self.proc, self.buf = self.proc, None, b""
        sock, self.sock = self.sock, None
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
        if p is None:
            return
        try:
            p.kill()
        except OSError:
            pass
        for f in (p.stdin, p.stdout):
            if f is None:
                continue
            try:
                f.close()
            except Exception:  # noqa: BLE001
                pass
        try:
            p.wait(timeout=2)
        except Exception:  # noqa: BLE001
            pass

    def _read_exact(self, n: int, deadline: float, until_newline: bool = False) -> bytes:
        fd = self.sock.fileno()
        while True:
            if until_newline:
                i = self.buf.find(b"\n")
                if i >= 0:
                    out, self.buf = self.buf[:i], self.buf[i + 1:]
                    return out
            elif len(self.buf) >= n:
                out, self.buf = self.buf[:n], self.buf[n:]
                return out
            left = deadline - time.monotonic()
            if left <= 0:
                raise ChildUnavailable("timed out")
            ready, _, _ = select.select([fd], [], [], left)
            if not ready:
                continue
            chunk = os.read(fd, max(1 << 20, min(n - len(self.buf), 1 << 26)))
            self.reads += 1
            if not chunk:
                raise ChildUnavailable("exited (rc=%s)" % self.proc.poll())
            self.buf += chunk

    def call(self, sig: Any, fn: str, args: list, timeout: float) -> bytes:
        if self.proc is not None and (self.sig != sig or self.proc.poll() is not None):
            self.close()  # hubview.py or the worker changed on disk, or the child died: start a fresh one
        if self.proc is None:
            try:
                parent, child = _answer_channel()
            except OSError as e:
                raise ChildUnavailable("socketpair failed: %s" % e) from e
            try:
                self.proc = subprocess.Popen(self.argv, stdin=subprocess.PIPE, stdout=child.fileno(),
                                             stderr=subprocess.DEVNULL, close_fds=True, bufsize=0)
            except OSError as e:
                parent.close()
                raise ChildUnavailable("spawn failed: %s" % e) from e
            finally:
                child.close()  # the child holds its own copy; EOF on ours then means it exited
            self.sock = parent
            self.sig = sig
        try:
            self.proc.stdin.write(json.dumps({"fn": fn, "args": args}).encode() + b"\n")
            self.reads = 0
            deadline = time.monotonic() + timeout
            head = json.loads(self._read_exact(0, deadline, until_newline=True))
            body = self._read_exact(int(head.get("len") or 0), deadline)
        except ChildUnavailable:
            self.close()
            raise
        except (OSError, ValueError) as e:
            self.close()
            raise ChildUnavailable("pipe: %s" % e) from e
        if not head.get("ok"):
            raise ChildReadError("read failed: %s" % head.get("error"))
        return body


class _Reader:
    """hubview reads in child processes (hubview_worker.py), so their CPU never holds the gateway's GIL.

    Any child failure answers that one read in-process (the old path) and keeps children off for
    ``backoff_s``; a hubview.py/worker change on disk replaces the children on their next read."""

    def __init__(self, view: Path, worker: Path, children: int = _CHILDREN, timeout_s: float = 60.0,
                 backoff_s: float = 30.0, python: Optional[str] = None):
        self.view, self.worker = Path(view), Path(worker)
        self.timeout_s, self.backoff_s = timeout_s, backoff_s
        argv = [python or sys.executable, "-u", str(self.worker), str(self.view)]
        self.children = [_Child(argv) for _ in range(max(1, children))]
        self.down_until = 0.0
        self.failures = 0

    def _sig(self):
        try:
            return (self.view.stat().st_mtime_ns, self.worker.stat().st_mtime_ns)
        except OSError:
            return None

    def call(self, fn: str, args: list) -> bytes:
        if time.monotonic() < self.down_until:
            raise ChildUnavailable("backing off after a failure")
        child = next((c for c in self.children if c.lock.acquire(blocking=False)), None)
        if child is None:
            child = self.children[0]
            child.lock.acquire()
        try:
            return child.call(self._sig(), fn, args, self.timeout_s)
        except ChildReadError:
            raise
        except ChildUnavailable as e:
            self.failures += 1
            self.down_until = time.monotonic() + self.backoff_s
            log.warning("hub-sync read child failed (%s: %s); reading in-process for %.0fs",
                        fn, e, self.backoff_s)
            raise
        finally:
            child.lock.release()

    def close(self) -> None:
        for c in self.children:
            with c.lock:
                c.close()


# None = in-process reads only (tests that patch _hv; HUB_SYNC_INPROC=1 as an escape hatch).
_READER: Optional[_Reader] = None if os.environ.get("HUB_SYNC_INPROC") == "1" else _Reader(_VIEW, _WORKER)


def _read(fn: str, *args) -> bytes:
    """One hubview read as JSON bytes: from a child when possible, else in this process. Called on a pool thread."""
    reader = _READER
    if reader is not None:
        try:
            return reader.call(fn, list(args))
        except ChildUnavailable:
            pass
    return json.dumps(getattr(_hv(), fn)(*args), separators=(",", ":"), default=str).encode()


def _respond(result: Any) -> Any:
    """Bytes from _read go out as-is; dicts (WARMING, test fns) go through FastAPI's encoder."""
    if isinstance(result, (bytes, bytearray)):
        return Response(content=bytes(result), media_type="application/json")
    return result


def _default_backlog(loop) -> Any:
    ex = getattr(loop, "_default_executor", None)
    q = getattr(ex, "_work_queue", None)
    try:
        return q.qsize() if q is not None else None
    except Exception:  # noqa: BLE001
        return None


async def _run_timed(key, fn):
    loop = asyncio.get_running_loop()
    entered = time.monotonic()
    started = {}

    def call():
        started["t"] = time.monotonic()
        return fn()

    try:
        return await loop.run_in_executor(_EXECUTOR, call)
    finally:
        done = time.monotonic()
        if entered - _LOADED_AT < _BOOT_LOG_S:
            # Boot window: entry time vs. the Desktop's send time tells a blocked loop from a slow read.
            wall = time.time() - (done - entered)
            log.warning("hub-sync boot poll key=%r entered=%s.%03d total=%.2fs default_backlog=%s",
                        key, time.strftime("%H:%M:%S", time.localtime(wall)), int(wall * 1000) % 1000,
                        done - entered, _default_backlog(loop))
        if done - entered >= _SLOW_LOG_S:
            t0 = started.get("t", done)
            # pid: the gateway, the dashboard and each Desktop serve log to the same agent.log
            log.warning("hub-sync slow poll key=%r total=%.2fs queue=%.2fs run=%.2fs default_backlog=%s pid=%d",
                        key, done - entered, t0 - entered, done - t0, _default_backlog(loop), os.getpid())


async def _cold_answer(flight: asyncio.Task):
    """A key with no snapshot yet waits at most ``_COLD_WAIT_S`` for its first read, then answers
    ``WARMING`` while the read finishes in the background and fills the snapshot. At backend boot
    the first cold reads ran 6.5 s (hubview's decode cache built under boot CPU contention), past
    the Desktop's 4 s timeout. The Desktop treats ``available: false`` as "no answer" and re-polls."""
    try:
        return await asyncio.wait_for(asyncio.shield(flight), timeout=_COLD_WAIT_S)
    except asyncio.TimeoutError:
        return dict(WARMING)


async def _poll(key, fn):
    flight = _flights.get(key)
    last = _snapshots.get(key)
    if flight is not None and not flight.done():
        return last if last is not None else await _cold_answer(flight)

    async def run():
        try:
            result = await _run_timed(key, fn)
            _snapshots[key] = result
            return result
        finally:
            _flights.pop(key, None)

    task = asyncio.create_task(run())
    _flights[key] = task
    if last is None:
        return await _cold_answer(task)
    try:
        return await asyncio.wait_for(asyncio.shield(task), timeout=_POLL_WAIT_S)
    except asyncio.TimeoutError:
        return last


@router.get("/ping")
async def ping() -> Dict[str, Any]:
    return {"ok": True, "api": 1}


@router.get("/status")
async def get_status() -> Dict[str, Any]:
    return _respond(await _poll("status", lambda: _read("status")))


@router.get("/sessions")
async def get_sessions(gateway: Optional[str] = None, exclude: Optional[str] = None, archived: int = 0,
                       limit: int = 500) -> Dict[str, Any]:
    excluded = _split(exclude)
    key = ("sessions", gateway, tuple(excluded), bool(archived), limit)
    return _respond(await _poll(key, lambda: _read("sessions", gateway, excluded, bool(archived), limit)))


@router.get("/inbox")
async def get_inbox(gateway: Optional[str] = None, exclude: Optional[str] = None, all: int = 0) -> Dict[str, Any]:  # noqa: A002
    excluded = _split(exclude)
    key = ("inbox", gateway, tuple(excluded), bool(all))
    return _respond(await _poll(key, lambda: _read("inbox", gateway, excluded, bool(all))))


@router.get("/projects")
async def get_projects(gateway: Optional[str] = None, exclude: Optional[str] = None, preview_limit: int = 3) -> Dict[str, Any]:
    excluded = _split(exclude)
    preview = max(0, min(preview_limit, 50))
    key = ("projects", gateway, tuple(excluded), preview)
    return _respond(await _poll(key, lambda: _read("projects", gateway, excluded, preview)))


@router.get("/tasks")
async def get_tasks(gateway: Optional[str] = None, exclude: Optional[str] = None, board: Optional[str] = None,
                    all: int = 0) -> Dict[str, Any]:  # noqa: A002
    excluded = _split(exclude)
    key = ("tasks", gateway, tuple(excluded), board, bool(all))
    return _respond(await _poll(key, lambda: _read("tasks", gateway, excluded, board, bool(all))))


@router.get("/where/{session_id}")
async def get_where(session_id: str) -> Dict[str, Any]:
    return _respond(await _poll(("where", session_id), lambda: _read("where", session_id)))


@router.get("/session-links/{session_id}")
async def get_session_links(session_id: str, ids: Optional[str] = None, all: int = 0,
                            origin_gateway: Optional[str] = None,
                            origin_profile: Optional[str] = None) -> Dict[str, Any]:  # noqa: A002
    id_list = [session_id] + _split(ids)
    key = ("session-links", tuple(id_list), bool(all), origin_gateway, origin_profile)
    return _respond(await _poll(key, lambda: _read("session_links", id_list, bool(all), origin_gateway,
                                                          origin_profile)))


@router.get("/requests")
async def get_requests(limit: int = 50) -> Dict[str, Any]:
    return _respond(await _poll(("requests", limit), lambda: _read("requests", limit)))


@router.post("/request")
async def post_request(body: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    hv = _hv()
    try:
        return await asyncio.get_running_loop().run_in_executor(_EXECUTOR, hv.request, body)
    except hv.RequestError as e:
        raise HTTPException(status_code=e.status, detail=e.detail)
