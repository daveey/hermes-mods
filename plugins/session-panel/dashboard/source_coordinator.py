"""Explicitly composed, UNACTIVATED shared-source coordinator.

Four existing work slots, reserved by role: local acquisition, strict external
acquisition, and two pure projections. The service is completion driven and does
no SQL. No default runtime caller composes this owner before activation review.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
import logging
import queue
import sys
import threading
import time
from typing import Any
from uuid import uuid4

log = logging.getLogger('session-panel')


class StageCost:
    """Explicit thread-local CPython C-boundary counter, not a syscall tracer.

    Counts SQLite connection/execute and socket/SSL I/O attempts in this thread.
    Existing/replaced profilers remain untouched and produce unavailable evidence,
    never zero costs. Rows/VM steps are supplied only by actual capture adapters.
    """
    def __init__(self):
        self.counters = dict(db_opens=0, sql_calls=0, network_calls=0)
        self.hook = self._profile
        self.reason = 'not_started'

    def start(self):
        if sys.getprofile() is not None:
            self.reason = 'preexisting_profiler'
            return
        self.reason = None
        sys.setprofile(self.hook)

    def _profile(self, frame, event, function):
        if event != 'c_call':
            return
        name = getattr(function, '__name__', '')
        module = getattr(function, '__module__', '')
        if name == 'connect' and module in ('sqlite3', '_sqlite3'):
            self.counters['db_opens'] += 1
        owner = getattr(function, '__self__', None)
        bases = type(owner).__mro__
        if name in ('execute', 'executemany', 'executescript') and any(
                base.__module__ == 'sqlite3' and base.__name__ in ('Connection', 'Cursor') for base in bases):
            self.counters['sql_calls'] += 1
        if name in ('connect', 'connect_ex', 'send', 'sendall', 'sendto', 'sendmsg',
                    'recv', 'recv_into', 'recvfrom', 'recvfrom_into', 'recvmsg', 'recvmsg_into',
                    'read', 'write', 'do_handshake') and any(
                base.__module__ in ('_socket', '_ssl') for base in bases):
            self.counters['network_calls'] += 1

    def finish(self):
        if self.reason is not None:
            return dict(available=False, reason=self.reason)
        if sys.getprofile() is not self.hook:
            return dict(available=False, reason='profiler_replaced')
        sys.setprofile(None)
        return dict(available=True, **self.counters)


class BoardBundle(dict):
    """In-process value carrying the exact projection to Feed's writer fence."""
    def __init__(self, projection, module):
        super().__init__(module.thaw(projection.bundle))
        self.projection = projection


@dataclass
class Attempt:
    stamp: object = None
    demands: object = None
    count: int = 0
    retry_at: float | None = None


@dataclass
class Ticket:
    ready: threading.Event = field(default_factory=threading.Event)
    bundle: object = None
    error: Exception | None = None


def _predecessor_exit_pending(predecessor):
    """Inspect stored old-generation owners without requiring a new method."""
    seen = set()
    while predecessor is not None and id(predecessor) not in seen:
        seen.add(id(predecessor))
        owner = predecessor.owner
        workers = getattr(predecessor, 'workers', None)
        if workers is None:
            workers = (*owner.acquiring.values(), *owner.active.values())
        if not predecessor.remaining and any(w.is_alive() for w in workers):
            return True
        predecessor = predecessor.parent
    return False


def predecessor_ready(predecessor):
    """Current-generation fence even when saved retirement code predates it."""
    return (predecessor is None or
            (predecessor.ready() and not _predecessor_exit_pending(predecessor)))


class RetiringOwner:
    """Compatibility fence for a genuinely older coordinator implementation.

    It need not know today's retire/drained methods. Its completion queue already
    marks work after reader release. Keep every occupied role until thread exit,
    including ancestors still draining across consecutive reloads.
    """
    def __init__(self, owner):
        self.owner = owner
        self.drained = threading.Event()
        with owner.lock:
            self.parent = getattr(owner, 'predecessor', None)
            self.state = {
                'pending': {s: age for s, age in getattr(owner, '_retired_pending', owner.pending).items()
                            if s in owner.projection_attempts},
                'ages': {s: age for s, age in getattr(owner, '_active_ages', {}).items()
                         if s in owner.projection_attempts},
                'active': set(owner.active),
                'acquiring': set(owner.acquiring),
                'chats': dict(owner.chats), 'tokens': dict(owner.tokens),
                'attempts': {key: Attempt(a.stamp, a.demands, a.count, a.retry_at)
                             for key, a in owner.attempts.items()},
                'scope_attempts': {source: {key: Attempt(a.stamp, a.demands, a.count, a.retry_at)
                    for key, a in attempts.items()} for source, attempts in owner.scope_attempts.items()},
                'role_attempts': {key: Attempt(a.stamp, a.demands, a.count, a.retry_at)
                                  for key, a in owner.role_attempts.items()},
                'projection_attempts': {key: Attempt(a.stamp, a.demands, a.count, a.retry_at)
                                        for key, a in owner.projection_attempts.items()},
                'local_stamp': owner._local_stamp, 'external_stamp': owner._external_stamp,
                'local_requested': owner._local_requested, 'external_requested': owner._external_requested,
            }
            self.remaining = {('acquisition_done', key) for key in owner.acquiring}
            self.remaining.update(('projection_done', key) for key in owner.active)
            self.workers = tuple((*owner.acquiring.values(), *owner.active.values()))
            owner.closed = True
            owner.store.close(owner.store.epoch)
            owner.service_stop.set()
            owner.pending.clear()
            for session in list(owner.tickets):
                owner._cancel_tickets(session, 'snapshot owner stopped')
        owner.service_wake.set()

    def ready(self):
        with self.owner.lock:
            while True:
                try:
                    kind, key, value = self.owner.completed.get_nowait()
                except queue.Empty:
                    break
                if kind == 'registration_error' and key in self.state['role_attempts']:
                    attempt = self.state['role_attempts'][key]
                    attempt.count += 1
                    attempt.retry_at = self.owner.feed._backoff(attempt.count)
                self.remaining.discard((kind, key))
            parent_ready = self.parent is None or self.parent.ready()
            if not self.remaining and parent_ready and not _predecessor_exit_pending(self):
                self.drained.set()
        return self.drained.is_set()


    def bind(self, successor):
        # Wake the old scheduler on its original event before handing worker
        # completion notifications to the successor. No watcher thread or poll.
        with self.owner.lock:
            self.owner.successor = successor
            self.owner.service_wake = successor.service_wake


class AwaitingReaders:
    """No input authority while current API registration is unavailable."""
    decision_inputs = None

    def release(self):
        pass

    def __getattr__(self, name):
        raise RuntimeError('current session-panel reader registration unavailable: ' + name)


class PendingSourceOwner:
    """Explicit intent without a current registered reader; no legacy fallback.

    Saved predecessor board loops can retain this handle. Once registration
    installs the real owner, it forwards to that owner without a second loop.
    """
    capacity = 2
    visible_only = False
    pending_install = True
    service_worker = None

    def __init__(self, feed, *, gateway, profile, predecessor, installers=()):
        self.feed, self.gateway, self.profile = feed, gateway, profile
        self.predecessor = predecessor
        self.installers = installers
        self.closed = False
        self.service_wake = feed._board
        self.reader_factory = None
        self.api_generation = None

    def _current(self):
        owner = getattr(self.feed, '_source_coordinator', None)
        return owner if owner is not self else None

    def start_service(self):
        with self.feed.lock:
            self.feed._board_service_required = True
            owner = self._current()
        if owner is not None:
            owner.start_service()

    def retry_at(self):
        owner = self._current()
        if owner is not None:
            return owner.retry_at()
        # The worker's completion wake may precede its actual return. The saved
        # board loop must revisit installation after that exit without API traffic.
        # Pending reloads must also wait for older candidate release, not just
        # retired capture/projection exit. No extra waiter thread is needed.
        return (self.feed.clock() + .01
                if (any(getattr(p, 'installing', False) for p in self.installers)
                    or _predecessor_exit_pending(self.predecessor)) else None)

    def step(self):
        owner = self._current()
        if owner is None and not self.closed and self.reader_factory is not None:
            self.feed.ensure_snapshot_service(self.api_generation, self.reader_factory)
            owner = self._current()
        if owner is not None:
            return owner.step()

    service = step

    def tick(self):
        owner = self._current()
        return owner.tick() if owner is not None else 0

    def full(self, sessions):
        owner = self._current()
        return owner.full(sessions) if owner is not None else {}

    def _admit(self, sessions, at):
        owner = self._current()
        if owner is not None:
            return owner._admit(sessions, at)

    def close(self):
        with self.feed.lock:
            self.closed = True
            owner = self._current()
        if owner is not None:
            owner.close()


class SourceCoordinator:
    capacity = 2
    visible_only = False

    def __init__(self, feed, reader_factory, *, gateway, profile, readers=None):
        self.feed, self.reader_factory = feed, reader_factory
        self.gateway, self.profile = gateway, profile
        rd = feed.readers if readers is None else readers
        modules = rd.snapshot_modules()
        self.capture, self.module = modules, modules.snapshot
        self.store = self.module.SnapshotStore()
        self.sources = tuple(self.module.SourceKey(gateway, profile, board, 'local', str(path))
                             for board, path in rd.boards())
        self.external = self.module.SourceKey(gateway, profile, 'default', 'hub-links',
                                             str(rd.state_db().parent))
        for source in (*self.sources, self.external):
            self.store.add_source(source)
        self.lock = threading.RLock()
        self.completed = queue.SimpleQueue()
        self._completion_pending = False
        self.pending, self.active, self.acquiring = {}, {}, {}
        self._active_ages = {}
        self._queue_generations = {}  # local worker fence, never source authority
        self.attempts = {s: Attempt() for s in (*self.sources, self.external)}
        self.scope_attempts = {s: {} for s in self.attempts}
        self.registration_attempt = Attempt()
        self.role_attempts = {'local': self.registration_attempt, 'external': Attempt()}
        self.projection_attempts = {}
        self.chats, self.tokens, self.bases, self.tickets = {}, {}, {}, {}
        # Exact per-scope raw provenance, keyed by the accepted scope receipt.
        # Not inherited across reload: a new owner must perform its own strict read.
        self.external_raw = {}
        self.canonical_dirty = {}
        self._change_seq = 0
        self.service_wake = threading.Event()
        self.service_stop = threading.Event()
        self.service_worker = None
        self._local_stamp = self._external_stamp = None
        self._local_requested = self._external_requested = True
        self.closed = False
        self.drained = threading.Event()
        self.drained.set()
        self._workers = 0
        self.predecessor = None
        self.successor = None

    def _receipt_log(self):
        return getattr(self.feed, 'receipt_log', None)

    def _record(self, event, **data):
        recorder = self._receipt_log()
        if recorder is not None:
            # Called after source/store locks; never flush/close from this seam.
            recorder.record(event, feed_epoch=self.feed.epoch,
                            coordinator_epoch=self.store.epoch, **data)

    def _admission_receipts(self, admissions, *, current_state, **data):
        for admission in admissions:
            self._record('admitted', admission=admission, chat=admission.chat,
                         current_state=current_state, **data)

    def inherit(self, retirement):
        """Use B1's compatible stopped-store transfer; never copy live workers.

        Retry signatures are rehomed with the immutable keys, not reset because
        the Python module has a new content-qualified identity. Reader objects,
        role ownership and canonical input bases are deliberately not inherited.
        """
        from collections.abc import Mapping
        from dataclasses import is_dataclass
        def value(v) -> Any:
            if is_dataclass(v):
                return self.module.rehome(v)
            if isinstance(v, Mapping):
                return {value(k): value(x) for k, x in v.items()}
            if isinstance(v, tuple):
                return tuple(value(x) for x in v)
            if isinstance(v, (set, frozenset)):
                return frozenset(value(x) for x in v)
            # File-stamp primitives and in-process Feed watch-token identities.
            return v
        def attempt(a):
            return Attempt(value(a.stamp), value(a.demands), a.count, a.retry_at)
        state = retirement.state
        chats = value(state['chats'])
        attempts = {value(k): attempt(a) for k, a in state['attempts'].items()}
        scopes = {value(k): {value(s): attempt(a) for s, a in group.items()}
                  for k, group in state['scope_attempts'].items()}
        roles = {k: attempt(a) for k, a in state['role_attempts'].items()}
        projections = {k: attempt(a) for k, a in state['projection_attempts'].items()}
        stopped = retirement.owner.store
        prior_sources = self.module.rehome(tuple(stopped._versions))
        if len(set(prior_sources) | set(self.sources) | {self.external}) > stopped._limits[1]:
            raise self.module.SnapshotCapacity('compatible transfer source capacity exceeded')
        store = self.module.SnapshotStore.transfer(stopped)
        # Newly discovered sources are missing, not inferred from transferred
        # coverage. Unchanged source keys retain their exact immutable versions.
        for source in (*self.sources, self.external):
            store.add_source(source)
            attempts.setdefault(source, Attempt())
            scopes.setdefault(source, {})
        self.store, self.chats = store, chats
        self.tokens = dict(state['tokens'])
        self.attempts, self.scope_attempts = attempts, scopes
        self.role_attempts = roles
        self.registration_attempt = roles['local']
        self.projection_attempts = projections
        self._local_stamp, self._external_stamp = value(state['local_stamp']), value(state['external_stamp'])
        self._local_requested = state['local_requested'] or 'local' in state['acquiring']
        self._external_requested = state['external_requested'] or 'external' in state['acquiring']
        # A fenced in-flight capture has already consumed its attempt. Resume
        # gives that attempt its existing finite backoff, not a fresh budget.
        for source, group in scopes.items():
            role = 'external' if source == self.external else 'local'
            if role in state['acquiring']:
                for a in group.values():
                    if a.count and a.retry_at is None:
                        a.retry_at = self.feed._backoff(a.count)
                retries = [a.retry_at for a in group.values() if a.retry_at is not None]
                attempts[source].retry_at = min(retries) if retries else None
        self.pending = dict(state['pending'])
        self._active_ages = dict(state['ages'])
        for s in projections:
            if s in self.chats:
                age = self.pending.get(s, self._active_ages.get(s,
                    store.pin(self.chats[s])[1].first_admitted_mono_ns))
                self._active_ages[s] = age
                if s in state['active']:
                    self.pending.setdefault(s, age)
                    a = projections[s]
                    if a.count and a.retry_at is None:
                        # Fenced in-flight retry consumed its attempt just like
                        # acquisition above. Preserve budget/age and give it the
                        # existing bounded delay, not an eternal in-flight mark.
                        a.retry_at = self.feed._backoff(a.count)
        # Forget/rewatch still wins over copied identities; _register handles its
        # exact token/lineage transition before any capture can claim coverage.
        self.canonical_dirty = {s: i for i, s in enumerate(self.chats, 1)}
        self._change_seq = len(self.canonical_dirty)

    def _available(self):
        # An inherited retirement object can implement an older ready() which
        # consumes completion before actual thread exit. Its reported readiness
        # is necessary, never sufficient, for successor capacity admission.
        return predecessor_ready(self.predecessor)

    def _worker_started(self):
        self._workers += 1
        self.drained.clear()

    def _worker_finished(self):
        # After reader release and all result bookkeeping: transfer capacity,
        # never connections or source authority, to a waiting successor.
        with self.lock:
            self._workers -= 1
            if not self._workers:
                self.drained.set()
            successor = self.successor
        if successor is not None:
            successor.service_wake.set()
            successor.feed._board.set()

    def start_service(self):
        with self.lock:
            if self.service_worker is not None or self.closed or self.feed.stopped.is_set():
                return
            self.service_worker = threading.Thread(target=self._serve,
                name='session-panel-card-service', daemon=True)
            self.service_worker.start()
        self.service_wake.set()

    def _serve(self):
        while not self.service_stop.is_set() and not self.feed.stopped.is_set():
            at = self.retry_at()
            self.service_wake.wait(None if at is None else max(0., at - self.feed.clock()))
            self.service_wake.clear()
            if not self.service_stop.is_set() and not self.feed.stopped.is_set():
                self.step()

    def retry_at(self):
        with self.lock:
            if not self._available():
                # Inherited work cannot run until predecessor capacity retires.
                # Expired source deadlines must not spin the service behind it.
                return (self.feed.clock() + .01
                        if _predecessor_exit_pending(self.predecessor) else None)
            ats = []
            if self._completion_pending:
                # No join under this lock: the worker still needs it to exit.
                # Reuse the existing service loop for bounded exit observation,
                # never another thread or a renewed source/projection attempt.
                ats.append(self.feed.clock() + .01)
            for source, attempt in self.attempts.items():
                role = 'external' if source == self.external else 'local'
                owner = self.role_attempts[role]
                if (attempt.retry_at is None or role in self.acquiring
                        or (owner.count and owner.retry_at is None)):
                    continue
                # A source deadline cannot wake earlier than its owning reader
                # role can actually run, or spin behind an exhausted role.
                ats.append(max(attempt.retry_at, owner.retry_at or attempt.retry_at))
            ats += [a.retry_at for s, a in self.projection_attempts.items()
                    if a.retry_at is not None and s not in self.active]
            ats += [a.retry_at for role, a in self.role_attempts.items()
                    if a.retry_at is not None and role not in self.acquiring]
            return min(ats) if ats else None

    def _cancel_tickets(self, session, reason):
        for ticket in self.tickets.pop(session, []):
            ticket.error = RuntimeError(reason)
            ticket.ready.set()

    def _queue(self, sessions):
        for s in sessions:
            if s in self.chats:
                self._queue_generations[s] = self._queue_generations.get(s, 0) + 1
                admission = self.store.pin(self.chats[s])[1]
                signature = (admission.required_versions, admission.required_scopes,
                             admission.required_failures, admission.missing_versions)
                previous = self.projection_attempts.get(s)
                if previous is None or previous.demands != signature:
                    self.projection_attempts[s] = Attempt(demands=signature)
                self.pending.setdefault(s, self._active_ages.get(s, time.monotonic_ns()))
                self.feed._board_failures.add(s)
                if not self._projection_ready(s):
                    self._cancel_tickets(s, 'snapshot projection retry is pending or exhausted')

    def _admit(self, sessions, at):
        """Legacy keyed-hint seam; acquisition and projection keep one owner.

        `at` belongs to the legacy lane's timing. This owner retains its existing
        monotonic first-unserved ages and exact-source retry budgets instead.
        Newly watched sessions are registered by the reserved local slot.
        """
        with self.lock:
            if self.closed or self.feed.stopped.is_set():
                raise RuntimeError('snapshot coordinator is closed')
            self._queue(sessions)
            self._local_requested = self._external_requested = True
        self.service_wake.set()
        self.feed._board.set()

    def _projection_ready(self, session):
        attempt = self.projection_attempts[session]
        return (not attempt.count or
                (attempt.retry_at is not None and self.feed.clock() >= attempt.retry_at))

    def _register(self, rd):
        # Runs only in the single local acquisition slot. Lineage is source
        # acquisition, never part of per-chat board projection.
        with self.feed.lock:
            wanted = dict(self.feed._watch_tokens)
        lineages = {s: tuple(rd.lineage(s) or [s]) for s in wanted}
        forgotten, registered = [], []
        with self.lock:
            if self.closed:
                return
            for s in list(self.chats):
                if wanted.get(s) is not self.tokens[s]:
                    chat = self.chats.pop(s)
                    watch = self.store.pin(chat)[0].watches[chat]
                    self.store.forget(chat, watch.opaque_watch_token)
                    forgotten.append(watch)
                    self.tokens.pop(s, None)
                    self.bases.pop(s, None)
                    self.pending.pop(s, None)
                    self._active_ages.pop(s, None)
                    self._queue_generations.pop(s, None)
                    self.projection_attempts.pop(s, None)
                    self._cancel_tickets(s, 'snapshot watch was forgotten or replaced')
            for s, token in wanted.items():
                if self.feed._watch_tokens.get(s) is not token:
                    continue
                ids = lineages[s]
                chat = self.module.ChatKey(self.gateway, self.profile, s)
                changed = self.store.register([self.module.Watch(chat, '', ids, 0)])
                self.chats[s], self.tokens[s] = chat, token
                if changed:
                    registered.append(chat)
                    self._queue([s])
                    self._changed([s])
            demands = self.store.demands(self.external)
            self.external_raw = {sid: value for sid, value in self.external_raw.items() if sid in demands}
            state = self.store.receipt_state() if self._receipt_log() is not None else None
        if state is not None:
            for watch in forgotten:
                self._record('forgotten', chat=watch.chat, watch=watch, current_state=state)
            self._admission_receipts((state['admissions'][c] for c in registered),
                                     current_state=state, reason='registration')

    def require(self, session, refs):
        """Exact owner-qualified dependency expansion, never an ID-only lookup."""
        with self.lock:
            changed = self.store.require(self.chats[session], refs)
            if changed:
                self._queue([session])
                self._local_requested = self._external_requested = True
            state = self.store.receipt_state() if changed and self._receipt_log() is not None else None
            chat = self.chats[session]
        if state is not None:
            self._admission_receipts([state['admissions'][chat]], current_state=state, reason='refs_changed')
        self.service_wake.set()
        self.feed._board.set()
        return changed

    def _changed(self, sessions):
        for s in sessions:
            self._change_seq += 1
            self.canonical_dirty[s] = self._change_seq
        self.feed._wake.set()

    def _eligible(self, source, stamp):
        demands = self.store.demands(source)
        a = self.attempts[source]
        scopes = self.scope_attempts[source]
        for sid in set(scopes) - set(demands):
            scopes.pop(sid)
        for sid, demand in demands.items():
            previous = scopes.get(sid)
            if previous is None or previous.stamp != stamp or previous.demands != demand:
                scopes[sid] = Attempt(stamp=stamp, demands=demand)
        a.stamp, a.demands = stamp, demands
        return any(v.count == 0 or (v.retry_at is not None and self.feed.clock() >= v.retry_at)
                   for v in scopes.values())

    def _settle_attempts(self, source, attempted, unknown):
        scopes = self.scope_attempts[source]
        for sid, attempt in scopes.items():
            if sid not in unknown:
                attempt.retry_at = None
            elif sid in attempted:
                attempt.retry_at = self.feed._backoff(attempt.count)
        retries = [v.retry_at for v in scopes.values() if v.retry_at is not None]
        self.attempts[source].retry_at = min(retries) if retries else None

    def step(self):
        if self.closed or self.feed.stopped.is_set():
            return
        self.feed._board.clear()
        self.service()
        rd = self.feed.readers
        local_stamp = rd.board_stamps()
        external_stamp = (local_stamp, rd.external_stamps())
        state_stamp = rd._stat(rd.state_db())
        with self.feed.lock:
            membership = dict(self.feed._watch_tokens)
        with self.lock:
            if self.closed:
                return
            # A recovered reader role must be re-admitted for a genuinely new
            # exact scope even when source files and membership stay unchanged.
            # Source-owned per-scope attempts still retain unchanged peer budgets.
            demand_stamp = frozenset(self.store.demands(self.external).values())
            registration_stamp = (local_stamp, state_stamp, tuple(membership.items()), demand_stamp)
            reg = self.registration_attempt
            if reg.stamp != registration_stamp:
                reg.stamp, reg.count, reg.retry_at = registration_stamp, 0, None
                self._local_requested = True
            if external_stamp != self._external_stamp:
                self._external_requested = True
            external_role = self.role_attempts['external']
            external_role_stamp = (external_stamp, tuple(membership.items()), demand_stamp)
            if external_role.stamp != external_role_stamp:
                external_role.stamp, external_role.count, external_role.retry_at = external_role_stamp, 0, None
            self._local_stamp, self._external_stamp = local_stamp, external_stamp
            self._launch('local', self._local_requested, self._acquire_local)
            # Local registration establishes the frozen scope set first. It does
            # not wait for external capture and subsequent local work is independent.
            if self.chats:
                self._launch('external', self._external_requested, self._acquire_external)
        self.service()

    def _launch(self, role, requested, target):
        if self.closed or not self._available() or role in self.acquiring:
            return
        reg = self.role_attempts[role]
        if reg.count and (reg.retry_at is None or self.feed.clock() < reg.retry_at):
            return
        requested = requested or reg.retry_at is not None
        sources = self.sources if role == 'local' else (self.external,)
        stamp = self._local_stamp if role == 'local' else self._external_stamp
        due = any(a.retry_at is not None and self.feed.clock() >= a.retry_at
                  for s, a in self.attempts.items() if s in sources)
        missing = any(self.store.pending(s) and self.attempts[s].demands != self.store.demands(s)
                      for s in sources)
        if not requested and not due and not missing:
            return
        if role == 'local':
            self._local_requested = False
        else:
            self._external_requested = False
        worker = threading.Thread(target=self._acquisition, args=(role, target, stamp),
                                  name='session-panel-source-' + role, daemon=True)
        self.acquiring[role] = worker
        self._worker_started()
        worker.start()

    def _acquisition(self, role, target, stamp):
        rd = None
        try:
            rd = self.reader_factory()
            if role == 'local':
                self._register(rd)
            target(rd, stamp)
            rd.release()
            rd = None
            with self.lock:
                if not self.closed:
                    self.role_attempts[role].count = 0
                    self.role_attempts[role].retry_at = None
        except Exception as exc:
            log.warning('session-panel %s acquisition failed', role, exc_info=True)
            self.completed.put(('registration_error', role, exc))
        finally:
            if rd is not None:
                try:
                    rd.release()
                except Exception:
                    log.warning('session-panel source reader release failed', exc_info=True)
            self.completed.put(('acquisition_done', role, None))
            self.service_wake.set()
            self.feed._board.set()
            self._worker_finished()

    def _remember_external(self, snapshot, raw):
        part = snapshot.parts[self.external]
        current = self.store.demands(self.external)
        self.external_raw = {sid: value for sid, value in self.external_raw.items() if sid in current}
        for sid, encoded in raw.captured.items():
            receipt = part.scope_receipts.get(sid)
            if (current.get(sid) == raw.demands[sid] and receipt is not None
                    and receipt.coverage.captured_demands.get(sid) == raw.demands[sid]):
                self.external_raw[sid] = (receipt.version, tuple(encoded),
                                         raw.inventory.get('dependency_inputs', {}).get(sid))

    def _observe_external(self, rd, raw, previous, prior):
        """Clock-only strict read, no hydration, acquisition age or new coverage.

        The unchanged part CAS advances the capture sequence while preserving all
        scoped receipts. It fences an older in-flight strict read even when a pull
        legitimately regresses. Feed's exact parent-token CAS separately orders
        presentation. Neither fence is based on timestamp ordering.
        """
        if (previous is None or previous.receipt.owner_token != raw.hints.owner_token
                or previous.receipt.version.store_epoch_uuid != prior.store_epoch_uuid
                or previous.receipt.version.source_generation != prior.source_generation
                or previous.receipt.coverage.captured_demands != raw.demands
                or set(previous.scope_receipts) != set(raw.demands)
                or previous.tombstones or previous.invalidated_keys):
            return False
        with self.feed.lock:
            projections = {sid: (self.feed.bundles.get(sid.chat.session_id) or {}).get('board_projection')
                           for sid in raw.demands}
        observations = {}
        for sid, captured in raw.captured.items():
            p = projections[sid]
            if (p is None or not p.external_capture or self.external not in p.covered_scopes
                    or p.feed_epoch != self.feed.epoch or p.feed_owner is not self.feed._projection_owner
                    or p.watch_token is not self.tokens.get(sid.chat.session_id)
                    or sid not in self.external_raw or len(self.external_raw[sid]) != 3
                    or self.external_raw[sid][2] != raw.inventory.get('dependency_inputs', {}).get(sid)
                    or p.covered_scopes[self.external] != raw.demands[sid]
                    or not self.store.current(p.admission)
                    or rd.external_semantic(captured) != rd.external_semantic(p.external_capture)):
                return False
            observations[sid.chat.session_id] = self.feed.carry_board_observations(self.store, p, captured)
        with self.lock:
            if self.closed:
                raise self.module.StaleCapture('source owner retired')
            if self.store.demands(self.external) != raw.demands:
                raise self.module.StaleCapture('external scope changed during strict read')
            # No new certificate: every receipt/row/detail/execution clock stays
            # exactly as before. Only the store's acceptance sequence changes.
            part = replace(previous, receipt=replace(previous.receipt,
                version=replace(prior, source_revision=prior.source_revision + 1)))
            snapshot, admissions = self.store.accept_external(part, prior)
            if admissions:
                raise RuntimeError('observation-only carriage unexpectedly admitted semantic work')
            self._remember_external(snapshot, raw)
        results = self.feed.publish_projections(self.store, observations)
        with self.lock:
            if not self.closed:
                # A concurrent writer may win the parent token after strict read.
                # Retain the raw capture and retry through existing pure slots;
                # never reacquire merely to recover this presentation race.
                self._queue(s for s, result in results.items()
                            if result.disposition not in {'stored', 'no_change'})
        return True

    def _capture_one(self, rd, source, stamp):
        with self.lock:
            if self.closed or not self._eligible(source, stamp):
                return
            demands = self.store.demands(source)
            if not demands:
                return
            snapshot = self.store.pin(next(iter(demands)).chat)[0]
            previous = snapshot.parts.get(source)
            prior = self.store.version(source)
            hint = self.capture.CaptureHints(prior, self.store.owner_token(source), self.feed.clock())
            a = self.attempts[source]
            a.count += 1
            a.retry_at = None
            attempted = set()
            for sid, attempt in self.scope_attempts[source].items():
                if attempt.count == 0 or (attempt.retry_at is not None and self.feed.clock() >= attempt.retry_at):
                    attempted.add(sid)
                    attempt.count += 1
                    attempt.retry_at = None
        measured = self._receipt_log() is not None
        receipt_data = dict(capture_attempt_id=uuid4().hex, source=source,
            domain='external' if source == self.external else 'local',
            frozen_demands=demands, input_stamp=stamp, prior_version=prior,
            owner_token=hint.owner_token) if measured else {}
        started = time.monotonic_ns() if measured else 0
        cpu = time.thread_time_ns() if measured else 0
        disposition, part, failure, raw = 'error', None, None, None
        cost = StageCost() if measured else None
        if measured:
            self._record('capture_start', **receipt_data)
            assert cost is not None
            cost.start()
        try:
            if source == self.external:
                raw = self.capture.capture_external_raw(
                    rd, demands, {}, {source: hint},
                    previous_keys=previous.scope_keys if previous else {})
                if self._observe_external(rd, raw, previous, prior):
                    with self.lock:
                        self._settle_attempts(source, attempted, self.store.pending(source))
                    disposition = 'observation_only'
                    return
                parts = rd.capture_external(demands, {}, snapshot.parts, hints={source: hint}, raw=raw)
                part, = parts
                with self.lock:
                    accepted, admissions = self.store.accept_external(part, prior)
                    self._remember_external(accepted, raw)
            else:
                part = rd.capture_local(source, previous, demands, hint)
                accepted, admissions = self.store.accept_local(part, prior)
            disposition = 'accepted'
            with self.lock:
                if self.closed:
                    return
                # Success does not renew a failure budget; only changed source /
                # exact demand does. Unknown returned coverage is failed work.
                unknown = self.store.pending(source)
                self._settle_attempts(source, attempted, unknown)
                if source == self.external:
                    self.feed._board_capture_failures.update(sid.chat.session_id for sid in unknown)
                self._queue(ad.chat.session_id for ad in admissions)
                self._changed(ad.chat.session_id for ad in admissions)
                self.completed.put(('source', source, part))
                state = self.store.receipt_state() if measured else None
            if measured:
                changed = set(part.tombstones) | set(part.invalidated_keys)
                for name in ('rows', 'details', 'edges'):
                    old = getattr(previous, name) if previous is not None else {}
                    new = getattr(part, name)
                    changed.update(k for k in set(old) | set(new) if old.get(k) != new.get(k))
                if previous is not None and previous.remote_parents != part.remote_parents:
                    changed.update(previous.rows)
                self._record('source_received', **receipt_data, receipt=part.receipt,
                    current_state=state, metrics=part.metrics, changed_keys=frozenset(changed),
                    old_membership=previous.memberships if previous is not None else (),
                    new_membership=accepted.parts[source].memberships,
                    elapsed_ns=time.monotonic_ns() - started,
                    thread_cpu_ns=time.thread_time_ns() - cpu)
                self._admission_receipts(admissions, current_state=state, **receipt_data)
        except self.module.StaleCapture:
            disposition = 'superseded'
            # A losing CAS must not poison a newer accepted source. A reset or
            # demand change can also refuse the capture without a replacement
            # success, however: settle still-unknown work on the existing finite
            # retry schedule rather than stranding it with retry_at=None. Do not
            # mutate a newer attempt's signature/budget.
            with self.lock:
                if not self.closed and self.attempts[source].stamp == stamp:
                    self._settle_attempts(source, attempted, self.store.pending(source))
            return
        except Exception as exc:
            failure = type(exc).__name__
            failed_admissions, state = (), None
            with self.lock:
                if not self.closed:
                    accepted = self.store.fail(source, prior.source_generation, exc, demands, prior_version=prior)
                    self._settle_attempts(source, attempted, set(demands))
                    if accepted:
                        self._queue(d.id.chat.session_id for d in demands.values())
                        if source == self.external:
                            self.feed._board_capture_failures.update(d.id.chat.session_id for d in demands.values())
                        if measured:
                            state = self.store.receipt_state()
                            failed_admissions = tuple(state['admissions'][sid.chat] for sid in demands
                                                      if sid.chat in state['admissions'])
            if state is not None:
                self._record('source_error', **receipt_data, current_state=state, error=failure)
                self._admission_receipts(failed_admissions, current_state=state, **receipt_data)
            log.warning('session-panel source capture failed: %s', source.producer, exc_info=True)
        finally:
            if measured:
                assert cost is not None
                io_cost = cost.finish()
                if io_cost.get('available') is True and part is not None:
                    # These are actual adapter counters, not inferred zeros for
                    # adapters which cannot supply rows/VM-step observations.
                    io_cost.update({k: part.metrics[k] for k in ('rows_returned', 'vm_steps')
                                    if k in part.metrics})
                self._record('capture_end', **receipt_data, disposition=disposition, error=failure,
                    receipt=part.receipt if part is not None else None,
                    strict_capture=raw,
                    metrics=part.metrics if part is not None else {},
                    io_cost=io_cost,
                    current_state=self.store.receipt_state(),
                    elapsed_ns=time.monotonic_ns() - started,
                    thread_cpu_ns=time.thread_time_ns() - cpu)

    def _acquire_local(self, rd, stamp):
        for source in self.sources:
            if self.closed or self.feed.stopped.is_set():
                break
            self._capture_one(rd, source, stamp)

    def _acquire_external(self, rd, stamp):
        self._capture_one(rd, self.external, stamp)

    def _drain_interest(self):
        # HTTP remains an enqueue-only path. The composed service must consume
        # these queues even when canonical reconciliation is blocked. Never hold
        # the coordinator lock while taking Feed.lock (keyed admission reverses
        # that order). Demand leases do not renew source retry/coverage authority.
        f = self.feed
        with f.lock:
            if self.closed or f.stopped.is_set():
                return
            f._drain()
            now = f.clock()
            while True:
                try:
                    subscriber, session, expires = f._panel_updates.get_nowait()
                except queue.Empty:
                    break
                if session and expires > now:
                    f._panel_subscribers[subscriber] = (session, expires)
                else:
                    f._panel_subscribers.pop(subscriber, None)
            f._panel_subscribers = {k: v for k, v in f._panel_subscribers.items() if v[1] > now}
            while True:
                try:
                    sessions, expires = f._board_demands.get_nowait()
                except queue.Empty:
                    break
                if expires > now:
                    f._board_requested.update({s: expires for s in sessions})
            f._board_requested = {s: end for s, end in f._board_requested.items() if end > now}

    def service(self):
        self._drain_interest()
        with self.lock:
            if self.closed or self.feed.stopped.is_set():
                return
            if not self._available():
                return
            deferred = []
            while True:
                try:
                    kind, key, value = self.completed.get_nowait()
                except queue.Empty:
                    break
                if kind == 'acquisition_done':
                    worker = self.acquiring.get(key)
                    if worker is not None and worker.is_alive():
                        deferred.append((kind, key, value))
                        continue
                    self.acquiring.pop(key, None)
                elif kind == 'projection_done':
                    worker = self.active.get(key)
                    if worker is not None and worker.is_alive():
                        deferred.append((kind, key, value))
                        continue
                    self.active.pop(key, None)
                    if key not in self.projection_attempts:
                        self._active_ages.pop(key, None)
                elif kind == 'registration_error':
                    self.feed._board_capture_failures.update(self.feed.interest)
                    attempt = self.role_attempts[key]
                    attempt.count += 1
                    attempt.retry_at = self.feed._backoff(attempt.count)
                elif kind == 'source':
                    if key == self.external:
                        completed = {sid.chat.session_id for sid in value.receipt.coverage.completed_scopes}
                        self.feed._board_capture_failures.difference_update(completed)
            for completion in deferred:
                self.completed.put(completion)
            self._completion_pending = bool(deferred)
            for s, attempt in self.projection_attempts.items():
                if attempt.retry_at is not None and self.feed.clock() >= attempt.retry_at:
                    self.pending.setdefault(s, self._active_ages.get(s, time.monotonic_ns()))
            for s in sorted(self.pending, key=lambda s: (self.pending[s], s)):
                if len(self.active) >= self.capacity:
                    break
                if s in self.active or s not in self.chats:
                    continue
                if not self._projection_ready(s):
                    self._cancel_tickets(s, 'snapshot projection retry is pending or exhausted')
                    continue
                self._active_ages[s] = self.pending.pop(s)
                self.projection_attempts[s].retry_at = None
                worker = threading.Thread(target=self._project, args=(s,),
                                          name='session-panel-projection-' + s[:12], daemon=True)
                self.active[s] = worker
                self._worker_started()
                worker.start()

    def _base(self, s, lineage):
        # Never take Feed.lock while holding the coordinator lock: keyed
        # admission owns Feed.lock before entering this coordinator. Snapshot
        # immutable candidates separately; publication revalidates the pinned
        # watch, lineage and source admission, including intervening forgets.
        with self.lock:
            owned = self.bases.get(s)
        with self.feed.lock:
            held = self.feed.bundles.get(s, {}).get('bundle')
        candidates = [b for b in (owned, held)
                      if b is not None and tuple(b['lineage']) == lineage]
        if candidates:
            return max(candidates, key=lambda b: b.get('canonical_seq', 0))
        # Unknown canonical inputs are not a synthesized successful empty read.
        self.feed._wake.set()
        return {'session': s, 'lineage': list(lineage), 'canonical_seq': 0,
                'requests': [], 'outcome_json': None, 'input_errors': ['canonical inputs unavailable']}

    def _project(self, s):
        tickets = []
        measured = self._receipt_log() is not None
        projection_attempt_id = uuid4().hex if measured else None
        started = time.monotonic_ns() if measured else 0
        cpu = time.thread_time_ns() if measured else 0
        pin, projection = None, None
        disposition, failure = 'error', None
        cost = StageCost() if measured else None
        io_cost, computation_end, computation_cpu = None, None, None
        try:
            with self.lock:
                chat = self.chats[s]
                tickets = self.tickets.pop(s, [])
                token = self.tokens[s]
                if token is not self.feed._watch_tokens.get(s):
                    raise RuntimeError('snapshot watch was replaced')
                pin = self.store.pin(chat)
                queued_generation = self._queue_generations.get(s, 0)
                lineage = pin[0].watches[chat].lineage_tuple
                demand = pin[1].required_scopes[self.external]
                raw = self.external_raw.get(demand.id)
                captured = (raw[1] if raw is not None
                            and raw[0] == pin[1].required_versions.get(self.external) else ())
            if measured:
                self._record('projection_start', projection_attempt_id=projection_attempt_id,
                    chat=chat, admission=pin[1], pinned_versions=pin[1].required_versions,
                    frozen_demands=pin[1].required_scopes,
                    current_state=self.store.receipt_state())
                assert cost is not None
                cost.start()
            base = self._base(s, lineage)
            with self.feed.lock:
                held = (self.feed.bundles.get(s) or {}).get('board_projection')
            if (held is not None and held.admission == pin[1]
                    and held.feed_epoch == self.feed.epoch and held.feed_owner is self.feed._projection_owner
                    and held.watch_token is token and captured and held.external_capture
                    and self.feed.readers.external_semantic(captured)
                        == self.feed.readers.external_semantic(held.external_capture)):
                # Reuse exact board computation on a canonical/full retry. The
                # writer independently overlays newer canonical authority. Do not
                # erase an observation or renew computation clocks at this seam.
                projection = self.feed.carry_board_observations(self.store, held, captured)
                if base.get('canonical_seq', 0) > held.bundle.get('canonical_seq', 0):
                    bundle = sys.modules[type(held).__module__].canonical_overlay(
                        self.module.thaw(projection.bundle), base)
                    bundle['model'] = self.feed.reducer(bundle, now=self.feed.clock())
                    projection = replace(projection, bundle=self.module.freeze(bundle))
            else:
                projection = self.feed.project_board(self.store, chat, canonical_base=base, pin=pin,
                                                     external_capture=captured)
                if captured:
                    bundle = self.feed.readers.advance_external(self.module.thaw(projection.bundle), captured)
                    bundle['model'] = self.feed.reducer(bundle, now=self.feed.clock())
                    projection = replace(projection, bundle=self.module.freeze(bundle))
            # Bind to the watch that owned this pin, never a same-name watch
            # installed while base selection or pure projection was in flight.
            projection = replace(projection, watch_token=token)
            if measured:
                # Publication has its own complete writer clocks/costs. Network
                # I/O in the existing emit callback is NOT projection I/O.
                assert cost is not None
                io_cost = cost.finish()
                computation_end, computation_cpu = time.monotonic_ns(), time.thread_time_ns()
            results = self.feed.publish_projections(self.store, {s: projection})
            result = results[s]
            disposition = result.disposition
            bundle = BoardBundle(projection, self.module)
            with self.lock:
                if self.closed:
                    for ticket in tickets:
                        ticket.error = RuntimeError('snapshot owner stopped')
                    return
                if result.disposition == 'superseded':
                    if self.tokens.get(s) is self.feed._watch_tokens.get(s) and s in self.feed.interest:
                        self.tickets.setdefault(s, []).extend(tickets)
                        tickets = []
                        self._queue([s])
                    else:
                        for ticket in tickets:
                            ticket.error = RuntimeError('snapshot watch was replaced')
                if (result.disposition != 'superseded'
                        and self.store.current(projection.admission)
                        and self._queue_generations.get(s, 0) == queued_generation
                        and not self.tickets.get(s)
                        and self.bases.get(s, {}).get('canonical_seq', 0)
                            <= projection.bundle.get('canonical_seq', 0)):
                    # A request/source change can requeue a worker after slot
                    # admission but BEFORE its immutable pin. If this successful
                    # publication covers that current pin/canonical base, the
                    # queued work is served too. Keeping it would retain the old
                    # episode age forever, letting two hot chats starve FIFO.
                    # Later source versions, canonical reads or tickets remain
                    # owed and preserve their original first-unserved age.
                    self.pending.pop(s, None)
                    self.projection_attempts.pop(s, None)
                elif result.disposition in {'stored', 'no_change'}:
                    # Success served this attempt, but a post-pin request is
                    # still owed. Re-arm its existing finite budget; leaving
                    # retry_at=None with count>0 would mean permanently in-flight.
                    attempt = self.projection_attempts.get(s)
                    if attempt is not None and attempt.count and attempt.retry_at is None:
                        attempt.retry_at = self.feed.clock()
                if (result.disposition != 'superseded' and not projection.missing_scopes
                        and s not in self.pending and self.store.current(projection.admission)):
                    # Evaluate after served-episode removal, not before it.
                    self.feed._board_failures.discard(s)
                    self.feed._retry.discard(s)
            for ticket in tickets:
                if result.disposition in {'stored', 'no_change'}:
                    # The writer may have merged a newer canonical component
                    # after this projection pinned its base. Return that actual
                    # publication, not obsolete consent from the proposed value.
                    with self.feed.lock:
                        current = self.feed.bundles.get(s)
                        if current is not None and self.tokens.get(s) is self.feed._watch_tokens.get(s):
                            actual = BoardBundle(projection, self.module)
                            actual.clear()
                            actual.update(current['bundle'])
                            ticket.bundle = actual
                        else:
                            ticket.error = RuntimeError('snapshot watch was replaced')
                elif ticket.error is None:
                    ticket.error = RuntimeError('snapshot publication refused: ' + result.disposition)
        except Exception as exc:
            failure = type(exc).__name__
            with self.lock:
                if not self.closed:
                    self.feed._board_failures.add(s)
                    attempt = self.projection_attempts.setdefault(s, Attempt())
                    attempt.count += 1
                    attempt.retry_at = self.feed._backoff(attempt.count)
            for ticket in tickets:
                ticket.error = exc
            log.warning('session-panel pure projection failed: %s', s, exc_info=True)
        finally:
            if measured:
                assert cost is not None
                if io_cost is None:
                    io_cost = cost.finish()
                    computation_end, computation_cpu = time.monotonic_ns(), time.thread_time_ns()
                state = self.store.receipt_state()
                if state['closed']:
                    disposition = 'retired'
                elif pin is not None and state['watches'].get(pin[1].chat) != pin[0].watches.get(pin[1].chat):
                    disposition = 'forgotten' if pin[1].chat not in state['watches'] else 'lineage_changed'
                assert computation_end is not None and computation_cpu is not None
                self._record('projection_end', projection_attempt_id=projection_attempt_id,
                    session=s, admission=pin[1] if pin is not None else None,
                    current_state=state, disposition=disposition, error=failure,
                    covered_scopes=projection.covered_scopes if projection is not None else None,
                    missing_scopes=projection.missing_scopes if projection is not None else None,
                    dependencies=projection.dependencies if projection is not None else None,
                    projected_card_details=(len(projection.bundle.get('cards', {}))
                        if projection is not None else None),
                    projected_membership_rows=(len(projection.bundle.get('tasks', ()))
                        + len(projection.bundle.get('done', ())) if projection is not None else None),
                    io_cost=io_cost,
                    elapsed_ns=computation_end - started, thread_cpu_ns=computation_cpu - cpu,
                    total_worker_elapsed_ns=time.monotonic_ns() - started)
                if disposition in {'no_change', 'superseded'} and pin is not None:
                    successor = state['admissions'].get(pin[1].chat)
                    self._record(disposition, chat=pin[1].chat, admission=pin[1],
                        current_state=state, successor=successor,
                        predecessor_admission_id=pin[1].id,
                        successor_admission_id=(successor.id if successor is not None
                            and successor.id != pin[1].id else None),
                        # This outcome is not an inclusion receipt. The analyzer
                        # may fulfill only via exact emission/response custody.
                        inclusion='not_established',
                        covered_scopes=projection.covered_scopes if projection is not None else None,
                        missing_scopes=projection.missing_scopes if projection is not None else None)
            for ticket in tickets:
                ticket.ready.set()
            self.completed.put(('projection_done', s, None))
            self.service_wake.set()
            self.feed._board.set()
            self._worker_finished()

    def _settle_local(self, sessions):
        """Finish the owned local turn, including a registration admitted mid-turn."""
        deadline = time.monotonic() + 3.
        while True:
            self.step()
            with self.lock:
                local = self.acquiring.get('local')
            if local:
                local.join(max(0., deadline - time.monotonic()))
                if local.is_alive():
                    raise TimeoutError('local source acquisition is still pending')
            self.service()
            with self.lock:
                ready = all(s in self.chats and self.tokens[s] is self.feed._watch_tokens.get(s)
                            for s in sessions)
                if ready and not self._local_requested:
                    return
                if self.registration_attempt.count:
                    raise RuntimeError('local source registration unavailable; bounded retry is pending')
            if time.monotonic() >= deadline or self.closed or self.feed.stopped.is_set():
                raise RuntimeError('local source registration was cancelled or remains pending')

    def tick(self):
        f = self.feed
        now = f._last_pass = f.clock()
        f._wake.clear()
        f._cold.clear()
        f._drain()
        with f.lock:
            f._expire(now)
            watched = list(f.interest)
            before = {s: f.bundles.get(s, {}).get('rev') for s in watched}
        if not watched:
            return 0
        rd = f.readers
        # Capture BEFORE asynchronous acquisition and canonical producer writes.
        source_stamp = getattr(rd, 'reconciliation_stamp', None)
        source_seen = source_stamp() if callable(source_stamp) else None
        self._settle_local(watched)
        panel_dirty = f._panel_dirty(watched)
        with self.lock:
            captured_dirty = dict(self.canonical_dirty)
            lineages = {s: self.store.pin(self.chats[s])[0].watches[self.chats[s]].lineage_tuple
                        for s in watched if s in self.chats}
        redo = set()
        if f._reconcile_retry_at is not None and now >= f._reconcile_retry_at:
            redo, f._reconcile_retry_at = set(f._reconcile_deferred), None
        reconcile = getattr(rd, 'reconcile_sources', None)
        if callable(reconcile) and callable(source_stamp):
            if getattr(rd, 'reconcile_scoped', False):
                panel_dirty.update(f._producer_admission(watched, source_seen, redo,
                    set(captured_dirty), panel_dirty, now, lineage=lambda s: lineages.get(s, (s,))))
            else:
                f._produce(watched, source_seen, redo)
        with self.lock:
            # Do not consume another same-chat source admission while reconcile
            # was blocked. A source result after this frozen read is still owed.
            for s, version in captured_dirty.items():
                if self.canonical_dirty.get(s) == version:
                    self.canonical_dirty.pop(s, None)
        with f.lock:
            dirty = set(captured_dirty) | panel_dirty | f._retry.intersection(watched)
            dirty.update(s for s in watched if s not in f.bundles)
        if dirty:
            f._attempted.update(dirty)
            f._publish(self.full([s for s in watched if s in dirty]), heartbeat=True)
        else:
            f._publish({}, heartbeat=True)
        with f.lock:
            return sum(f.bundles.get(s, {}).get('rev') != before[s] for s in watched)

    def full(self, sessions):
        self.feed.watch(sessions)
        # Wait for owned LOCAL acquisition only. Strict external is deliberately
        # never joined; unknown external coverage remains explicit in projection.
        self._settle_local(sessions)
        rd = self.feed.readers
        tickets = {}
        try:
            for s in sessions:
                with self.lock:
                    chat = self.chats[s]
                    pin = self.store.pin(chat)
                    lineage = pin[0].watches[chat].lineage_tuple
                seq = next(self.feed._seq)  # before canonical read, never after
                inputs = rd.panel_inputs(s, list(lineage))
                base = {'session': s, 'lineage': list(lineage), 'canonical_seq': seq, **inputs}
                with self.lock:
                    self.bases[s] = base
                    ticket = tickets[s] = Ticket()
                    self.tickets.setdefault(s, []).append(ticket)
                    self._queue([s])
            self.service()
            for ticket in tickets.values():
                while not ticket.ready.wait(.1):
                    if self.closed or self.feed.stopped.is_set():
                        raise RuntimeError('snapshot owner stopped')
                    self.service()
            return {s: t.bundle for s, t in tickets.items() if t.error is None}
        finally:
            rd.release()

    def retire(self):
        """Nonjoining authority withdrawal, safe under the Feed writer fence."""
        with self.lock:
            if not self.closed:
                self._retired_pending = dict(self.pending)
            self.closed = True
            # Withdraw immutable publication authority before any potentially
            # blocked join. A late projection may keep its pin, never its lease.
            self.store.close(self.store.epoch)
            self.service_stop.set()
            self.pending.clear()
            for s in list(self.tickets):
                self._cancel_tickets(s, 'snapshot owner stopped')
        self.service_wake.set()

    def close(self):
        self.retire()
        with self.lock:
            workers = [*self.acquiring.values(), *self.active.values()]
        for worker in [self.service_worker, *workers]:
            if worker is not None and worker is not threading.current_thread():
                worker.join(2)
