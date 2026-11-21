"""Backend-wide bounded projection supervisor; no result cache or watch loop."""
from __future__ import annotations

from collections import OrderedDict
import concurrent.futures
import os
from pathlib import Path
import select
import subprocess
import threading
import time
import uuid


def sibling(name):
    import importlib.util
    spec = importlib.util.spec_from_file_location('lanes_client_' + name, Path(__file__).with_name(name + '.py'))
    assert spec is not None and spec.loader is not None
    obj = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(obj)
    return obj


rpc = sibling('projector_protocol')
identity = sibling('projector_identity')


class Unavailable(RuntimeError):
    pass


class Fallback:
    """One daemon slot, retained until real sync completion, not waiter lifetime."""
    def __init__(self):
        self.lock = threading.Lock()
        self.active = None

    def submit(self, key, compute):
        with self.lock:
            if self.active is not None:
                if self.active[0] == key:
                    return self.active[1]
                raise Unavailable('lanes fallback occupied')
            future = concurrent.futures.Future()
            future.set_running_or_notify_cancel()
            self.active = key, future
        def run():
            try:
                value = compute()
            except BaseException as exc:
                with self.lock:
                    self.active = None
                future.set_exception(exc)
            else:
                with self.lock:
                    self.active = None
                future.set_result(value)
        threading.Thread(target=run, name='lanes-fallback', daemon=True).start()
        return future


class Process:
    """One private session, bounded writes/replies, multiplexed cursor controls."""
    def __init__(self, config, command):
        self.session = uuid.uuid4().hex
        self.lock = threading.Lock()
        self.write_lock = threading.Lock()
        self.pending = {}
        self.ident = 0
        self.ready = concurrent.futures.Future()
        self.expected = config['identity']
        self.proc = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, shell=False, close_fds=True)
        self.closed = False
        threading.Thread(target=self._read, name='lanes-rpc-read', daemon=True).start()
        threading.Thread(target=self._drain, name='lanes-rpc-stderr', daemon=True).start()
        try:
            self._write(config | {'session': self.session}, rpc.REPLY_CAP)
            self.ready.result(5)
        except BaseException:
            self.close()
            raise Unavailable('lanes projector startup failed') from None

    def _write(self, cmd, cap=rpc.COMMAND_CAP):
        raw = rpc.encode(cmd, cap)
        deadline = time.monotonic() + 1
        with self.write_lock:
            fd = self.proc.stdin.fileno()
            os.set_blocking(fd, False)
            while raw:
                left = deadline - time.monotonic()
                if left <= 0 or not select.select([], [fd], [], left)[1]:
                    raise Unavailable('lanes pipe write timeout')
                try:
                    n = os.write(fd, raw)
                    raw = raw[n:]
                except BlockingIOError:
                    pass

    def _read(self):
        try:
            while True:
                raw = self.proc.stdout.readline(rpc.REPLY_CAP + 1)
                reply = rpc.decode(raw, rpc.REPLY_CAP)
                if reply.get('session') != self.session or reply.get('v') != rpc.VERSION:
                    raise Unavailable('lanes session mismatch')
                if not self.ready.done():
                    if reply.get('op') != 'ready' or reply.get('identity') != self.expected:
                        raise Unavailable('lanes identity mismatch')
                    self.ready.set_result(reply)
                    continue
                with self.lock:
                    future = self.pending.pop(reply.get('id'), None)
                if future is None:
                    raise Unavailable('lanes unknown reply')
                if reply.get('op') == 'error':
                    future.set_exception(Unavailable('lanes projector unavailable'))
                else:
                    future.set_result(reply)
        except BaseException:
            error = Unavailable('lanes projector pipe closed')
            if not self.ready.done():
                self.ready.set_exception(error)
            with self.lock:
                pending, self.pending = self.pending, {}
            for future in pending.values():
                if not future.done():
                    future.set_exception(error)

    def _drain(self):
        # Drain forever but retain/log no stderr (including imported-library secrets).
        while self.proc.stderr.read(65536):
            pass

    def request(self, cmd, timeout):
        with self.lock:
            if self.closed or len(self.pending) >= 4:
                raise Unavailable('lanes RPC unavailable')
            self.ident += 1
            cmd = cmd | {'v': rpc.VERSION, 'session': self.session, 'id': self.ident}
            future = concurrent.futures.Future()
            self.pending[self.ident] = future
        try:
            self._write(cmd)
            result = future.result(timeout)
            if cmd['op'] == 'project':
                return rpc.validate_result(result, cmd)
            if result.get('op') != 'cursor' or not isinstance(result.get('cursor'), str) or len(result['cursor']) != 64:
                raise Unavailable('invalid observation cursor')
            return result['cursor']
        except BaseException:
            # Late replies are fenced by terminating this exact process/session.
            self.close()
            raise Unavailable('lanes RPC failed') from None

    def close(self):
        with self.lock:
            if self.closed:
                return
            self.closed = True
        try:
            self.proc.terminate()
            try:
                self.proc.wait(1)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(1)
        finally:
            for pipe in (self.proc.stdin, self.proc.stdout, self.proc.stderr):
                # Read threads own their buffered read handles; closing while a
                # Python read lock is held can block. Process exit supplies EOF.
                if pipe is self.proc.stdin:
                    pipe.close()


class Supervisor:
    def __init__(self, api, manifest):
        self.api, self.manifest = api, manifest
        self.cv = threading.Condition()
        self.queue = OrderedDict()
        self.active = None
        self.process = None
        self.viewers = 0
        self.idle_since = time.monotonic()
        self.retry_at = 0.
        self.failures = 0
        self.stopped = False
        self.last_end = 0.
        self.source_paths = None
        self.fallback = Fallback()
        threading.Thread(target=self._run, name='lanes-projector-supervisor', daemon=True).start()

    def acquire(self):
        with self.cv:
            if self.viewers >= 128:
                raise Unavailable('lanes viewer limit')
            self.viewers += 1

    def release(self):
        with self.cv:
            self.viewers -= 1
            self.idle_since = time.monotonic()
            self.cv.notify_all()

    def submit(self, params, generation, cursor):
        rpc.validate_params(params)
        revision = (generation, rpc.digest(cursor))
        key = tuple(params)
        with self.cv:
            if self.active and self.active[:2] == (key, revision):
                return self.active[2]
            old = self.queue.get(key)
            if old and old[0] == revision:
                return old[1]
            if len(self.queue) >= 32 and key not in self.queue:
                raise Unavailable('lanes pending limit')
            if old:
                # All waiters for this key follow its one latest queued revision.
                # Superseding a queued revision is NOT an error/disconnect for
                # the sockets already waiting on the same parameter key.
                self.queue[key] = (revision, old[1], old[2])
                self.cv.notify_all()
                return old[1]
            future = concurrent.futures.Future()
            future.set_running_or_notify_cancel()
            self.queue[key] = (revision, future, time.monotonic())
            self.cv.notify_all()
            return future

    def cursor(self):
        with self.cv:
            process = self.process
        if process is None or process.closed:
            return None
        try:
            return process.request({'op': 'observation_cursor'}, .75)
        except Unavailable:
            return None

    def _configuration(self):
        from hermes_cli import kanban_db_sync as sync, kanban_worker_node as wn
        from hermes_constants import get_default_hermes_root
        mods = self.api.core()
        if mods is None or not identity.matches(self.manifest):
            raise Unavailable('lanes code identity unavailable')
        with mods[0].connect_closing() as conn:
            local = sync._db_key(conn)
            mode = sync.board_mode(conn)
            candidate = wn._replica_path(conn)
            # Match cli._fleet's intentional absence, not its unreadable-file fallback.
            fleet = str(candidate.resolve()) if candidate.exists() else None
        return {'identity': self.manifest, 'local': local, 'fleet': fleet, 'mode': mode,
                'home': str(get_default_hermes_root())}

    def _spawn(self, config):
        import hermes_cli
        from hermes_cli._launchers import runtime_command
        root = Path(hermes_cli.__file__).resolve().parent.parent
        path = Path(__file__).with_name('projector.py').resolve()
        command = runtime_command(root, code=f"runpy.run_path({str(path)!r}, run_name='__main__')", home=config['home'])
        return Process(config, command)

    def _run(self):
        while True:
            with self.cv:
                while not self.queue and not self.stopped:
                    if self.process and not self.viewers and time.monotonic() - self.idle_since >= 30:
                        self.process.close()
                        self.process = None
                    self.cv.wait(1)
                if self.stopped:
                    return
                gap = self.api.MIN_PROJECT_GAP_S - (time.monotonic() - self.last_end)
                if gap > 0:
                    self.cv.wait(gap)
                    continue
                key, (revision, future, queued) = self.queue.popitem(last=False)
                self.active = key, revision, future
            attempted = False
            try:
                config = self._configuration()
                paths = config['local'], config['fleet']
                if self.process and paths != self.source_paths:
                    self.process.close()
                    self.process = None
                if time.monotonic() >= self.retry_at:
                    attempted = True
                    if self.process is None or self.process.closed:
                        self.process = self._spawn(config)
                        self.source_paths = paths
                    command = rpc.project_command(self.process.session, 0, key, revision[0], None, config['mode'])
                    command['revision'] = list(revision)
                    result = self.process.request(command, max(.01, 10 - (time.monotonic() - queued)))
                    if not identity.matches(self.manifest):
                        raise Unavailable('projection identity moved')
                    self.failures = 0
                    result['mode'] = 'projector'
                else:
                    raise Unavailable('lanes projector retry backoff')
            except Exception:
                if self.process:
                    self.process.close()
                    self.process = None
                if attempted:
                    self.failures += 1
                    self.retry_at = time.monotonic() + min(30, 2 ** min(5, self.failures - 1))
                try:
                    if not identity.matches(self.manifest):
                        raise Unavailable('loaded projection identity unavailable')
                    def compute(key=key, revision=revision):
                        started = time.time()
                        view = self.api._lane_view(*key)  # uncached, current sources
                        if not identity.matches(self.manifest):
                            raise Unavailable('fallback identity moved')
                        return {'view': view, 'projector_pid': None, 'mode': 'fallback',
                                'revision': list(revision), 'params': list(key),
                                'timing': {'project_start': started, 'project_end': time.time()}}
                    job = self.fallback.submit((key, revision), compute)
                    result = job.result(3)
                except Exception:
                    future.set_exception(Unavailable('lanes projection unavailable'))
                    result = None
            with self.cv:
                self.last_end = time.monotonic()
                self.active = None
            if result is not None:
                future.set_result(result)

    def close(self):
        with self.cv:
            self.stopped = True
            for _, future, _ in self.queue.values():
                future.set_exception(Unavailable('lanes supervisor stopped'))
            self.queue.clear()
            self.cv.notify_all()
        if self.process:
            self.process.close()
