"""Session-panel feed: the gateway precomputes each watched chat's panel data and pushes what changed.

[daveey 2026-11-20] "precompute the status panel data on the hub ... use a websocket or some form of event
streaming so that the desktop is always kept in sync without polling." The Desktop registers the chats it
cares about (`changes`); a background tick (1 s) checks the board/session data stamps, finds the cards that
changed (new `task_events` ids), recomputes only the bundles that reference them, and hands every changed
bundle to `emit` in one batch (the gateway pushes it as `plugin.session-panel.changed`). A full refresh of every
watched chat runs every FULL_EVERY_S as a safety net. With nothing to push, a heartbeat batch goes out every
HEARTBEAT_S: clients count the feed live only from pushes, so a feed whose pushes do not reach them (no socket,
another backend, a stuck thread) is never trusted. `changes()` is the catch-up for a client that (re)connected,
saw a revision gap, or got a batch by name only.

A bundle holds exactly what the Desktop panel would otherwise fetch itself, in the same shapes:
- `lineage`: the chat and its compression ancestors (session-tasks `lineageIds`);
- `tasks`: the rows of `/api/session-kanban-tasks` for that lineage;
- `done`: the rows of `/api/plugins/user-steps/session-cards` (finished cards included);
- `cards`: a slim `/api/plugins/kanban/tasks/<id>` record (`{detail, board}`) for every card on a linked card's
  dependency chain: only the fields session-panel's chain and wake logic read.
Rendering (sections, chains, "10m left", evidence freshness) stays on the Desktop, computed at render time.

Concurrency: bundles are computed outside any lock (the readers give each thread its own connections); storing a
bundle, taking its revision and emitting the batch happen under `compute_lock`, so batches go out in revision order
and each batch's `prev` is the revision right before it. Pure: every read goes through `readers`.
"""
from __future__ import annotations

import hashlib
import importlib.util
import inspect
from pathlib import Path
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass, field
import itertools
import json
import queue
from types import MappingProxyType
import logging
import re
import threading
import time
import uuid
import sys
from typing import Any, Callable, Iterable, Optional

FULL_EVERY_S = 60.0          # a watched chat with open cards is recomputed at least this often (run evidence)
QUIET_FULL_EVERY_S = 300.0   # one with none (it changes only through card events, which recompute it at once)
HEARTBEAT_S = 20.0           # with nothing to push, a heartbeat at least this often (clients' liveness)
INTEREST_TTL_S = 15 * 60.0   # a chat nobody re-registers stops being computed
MAX_WATCHED = 400            # interest cap across all clients (a Desktop keeps its whole sidebar warm)
MAX_REQUEST = 200            # chats one request may register
FIRST_CHUNK = 4              # a pass stores and pushes the chats asked for most recently first, this many...
CHUNK = 16                   # ...then the rest this many at a time, so a click never waits for a whole sweep
DECISION_FULL_EVERY_S = 30.0 # a chat whose decision inputs did not move is still re-read this often
RETRY_EVERY_S = 2.0          # with no write to wake a pass: a read that failed or raced is tried again this often
MIN_IDLE_WAIT_S = 0.2        # the event-driven loop never sleeps less than this between idle checks
RECENT_PUBLISHES = 32        # pushes kept in `Feed.timing['recent']` (rev, time, chats, pass start)
PRODUCER_FULL_EVERY_S = 60.0 # source-event ceiling for canonical time/courier and unkeyed mirror inputs
PRODUCER_PASS_BUDGET_S = 5.0 # producer time per pass before hydration; chats not reached carry to the next pass


class _NoAccounting:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def skip(self):
        pass

    def change(self, changed=True):
        pass

    def fallback(self, fell_back=True):
        pass

    def rows(self, n):
        pass


def accounted_pass(name):
    """Optional core telemetry; nested passes do not double-count thread CPU."""
    try:
        from hermes_cli.loop_accounting import pass_
    except ImportError:
        return _NoAccounting()
    return pass_(name)


def _takes_deadline(readers: Any) -> bool:
    """The reader's reconcile_sources accepts a pass deadline (a replaced callable may not)."""
    fn = getattr(readers, 'reconcile_sources', None)
    if fn is None or not getattr(readers, 'reconcile_budgeted', False):
        return False
    try:
        return 'deadline' in inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False
RETRY_STEPS = 3              # failed work is retried this many times without a source event (0.1, 0.2, 0.4 s)
OPEN_KEYS = ('tasks', 'requests', 'subagents', 'processes', 'rentals')  # a bundle with any of these ages
SNAPSHOT_VERSION = 1         # the on-disk shape of Feed.snapshot()
RESTORE_REV_GAP = 1_000_000  # a restored feed's revisions start this far past the snapshot's (see Feed.restore)
CHAIN_DEPTH = 6              # hops per chain (session-panel walks at most six cards)
CHAIN_CARDS = 80             # cards per bundle
TERMINAL = {'done', 'archived', 'cancelled'}
CARD_ID = re.compile(r'\bt_[0-9a-f]{6,}\b')
# Bundle inputs the hub reduces `model` from. No client reads them (Desktop renders `model` and takes `tasks` and
# `lineage` for chips), yet they are most of a bundle (one long card chain: ~1 MB of `cards`). [daveey 2026-11-22]
# "switching sessions ... should be instant": a Desktop pulled ~45 MB per 2 min of them over its ssh tunnel, and
# every transcript read on that tunnel queued behind them (cache paint 40 ms, reconcile 6-8 s). Pushes and
# catch-ups carry wire_bundle(); the held bundle (and Feed.changes(), the in-process view) keeps everything.
WIRE_OMIT = frozenset({'cards', 'done', 'requests', 'outcome_json', 'board_errors'})


def wire_bundle(bundle):
    """What a client receives for one chat: the held bundle without the server-only inputs in WIRE_OMIT."""
    if not isinstance(bundle, dict):
        return bundle
    return {k: v for k, v in bundle.items() if k not in WIRE_OMIT}


PROCESS_FEED = None          # the one feed of this process (plugin_api.feed())
PROCESS_LOCK = threading.Lock()
RESTART = None               # plugin_api.feed: a plugin reload restarts (resumes) the feed at once
SNAPSHOT_READER_FACTORY = None  # current API registration, never a saved predecessor's factory
log = logging.getLogger('session-panel')
_MODEL_PATH = Path(__file__).resolve().parents[1] / 'model.py'
# Content-addressed module generation: model/brief changes cannot leave a hot
# feed on old reducer code. No mutable global sys.path or half-loaded singleton.
def _model():
    spec = importlib.util.spec_from_file_location('_session_panel_model', _MODEL_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod



_board_spec = importlib.util.spec_from_file_location('_session_panel_board_lane', Path(__file__).with_name('board_lane.py'))
_board_module = importlib.util.module_from_spec(_board_spec)
_board_spec.loader.exec_module(_board_module)
BoardLane = _board_module.BoardLane

_PROJECTION_LOCK = threading.Lock()


def _projection():
    # Lazy/content-qualified: importing Feed does not activate snapshot consumers.
    path = Path(__file__).with_name('board_projection.py')
    name = '_panel_projection_' + hashlib.sha256(path.read_bytes()).hexdigest()
    with _PROJECTION_LOCK:
        if name not in sys.modules:
            spec = importlib.util.spec_from_file_location(name, path)
            assert spec is not None and spec.loader is not None
            mod = importlib.util.module_from_spec(spec)
            sys.modules[name] = mod
            try:
                spec.loader.exec_module(mod)
            except BaseException:
                sys.modules.pop(name, None)
                raise
        return sys.modules[name]

_facts_spec = importlib.util.spec_from_file_location('_session_panel_fact_subscription', Path(__file__).with_name('fact_subscription.py'))
_facts_module = importlib.util.module_from_spec(_facts_spec)
_facts_spec.loader.exec_module(_facts_module)
PanelSubscription = _facts_module.PanelSubscription
PanelReadiness = _facts_module.PanelReadiness


def decision_projection(bundle):
    """The decision repaint wire, shared by legacy and facts publication."""
    result = {k: bundle[k] for k in ('session', 'lineage', 'model')}
    result['tasks'] = [{k: task[k] for k in ('id', 'gateway', 'profile', 'board') if k in task}
                       for task in bundle.get('tasks', [])]
    return result


_NO_PANEL_INPUTS = object()


def panel_bundle(session, lineage, tasks, done, cards, *, computed_at, computed_seq,
                 inputs=_NO_PANEL_INPUTS, canonical_seq=None, base=None, reducer=None):
    """Canonical pure assembly from captured inputs, shared with owner adapters.

    Source capture and reconciliation are deliberately outside this function.
    Inject the loaded reducer at owner registration to avoid module/file discovery
    during projection. The legacy caller retains its existing loader fallback.
    """
    bundle = {**(base or {}), 'session': session, 'lineage': lineage, 'tasks': tasks,
              'done': done, 'cards': cards, 'computed_at': computed_at,
              'computed_seq': computed_seq}
    if inputs is not _NO_PANEL_INPUTS:
        bundle['canonical_seq'] = canonical_seq
        bundle.update(inputs)
    bundle['model'] = (reducer or _model().reduce_panel)(bundle, now=bundle['computed_at'], wire=True)
    return bundle


def _hash(bundle: dict) -> str:
    body = {k: v for k, v in bundle.items() if k not in ('computed_at', 'computed_seq', 'canonical_seq')}
    return hashlib.sha1(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()


def chain_refs(row: Optional[dict], detail: Optional[dict]) -> list[str]:
    """The cards a chain can step to from this card: its open parents and every card id its wait names
    (the row's wait/block text, the newest park's `awaiting` ref, its human request)."""
    out: list[str] = []
    task = (detail or {}).get('task') or {}
    status = {t.get('id'): t.get('status') for t in (detail or {}).get('link_tasks') or [] if isinstance(t, dict)}
    for pid in ((detail or {}).get('links') or {}).get('parents') or []:
        if status.get(pid) not in TERMINAL:
            out.append(pid)
    texts: list[Any] = [row or {}, task.get('block_reason'), task.get('human_request')]
    for e in ((detail or {}).get('events') or [])[-10:]:
        if isinstance(e, dict) and e.get('kind') in ('continued', 'scheduled', 'blocked'):
            texts.append(e.get('payload'))
    blob = json.dumps(texts, default=str)
    own = task.get('id') or (row or {}).get('id')
    out += [m for m in CARD_ID.findall(blob) if m != own]
    return list(dict.fromkeys(out))


@dataclass
class _Flight:
    owner: int
    ready: threading.Event = field(default_factory=threading.Event)
    bundle: Optional[dict] = None


class _Stop(threading.Event):
    """The feed's stop flag: setting it also wakes the threads that sleep until something happens."""

    def __init__(self, feed):
        super().__init__()
        self._feed = feed

    def set(self):
        super().set()
        for name in ('_wake', '_cold', '_decide', '_board'):
            e = getattr(self._feed, name, None)
            if e is not None:
                e.set()


class Feed:
    """One feed per gateway process (one profile: the process's own)."""
    decision_projection = staticmethod(decision_projection)

    def __init__(self, readers: Any, *, emit: Callable[[dict], None], clock: Callable[[], float] = time.time,
                 full_every_s: float = FULL_EVERY_S, interest_ttl_s: float = INTEREST_TTL_S,
                 heartbeat_s: float = HEARTBEAT_S):
        self.readers = readers
        self.emit = emit
        self.clock = clock
        self.full_every_s = full_every_s
        self.interest_ttl_s = interest_ttl_s
        self.heartbeat_s = heartbeat_s
        self.epoch = uuid.uuid4().hex[:12]   # a new process starts a new revision space
        self.rev = 0
        self.bundles: dict[str, dict] = {}   # session -> {rev, hash, bundle, refs, lineage}
        self.interest: dict[str, float] = {} # session -> expires at
        self._watch_tokens = {}             # rejects reads from forgotten/re-watched membership
        self._projection_owner = object()   # not transferred by resume or disk restore
        self._panel_updates = queue.SimpleQueue()
        self._board_demands = queue.SimpleQueue()  # HTTP only enqueues; service owns expiry
        self._board_requested = {}
        self._panel_subscribers = {}  # mounted-panel id -> (session, lease expiry)
        self._board_dirty = set()
        self._board_failures = set()
        self._board_capture_failures = set()  # only a successful strict recapture may clear these
        self._board = threading.Event()
        self.failures: dict[str, dict] = {}  # session -> {count, error, at}: shown by /status
        self.lock = threading.RLock()        # interest / bundles / failures
        self.compute_lock = threading.Lock() # store + revision + emit, in order
        self._writer_context = threading.local()  # private measurement, never runtime authority
        self.stopped = _Stop(self)
        self.last_emit = clock()          # the heartbeat clock starts with the feed
        self.last_tick_s = 0.0               # duration of the last tick that recomputed (shown by /status)
        # Wall-clock timing readable without the session token (/status, and the timing file the run loop writes).
        self.timing: dict[str, Any] = {'ticks': 0, 'tick_started_at': None, 'tick_duration_s': None,
                                       'producer_s': None, 'producer_chats': 0, 'last_publish_at': None,
                                       'last_publish_rev': None, 'recent': []}
        self._producer_seen: Any = None      # global source stamp the producer last settled (admission only)
        self._producer_lineage: dict[str, tuple] = {}  # lineage each chat was last reconciled under
        self._producer_owed: set[str] = set()  # outcome/request stamp moved under a stamp that predated it
        self._producer_at: dict[str, float] = {}  # last actual producer pass, not an unaffected stamp settlement
        self._pass: Optional[tuple] = None   # (thread ident, wall start) of the pass in flight
        self._seq = itertools.count(1)       # compute order: a bundle computed earlier never replaces a later one
        self._stamps: Any = None
        self._panel_stamps: dict[str, Any] = {}
        self._reconcile_stamps: dict[str, Any] = {}
        # Failed work is retried a few times on a short backoff, then waits for the next source event.
        # It is never an idle timer: with nothing failed there is no retry time at all.
        self._reconcile_deferred: set[str] = set()   # producer work a reader could not finish
        self._producer_carry: set[str] = set()       # producer work a budgeted pass did not reach
        self._producer_pending: dict[str, float] = {}  # floor-delayed work; never consumes failure retries
        self._producer_run_at: dict[str, float] = {}  # actual calls, not rolled back with failed capture
        self._budgets: dict[str, dict[str, int]] = {}  # lane -> chat -> retries spent since it last succeeded
        self._reconcile_retry_at = self._hydrate_retry_at = self._decision_retry_at = None
        self._attempted: set[str] = set()            # chats the current pass tried to hydrate
        self._panel_files: Any = None        # the files behind _panel_stamps, as last read
        self._decision_files: Any = None     # the same for the decision lane's last complete pass
        self._cursor: Any = None
        self._last_full = 0.0
        self._last_pass = 0.0   # clock of the last tick (the idle retry is measured from it)
        self._flights: dict[str, _Flight] = {}
        self._retry: set[str] = set()  # a tick joined reads that may predate its change cursor
        self._carried: set[str] = set()  # bundles resumed from a stopped feed: never vouched for until restored
        self._reissue: set[str] = set()  # restored from another process: their recompute takes a new revision
        self._successor: Optional['Feed'] = None  # the feed that resumed this one (requests still here move on)
        self._decision_failures: set[str] = set()
        self._decision_result_seq: dict[str, int] = {}
        self._last_decision = 0.0  # clock of the last decision pass
        self._decision_lock = threading.Lock()  # cheap read lane, never board computation
        self._registrations = queue.SimpleQueue()
        # [daveey 2026-11-22] "no polling. Update only when the data changes": the producer threads sleep on these.
        self._wake = threading.Event()       # a pass is due: a source file changed, or a read found no bundle
        self._cold = threading.Event()       # ...the latter: a reader is waiting, skip the write-burst debounce
        self._decide = threading.Event()     # the decision lane's own: a source file changed
        self._board_stamps: Any = None       # the board files alone, as of the last pass that read their events
        self._urgent: list[str] = []         # tick thread only: chats with no bundle, most recently asked first
        self._focus: list[str] = []          # ...and of those, the ones that led their request (a focused chat)
        self._decision_seen: dict[str, tuple] = {}  # decision lane only: session -> (inputs stamp, read at)
        self._wire_sources = {}
        self.receipt_log = self.receipt_module = None  # explicit private measurement only; never default activation
        self._wire = (self.epoch, self.rev, MappingProxyType({}), b'')
        self._publish_wire()

    def _bundle_fresh(self, session, now):
        return (getattr(self, '_watching', True) and session in self.interest
                and session in self.bundles
                and not any(session in values for values in (self.failures, self._retry, self._carried,
                    self._decision_failures, self._board_failures, self._board_dirty, self._board_capture_failures))
                and not self.bundles[session].get('source_stale')
                and 0 <= now - self.bundles[session]['bundle']['computed_at'] <= self.full_every_s)

    def _client_bundle(self, session, bundle, fresh):
        out = wire_bundle(bundle)
        if isinstance(out.get('model'), dict):
            model = out['model']
            out = {**out, 'model': {**model,
                'freshness': {'trusted': bool(fresh), 'computed_at': bundle['computed_at'],
                              'ttl_s': self.full_every_s}}}
        return out

    @contextmanager
    def _writer(self, producer_kind, *, timeout=None):
        """The existing fence, with explicit-only queue/acquire/release custody.

        Synchronous receipt enqueue is measured work. Sink flush/close never runs
        here. A timed-out snapshot retains a request and refusal, not a fictitious
        acquisition. No admission scheduling or fairness behavior is changed.
        """
        recorder = self.receipt_log
        trace = None
        if recorder is not None:
            trace = dict(writer_id=uuid.uuid4().hex, feed_epoch=self.epoch,
                         producer_kind=producer_kind, requested_ns=time.monotonic_ns(), acquired_ns=None)
            cpu = time.thread_time_ns()
            recorder.record('writer_request', **trace)
        acquired = self.compute_lock.acquire(timeout=-1 if timeout is None else timeout)
        prior = getattr(self._writer_context, 'current', None)
        try:
            if acquired and trace is not None:
                trace = {**trace, 'acquired_ns': time.monotonic_ns()}
                self._writer_context.current = trace
                recorder.record('writer_acquire', **trace)
            yield acquired
        finally:
            if acquired:
                self._writer_context.current = prior
                self.compute_lock.release()
            if trace is not None:
                recorder.record('writer_release', **trace, acquired=acquired,
                    end_feed_epoch=self.epoch, released_ns=time.monotonic_ns(),
                    thread_cpu_ns=time.thread_time_ns() - cpu)

    def install_receipts(self, log):
        """Explicit in-process measurement seam; no source/reader activation.

        The caller owns log flush/close after producer retirement. A running
        measurement cannot silently swap sinks and lose predecessor custody.
        """
        module = sys.modules[type(log).__module__]
        with self.compute_lock, self.lock:
            if self.receipt_log is not None and self.receipt_log is not log:
                raise ValueError('receipt log already installed')
            self.receipt_log, self.receipt_module = log, module
            self._wire_sources = {}  # stamp the exact current immutable bytes
            self._publish_wire()

    def _publish_wire(self):
        """Writer-only: freeze JSON bytes before swapping the complete revision view.

        Entries are immutable and old publications remain valid while a reader uses
        them. Serialize even unchanged revisions: computed_at can still advance.
        """
        entries = {}
        old_entries = self._wire[2]
        sources = {}
        now = self.clock()
        for s, held in self.bundles.items():
            old = old_entries.get(s)
            fresh = self._bundle_fresh(s, now)
            source = self._wire_sources.get(s)
            sources[s] = (held['bundle'], fresh)
            if old and isinstance(source, tuple) and source[0] is held['bundle'] and source[1] == fresh and old[0] == held['rev']:
                entries[s] = old
            else:
                encoded = self._encode(self._client_bundle(s, held['bundle'], fresh))
                entry = (held['rev'], self._encode(s), encoded)
                if self.receipt_log is not None:
                    custody = self.receipt_module.stored_model(self, s, held, encoded)
                    entry += (custody,)
                    self.receipt_log.record('model_stored', publication=custody.publication,
                                            **self.receipt_module.payload_fields(encoded))
                entries[s] = entry
        self._wire_sources = sources
        header = b'{"epoch":' + self._encode(self.epoch) + b',"rev":' + str(self.rev).encode()
        self._wire = (self.epoch, self.rev, MappingProxyType(entries), header)

    @staticmethod
    def _encode(value):
        # Match the route's historical json_safe + Starlette JSONResponse bytes.
        def safe(v):
            if isinstance(v, float):
                return v if v == v and v not in (float('inf'), float('-inf')) else None
            if v is None or isinstance(v, (bool, int, str)):
                return v
            if isinstance(v, dict):
                return {str(k): safe(x) for k, x in v.items()}
            if isinstance(v, (list, tuple, set)):
                return [safe(x) for x in v]
            return str(v)
        return json.dumps(safe(value), ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode()

    def changes_bytes(self, sessions, *, since=0, epoch=None, subscriber=None, visible_session=None):
        return self.changes_packet(sessions, since=since, epoch=epoch, subscriber=subscriber,
                                   visible_session=visible_session)[0]

    def changes_packet(self, sessions, *, since=0, epoch=None, subscriber=None, visible_session=None):
        """One immutable wire snapshot plus its exact private custody sidecars.

        No locks, readers, computation or JSON conversion on the HTTP read path.
        Cold sessions return no bundle until background publication. Sidecars are
        private bytes, never source certificates added to the client protocol.
        """
        if self._successor is not None:
            return self._current().changes_packet(sessions, since=since, epoch=epoch,
                                                  subscriber=subscriber, visible_session=visible_session)
        if subscriber:
            self._panel_updates.put((subscriber, visible_session, self.clock() + 75.0))
            self._board.set()
            lane = getattr(self, '_board_lane', None)
            if lane is not None:
                lane.service_wake.set()
        wanted = tuple(dict.fromkeys(sessions))[:MAX_REQUEST]
        self._board_demands.put((wanted, self.clock() + 75.0))
        lane = getattr(self, '_board_lane', None)
        if lane is not None:
            lane.service_wake.set()
        self._registrations.put(wanted)
        readiness = getattr(self, '_facts_readiness', None)
        if readiness is not None:
            readiness.set()
        if getattr(self, '_facts_mode', False):
            self._wake.set()  # interest renewal/demand is background-only
        current, rev, entries, header = self._wire
        reset = epoch != current
        if any(s not in entries for s in wanted):
            self._cold.set()  # the producer computes it on its own thread, at once
            self._wake.set()
        selected = [entries[s] for s in wanted if s in entries and (reset or entries[s][0] > since)]
        parts = [entry[1] + b':' + entry[2] for entry in selected]
        payload = (header + b',"reset":' + (b'true' if reset else b'false')
                   + b',"bundles":{' + b','.join(parts) + b'}}')
        return payload, tuple(entry[3] for entry in selected if len(entry) == 4)

    def resume(self, prev: 'Feed') -> 'Feed':
        """Carry a stopped feed of this process into this one, so clients see no reset.
        [daveey 2026-11-21] "the panels keep flipping between correct and incorrect": a gateway reloads its plugins
        for a new session whenever plugin inputs changed, which unloads this plugin and stopped the feed; the next
        request started a new epoch with no bundles (4 resets in 15 min on the hub), and every Desktop dropped its
        trust and re-read. The revision space, held bundles and interest carry over; every held chat is recomputed
        before a push vouches for it again. `prev` emits nothing after this (its in-flight push finishes first)."""
        with prev.lock:
            predecessor = getattr(prev, '_source_coordinator', None)
            installers = ()
            if predecessor is not None and getattr(predecessor, 'pending_install', False):
                # Stop admission before taking the installation snapshot. A
                # constructing/releasing candidate still owns its reader slot.
                predecessor.closed = True
                installers = tuple(p for p in (*getattr(predecessor, 'installers', ()), predecessor)
                                   if getattr(p, 'installing', False))
        retirement = None
        if predecessor is not None:
            # Withdraw source authority without joining under either writer lock.
            # The successor is attached before saved predecessor loops can start;
            # its four roles stay gated until retiring workers release readers.
            retirement = (predecessor.predecessor if getattr(predecessor, 'pending_install', False)
                          else self._source_module().RetiringOwner(predecessor))
        with prev.compute_lock, prev.lock:
            # First: whatever follows, the old object pushes nothing more under the revision space handed over here.
            prev.emit = lambda payload: None
            prev.stopped.set()
            self.epoch, self.rev, self.last_emit = prev.epoch, prev.rev, prev.last_emit
            self.last_tick_s = getattr(prev, 'last_tick_s', 0.0)
            self.timing.update(getattr(prev, 'timing', None) or {})
            bundles = getattr(prev, 'bundles', {})
            self.bundles = {s: dict(b) for s, b in bundles.items()}
            self.interest = dict(getattr(prev, 'interest', {}))
            old_tokens = getattr(prev, '_watch_tokens', {})
            self._watch_tokens = {s: old_tokens.get(s, object()) if predecessor is not None else object()
                                  for s in self.interest}
            self._registrations = getattr(prev, '_registrations', self._registrations)
            self._panel_updates = getattr(prev, '_panel_updates', self._panel_updates)
            self._board_demands = getattr(prev, '_board_demands', self._board_demands)
            self._board_requested = dict(getattr(prev, '_board_requested', {}))
            self._panel_subscribers = dict(getattr(prev, '_panel_subscribers', {}))
            self._board_dirty = set(getattr(prev, '_board_dirty', ()))
            for name in ('_wake', '_cold', '_decide', '_board'):
                setattr(self, name, getattr(prev, name, getattr(self, name)))
            self.failures = {s: dict(f) for s, f in getattr(prev, 'failures', {}).items()}
            self._retry = set(getattr(prev, '_retry', ())) | set(bundles)  # recompute every carried chat next pass
            self._carried = set(bundles)  # ...and vouch for none of them until its new bundle is stored
            self._board_capture_failures = set(getattr(prev, '_board_capture_failures', ())) & set(self.interest)
            self._board_failures = set(getattr(prev, '_board_failures', ())) & set(self.interest)
            self._decision_failures = set(getattr(prev, '_decision_failures', ())) & set(self.interest)
            # Restored from another process and not recomputed yet: still re-issued on its recompute.
            self._reissue = set(getattr(prev, '_reissue', ())) & set(bundles)
            # A carried bundle never outranks a newer computation: sequence numbers continue past every held one.
            top = max((max(int(b['bundle'].get(k) or 0) for k in ('computed_seq', 'canonical_seq')) for b in bundles.values()), default=0)
            self._seq = itertools.count(top + 1)
            prev._successor = self
            self._publish_wire()
            # Older feed code (a hot reload) reads neither `stopped` in its publish nor `_successor`: its entry points
            # are routed here as instance attributes (found before the class's), so a request still on it neither
            # moves its revision nor answers with one this feed never issued.
            prev._publish = lambda computed, **kwargs: {}
            prev.watch = self.watch
            prev.changes = self.changes
        if predecessor is not None:
            module = self._source_module()
            self._source_coordinator = self._board_lane = module.PendingSourceOwner(self,
                gateway=predecessor.gateway, profile=predecessor.profile, predecessor=retirement,
                installers=installers)
            self._snapshot_install_pending = True
            # The saved API constructed this object on the restart thread; release
            # its unused reader on that same thread. No old connection transfers.
            unused, self.readers = self.readers, module.AwaitingReaders()
            unused.release()
            loader = sys.modules.get('_session_panel_feed_loader')
            factory = SNAPSHOT_READER_FACTORY or getattr(loader, 'snapshot_reader_factory', None)
            # resume can be called by a genuine saved API while PROCESS_LOCK is
            # held. Current capability discovery takes the loader lock, whose
            # reload path takes PROCESS_LOCK in the opposite order. Publish only
            # registration here; the existing board loop installs after resume.
            pending = self._source_coordinator
            pending.api_generation = getattr(loader, 'generation', None)
            pending.reader_factory = factory
            self._board.set()
        return self

    def _current(self) -> 'Feed':
        """The feed that now owns this one's revision space (itself unless a reload resumed it elsewhere)."""
        f = self
        while f._successor is not None:
            f = f._successor
        return f

    # ── interest ────────────────────────────────────────────────────────────
    def watch(self, sessions: Iterable[str]) -> list[str]:
        now = self.clock()
        wanted = [s for s in dict.fromkeys(str(s) for s in sessions if s)][:MAX_REQUEST]
        with self.lock:
            for s in wanted:
                self._watch_tokens.setdefault(s, object())
                self.interest[s] = now + self.interest_ttl_s
            self._expire(now)
            while len(self.interest) > MAX_WATCHED:  # oldest registration first
                s = min(self.interest, key=self.interest.get)
                self._forget(s)
        return wanted

    def _expire(self, now: float) -> None:
        for s, until in list(self.interest.items()):
            if until < now:
                self._forget(s)

    def _forget(self, s: str) -> None:
        self._watch_tokens.pop(s, None)
        self._board_failures.discard(s)
        self._board_capture_failures.discard(s)
        self._decision_failures.discard(s)
        self._decision_result_seq.pop(s, None)
        self._decision_seen.pop(s, None)
        self.interest.pop(s, None)
        self._panel_stamps.pop(s, None)
        self._reconcile_stamps.pop(s, None)
        self._producer_pending.pop(s, None)
        self._producer_run_at.pop(s, None)
        self.__dict__.setdefault('_producer_lineage', {}).pop(s, None)
        self.__dict__.setdefault('_producer_owed', set()).discard(s)
        self._producer_at.pop(s, None)
        getattr(self, '_reconcile_deferred', set()).discard(s)
        getattr(self, '_producer_carry', set()).discard(s)
        (getattr(self, '_external_observations', None) or {}).pop(s, None)
        (getattr(self, '_external_captures', None) or {}).pop(s, None)
        self.bundles.pop(s, None)
        self.failures.pop(s, None)
        self._retry.discard(s)
        self._carried.discard(s)
        self._reissue.discard(s)
        for lane in (getattr(self, '_budgets', None) or {}).values():
            lane.pop(s, None)

    # ── computing ───────────────────────────────────────────────────────────
    @staticmethod
    def reducer(bundle, *, now):
        return _model().reduce_panel(bundle, now=now, wire=True)

    @staticmethod
    def _source_module():
        # Lifetime fences must work before current readers are registered. Never
        # ask a saved predecessor reader to discover its successor implementation.
        path = Path(__file__).with_name('source_coordinator.py')
        name = '_panel_source_' + hashlib.sha256(path.read_bytes()).hexdigest()
        with _PROJECTION_LOCK:
            if name not in sys.modules:
                spec = importlib.util.spec_from_file_location(name, path)
                assert spec is not None and spec.loader is not None
                mod = importlib.util.module_from_spec(spec)
                # Dataclasses resolves the defining module during execution;
                # the lock prevents a second caller observing a partial module.
                sys.modules[name] = mod
                try:
                    spec.loader.exec_module(mod)
                except BaseException:
                    sys.modules.pop(name, None)
                    raise
            return sys.modules[name]

    def ensure_snapshot_service(self, api_generation, reader_factory):
        """Complete only an existing opt-in, idempotently; manual owners stay inert.

        Reader construction/capability discovery is outside the publication lock.
        Revalidate the pending identity after construction, including stop/reload.
        A missing registration never borrows predecessor readers or hydrators.
        """
        with self.lock:
            pending = getattr(self, '_source_coordinator', None)
            if pending is None or self.stopped.is_set():
                return None
            if not getattr(pending, 'pending_install', False):
                return pending
            if pending.closed or reader_factory is None:
                return pending
            pending.reader_factory = reader_factory
            pending.api_generation = api_generation
            registration = (api_generation, reader_factory)
            if (getattr(pending, 'installing', False)
                    or any(getattr(p, 'installing', False) for p in getattr(pending, 'installers', ()))
                    or getattr(pending, 'failed_registration', None) == registration):
                return pending
            if not self._source_module().predecessor_ready(pending.predecessor):
                # Even current metadata discovery can open SQLite. Hold it behind
                # the same four-role retirement fence as capture/projection.
                pending.predecessor.bind(pending)
                return pending
            pending.installing = True
        candidate = None
        release_attempted = False
        owner = None
        try:
            candidate = reader_factory()
            for name in ('snapshot_modules', 'capture_local', 'capture_external', 'panel_inputs', 'release'):
                if not callable(getattr(candidate, name, None)):
                    raise RuntimeError('current snapshot reader capability unavailable: ' + name)
            # The constructor reads source coordinates through the current reader,
            # never through the Feed's not-yet-installed canonical component.
            owner = self._source_module().SourceCoordinator(self, reader_factory,
                gateway=pending.gateway, profile=pending.profile, readers=candidate)
            release_attempted = True
            candidate.release()
            with self.lock:
                if (self.stopped.is_set() or self._source_coordinator is not pending
                        or pending.closed
                        or (pending.api_generation, pending.reader_factory) != registration):
                    owner.retire()
                    return getattr(self, '_source_coordinator', None)
                if pending.predecessor is not None:
                    owner.inherit(pending.predecessor)
                self.readers = candidate
                owner.predecessor = pending.predecessor
                if owner.predecessor is not None:
                    owner.predecessor.bind(owner)
                self._source_coordinator = self._board_lane = owner
                self._snapshot_install_pending = False
                self._snapshot_api_generation = api_generation
                if getattr(self, '_board_service_required', False):
                    owner.start_service()
                self._wake.set(); self._decide.set(); self._board.set()
                return owner
        except Exception:
            if owner is not None:
                owner.retire()
            with self.lock:
                pending.failed_registration = registration
            log.warning('session-panel current reader installation unavailable', exc_info=True)
            return pending
        finally:
            try:
                if candidate is not None and not release_attempted:
                    # Construction/capability failures release on the caller thread.
                    release = getattr(candidate, 'release', None)
                    if callable(release):
                        release()
            except Exception:
                log.warning('session-panel candidate reader release failed', exc_info=True)
            finally:
                with self.lock:
                    pending.installing = False
                    if pending.closed or (pending.api_generation, pending.reader_factory) != registration:
                        self._board.set()

    def compose_board_sources(self, reader_factory, *, gateway, profile, _predecessor=None):
        """Explicit disposable composition only; normal startup remains UNACTIVATED.

        All board/full/catch-up routes share this owner once composed. No thread
        starts until step/full/start_service. Rollback must close it first.
        """
        with self.lock:
            if getattr(self, '_source_coordinator', None) is not None:
                return self._source_coordinator
            module = self._source_module()
            owner = module.SourceCoordinator(self, reader_factory, gateway=gateway, profile=profile)
            owner.predecessor = _predecessor
            if _predecessor is not None:
                _predecessor.bind(owner)
            self._source_coordinator = self._board_lane = owner
        return owner

    def project_board(self, store, chat, *, canonical_base, pin=None, seq=None, external_capture=()):
        """Explicit unactivated board/full/catch-up injection seam; no live reads.

        Callers acquire canonical inputs separately for this exact lineage. B2b
        owns scheduling and production route cutover; this method starts no work.
        """
        return _projection().project(self, store, chat, canonical_base=canonical_base, pin=pin, seq=seq,
                                     external_capture=external_capture)

    def carry_board_observations(self, store, previous, captured):
        """Pure, exact-parent-fenced canonical pull-clock carriage; no hydration."""
        return _projection().observe(self, store, previous, captured)

    def project_boards(self, store, canonical_bases, *, pins=None):
        """Shared-store full/catch-up composition, with one immutable pin per chat."""
        seq = next(self._seq)
        return {chat.session_id: self.project_board(store, chat, canonical_base=base,
                pin=(pins or {}).get(chat), seq=seq) for chat, base in canonical_bases.items()}

    def publish_projections(self, store, projections):
        """CAS and store under writer+scope fences; emit outside the store lock.

        Results separate storage/no-change from transport success. This is not
        the B3 authenticated model-inclusion receipt and never acknowledges work.
        """
        results = {}
        self._publish({}, _projections=projections, _snapshot_store=store, _results=results)
        return results

    def compute(self, session: str, *, seq: Optional[int] = None, readers=None, base=None, stages=None) -> dict:
        owner = getattr(self, '_source_coordinator', None)
        if owner is not None and readers is None:
            return owner.full([session])[session]
        seq = next(self._seq) if seq is None else seq  # assigned before the pass's first shared read
        read_at = self.clock()
        r = self.readers if readers is None else readers
        stages = {} if stages is None else stages
        def timed(name, fn, *args, **kwargs):
            began = time.monotonic()
            try:
                # Sibling scopes attribute this thread's CPU, including failed reads.
                # Core descendants remain inclusive; do not sum them with these stages.
                with accounted_pass('session_panel.compute.' + name.removesuffix('_s')):
                    return fn(*args, **kwargs)
            finally:
                stages[name] = round(stages.get(name, 0) + time.monotonic() - began, 4)
        lineage = timed('lineage_s', lambda: list(r.lineage(session))) or [session]
        if base is not None and lineage != base['lineage']:
            self._wake.set()
            raise RuntimeError('board-only read changed lineage; full hydration required')
        tasks = timed('linked_s', lambda: list(r.linked(lineage)))
        done = timed('done_s', lambda: list(r.done(lineage)))
        cards: dict[str, dict] = {}
        queue = [(str(t.get('id')), str(t.get('board') or 'default'), t, 0) for t in tasks if t.get('id')]
        seen: set[str] = set()
        while queue and len(cards) < CHAIN_CARDS:
            cid, board, row, depth = queue.pop(0)
            if cid in seen:
                continue
            seen.add(cid)
            qualified = getattr(r, 'card_ref', None)
            rec = timed('cards_s', qualified, cid, board, row) if callable(qualified) else timed('cards_s', r.card, cid, board)
            if rec is None:
                continue
            cards[cid] = rec
            status = ((rec.get('detail') or {}).get('task') or {}).get('status')
            if depth >= CHAIN_DEPTH or status in TERMINAL or status == 'running':
                continue
            for ref in chain_refs(row, rec.get('detail')):
                if ref not in seen:
                    parent = (next((p for p in (rec.get('detail') or {}).get('link_tasks', ())
                                   if p.get('id') == ref), None) if callable(qualified) else None)
                    queue.append((ref, rec.get('board') or board, parent, depth + 1))
        read_inputs = getattr(r, 'panel_inputs', None)
        inputs, canonical_seq = _NO_PANEL_INPUTS, None
        if base is None and callable(read_inputs):
            canonical_seq = next(self._seq)
            inputs = timed('panel_inputs_s', read_inputs, session, lineage)
        return timed('reduction_s', panel_bundle, session, lineage, tasks, done, cards,
                     computed_at=read_at, computed_seq=seq, inputs=inputs,
                     canonical_seq=canonical_seq, base=base)

    def _decision_result(self, session, base, seq, *, failed):
        """Set read availability only from the newest completed scoped attempt."""
        with self.lock:
            held = self.bundles.get(session)
            if (session not in self.interest or held is None
                    or held['bundle']['lineage'] != base['lineage']
                    or seq < max(self._decision_result_seq.get(session, 0),
                                 held['bundle'].get('canonical_seq', 0))):
                return False
            self._decision_result_seq[session] = seq
            if failed:
                self._decision_failures.add(session)
            else:
                self._decision_failures.discard(session)
                self._budgets.setdefault('decision', {}).pop(session, None)
            # Settle only this accepted attempt, never a sweep's old membership
            # snapshot. A newer chat/failure owns its own budget and deadline.
            keep = self._decision_failures - {session}
            due = self._spend('decision', {session} if failed else set(), keep=keep)
            others = [n for s, n in self._budgets['decision'].items()
                      if s in keep and n <= RETRY_STEPS]
            if others:
                earlier = self._decision_retry_at
                if earlier is None:
                    earlier = self._backoff(min(others))
                due = earlier if due is None else min(due, earlier)
            self._decision_retry_at = due
            return True

    def refresh_decision(self, session, *, readers):
        """Interactive canonical read, independent of the sweep's decision lock.

        Uses the existing component merge: never refresh board clocks or infer
        running work from a delivered decision. Old in-flight reads lose by seq.
        """
        with self.lock:
            held = self.bundles.get(session)
            if session not in self.interest or held is None:
                raise ValueError('Session panel is not hydrated')
            base = held['bundle']
            seq = next(self._seq)
        try:
            inputs = readers.decision_inputs(session, base['lineage'])
            if not isinstance(inputs, dict) or any(k not in inputs for k in ('outcome_json', 'requests')):
                raise ValueError('incomplete canonical decision inputs')
        except Exception:
            self._decision_result(session, base, seq, failed=True)
            raise
        self._decision_result(session, base, seq, failed=False)
        self._publish({session: {**base, **{k: inputs[k] for k in ('outcome_json', 'requests')},
                                 'canonical_seq': seq}}, canonical_only=True)
        with self.lock:
            held = self.bundles.get(session)
            if session not in self.interest or held is None or held['bundle']['lineage'] != base['lineage']:
                raise ValueError('Session panel changed during decision refresh')
            return {'bundle': held['bundle'], 'epoch': self.epoch, 'rev': self.rev}

    def refresh_decisions(self, *, readers=None) -> int:
        if getattr(self, '_source_coordinator', None) is not None:
            # Genuine saved decision loops pass their own old CoreReaders. The
            # opted-in successor owns current canonical readers as well as board
            # readers; thread-local connections are released by this caller.
            if getattr(self, '_snapshot_install_pending', False):
                return 0
            try:
                return self._refresh_decisions(readers=self.readers)
            finally:
                self.readers.release()
        return self._refresh_decisions(readers=readers)

    def _refresh_decisions(self, *, readers=None) -> int:
        """Read canonical decisions without board hydration during a long full sweep.

        Never reconcile mirrors or refresh board trust clocks here. Independent board
        and canonical sequences merge at publication. Failed reads remain unvouched.
        """
        read = getattr(self.readers if readers is None else readers, 'decision_inputs', None)
        if not callable(read) or not self._decision_lock.acquire(blocking=False):
            return 0
        try:
            with self.lock:
                held = {s: b['bundle'] for s, b in self.bundles.items() if s in self.interest}
                failed = set(self._decision_failures)
            # One batched stamp of every held chat's decision inputs, taken BEFORE the reads it guards (a write
            # after it is seen by the next pass). A chat whose stamp did not move is not re-read every pass: on
            # the hub that was one user-steps read per watched chat per second, most of the serve's lock traffic.
            rd = self.readers if readers is None else readers
            now = self._last_decision = self.clock()
            files = getattr(rd, 'panel_files_stamp', None)
            moved = files() if callable(files) else None
            if (moved is not None and moved == self._decision_files and not failed
                    and all(s in self._decision_seen for s in held)):
                return 0  # nothing behind any held chat's decisions moved: no database opened this pass
            stamps = self._decision_stamps(rd, held)
            count = 0
            for s, base in held.items():
                if self.stopped.is_set():
                    break
                stamp = stamps.get(s)
                seen = self._decision_seen.get(s)
                if stamp is not None and seen is not None and seen[0] == stamp and s not in failed:
                    continue
                seq = next(self._seq)
                try:
                    inputs = read(s, base['lineage'])
                    if not isinstance(inputs, dict) or any(k not in inputs for k in ('outcome_json', 'requests')):
                        raise ValueError('incomplete canonical decision inputs')
                    with self.lock:
                        if self._decision_result(s, base, seq, failed=False):
                            self._decision_seen[s] = (stamp, now)
                    update = {k: inputs[k] for k in ('outcome_json', 'requests')}
                    count += len(self._publish({s: {**base, **update, 'canonical_seq': seq}}, canonical_only=True))
                except Exception:
                    with self.lock:
                        self._decision_result(s, base, seq, failed=True)
                    log.debug('session-panel canonical refresh failed: %s', s, exc_info=True)
            if not self.stopped.is_set():
                self._decision_files = moved if stamps else None
            return count
        finally:
            self._decision_lock.release()

    @staticmethod
    def _decision_stamps(readers, held: dict) -> dict:
        """session -> a value that moves whenever its decision inputs may have; {} when the readers cannot say (every
        chat is then read every pass, as before). Never raises."""
        batch, store = getattr(readers, 'panel_stamps', None), getattr(readers, 'decision_store_stamp', None)
        if not callable(batch) or not callable(store):
            return {}
        try:
            whole = store()
            rows = batch(list(dict.fromkeys(i for b in held.values() for i in b['lineage'])))
            return {s: (whole, tuple(rows[i] for i in b['lineage'])) for s, b in held.items()}
        except Exception:
            log.debug('session-panel decision stamps failed', exc_info=True)
            return {}

    def _wait_flight(self, flight: _Flight) -> Optional[dict]:
        # No interest/publication lock while waiting, including heartbeat emits.
        if flight.owner == threading.get_ident() and not flight.ready.is_set():
            raise RuntimeError('reentrant session-panel compute')
        while not flight.ready.wait(min(self.heartbeat_s, 1.0)):
            self._publish({}, heartbeat=True)
        return flight.bundle

    def _compute_all(self, sessions: Iterable[str], *, refresh: bool = False) -> dict[str, dict]:
        with accounted_pass('session_panel.compute_all') as acct:
            result = self._compute_all_inner(sessions, refresh=refresh)
            acct.rows(len(result))
            acct.change(bool(result))
            if not result:
                acct.skip()
            return result

    def _compute_all_inner(self, sessions: Iterable[str], *, refresh: bool = False) -> dict[str, dict]:
        """One owner per session, shared by requests and ticks; disjoint owners never wait on each other.

        Reserve each session immediately before its reads, never queued behind unrelated sessions.
        Join after owned work so overlapping passes cannot form a cycle. Results stay joinable until publication.
        """
        owner = getattr(self, '_source_coordinator', None)
        if owner is not None:
            return owner.full(list(dict.fromkeys(sessions)))
        owned, joined, out = {}, {}, {}
        ident = threading.get_ident()
        finished = False
        sessions = list(dict.fromkeys(sessions))
        with self.lock:
            if any(s in self._flights and self._flights[s].owner == ident
                   and not self._flights[s].ready.is_set() for s in sessions):
                raise RuntimeError('reentrant session-panel compute')
            seq = next(self._seq)
            read_at = self.clock()
        batch = getattr(self.readers, 'batch', None)
        try:
            with batch(sessions) if sessions and callable(batch) else nullcontext():
                for s in sessions:
                    with self.lock:
                        flight = self._flights.get(s)
                        if flight is not None:
                            joined[s] = flight
                            if refresh:
                                self._retry.add(s)
                            continue
                        self._retry.discard(s)
                        owned[s] = self._flights[s] = flight = _Flight(ident)
                    self._publish({}, heartbeat=True)
                    try:
                        flight.bundle = out[s] = self.compute(s, seq=seq)
                        flight.bundle['computed_at'] = read_at  # trust ages from the snapshot, never its completion
                        with self.lock:
                            self.failures.pop(s, None)
                    except Exception as e:
                        with self.lock:
                            f = self.failures.setdefault(s, {'count': 0})
                            f.update(count=f['count'] + 1, error=f'{type(e).__name__}: {e}'[:300], at=self.clock())
                            first = f['count'] == 1
                        (log.warning if first else log.debug)('session-panel feed: %s failed', s, exc_info=True)
                    finally:
                        with self.lock:
                            if flight.bundle is None and self._flights.get(s) is flight:
                                self._flights.pop(s)
                            flight.ready.set()
            for s, flight in joined.items():
                bundle = self._wait_flight(flight)
                if bundle is not None:
                    out[s] = bundle
            finished = True
            return out
        finally:
            # Includes BaseException and a reader scope that fails at entry/exit: never strand waiters.
            with self.lock:
                for s, flight in owned.items():
                    if not finished or not flight.ready.is_set() or s not in out:
                        if self._flights.get(s) is flight:
                            self._flights.pop(s)
                        flight.ready.set()

    # ── across processes ────────────────────────────────────────────────────
    def snapshot(self, timeout: Optional[float] = None) -> Optional[dict]:
        """What a later process of this gateway needs to carry this feed on (Feed.restore): the revision space, the
        held bundles and the interest. Taken under the publish lock, so it is one consistent revision. With a
        `timeout`, None when the lock was not free in time (an exit must not hang on a stuck push)."""
        with self._writer('snapshot', timeout=timeout) as acquired:
            if not acquired:
                return None
            with self.lock:
                return self._snapshot_locked()

    def _snapshot_locked(self) -> dict:
        return {'version': SNAPSHOT_VERSION, 'epoch': self.epoch, 'rev': self.rev, 'saved_at': self.clock(),
                'bundles': {s: {'rev': b['rev'], 'hash': b['hash'], 'bundle': b['bundle']}
                            for s, b in self.bundles.items()},
                'interest': dict(self.interest)}

    def restore(self, snap: dict) -> 'Feed':
        """Carry on a feed a previous process of this gateway saved, so clients keep their revision chain across a
        restart. [daveey 2026-11-21] "we restart the hub for updates, figure out how to preserve the feed": every
        restart began a new epoch under a new feed id, and every Desktop dropped its trust and re-read.
        Revisions continue RESTORE_REV_GAP past the snapshot's: the old process may still push for a while after
        it saved (a restart overlaps the two), and a client following it must find every revision of this one
        newer than anything it holds (it then catches up, never resets). Held bundles are served to catch-ups but
        vouched for only after this process recomputes them (the next pass recomputes every one); interest that
        has not expired carries over. Raises ValueError on a snapshot of another shape (the caller starts fresh)."""
        if snap.get('version') != SNAPSHOT_VERSION or not isinstance(snap.get('epoch'), str):
            raise ValueError('not a session-panel feed snapshot of this version')
        now = self.clock()
        interest = {str(s): float(until) for s, until in (snap.get('interest') or {}).items() if float(until) > now}
        bundles = {}
        for s, held in (snap.get('bundles') or {}).items():
            if str(s) not in interest:
                continue  # nobody watches it any more: a later registration computes it fresh
            bundle = held['bundle']
            rows = list(bundle['tasks']) + list(bundle['done'])
            bundles[str(s)] = {'rev': int(held['rev']), 'hash': str(held['hash']), 'bundle': bundle,
                               'refs': set(bundle['cards']) | {str(t.get('id')) for t in rows if t.get('id')},
                               'lineage': set(bundle['lineage']), 'source_stale': False}
        with self._writer('restore'), self.lock:
            self.epoch = snap['epoch']
            self.rev = int(snap['rev']) + RESTORE_REV_GAP
            self.bundles = bundles
            self.interest = interest
            self._watch_tokens = {s: object() for s in interest}
            self._retry = set(bundles)
            self._carried = set(bundles)
            self._reissue = set(bundles)
            top = max((max(int(b['bundle'].get(k) or 0) for k in ('computed_seq', 'canonical_seq')) for b in bundles.values()), default=0)
            self._seq = itertools.count(top + 1)
            self._publish_wire()
        return self

    def _store(self, session: str, bundle: dict, *, canonical_only: bool = False, board_current: bool = False,
               observation_only: bool = False, projection_module=None, reducer=None, authoritative: bool = False) -> bool:
        """Keep `bundle`; True when it differs from what clients last got (it took a new revision)."""
        observation = (getattr(self, '_external_observations', None) or {}).get(session)
        if not authoritative and observation and not board_current and bundle.get('computed_seq', 0) <= observation[0]:
            bundle = self.readers.advance_external(bundle, observation[1])
            bundle = {**bundle, 'model': (reducer or _model().reduce_panel)(bundle, now=self.clock(), wire=True)}
        prev = self.bundles.get(session)
        if canonical_only and (not prev or prev['bundle']['lineage'] != bundle['lineage']):
            return False  # expired/re-watched or reparented while the cheap read was in flight
        board_refresh = not observation_only and (authoritative or board_current or not prev or bundle.get('computed_seq', 0) > prev['bundle'].get('computed_seq', 0))
        merged = canonical_only
        if prev and not authoritative:
            old = prev['bundle']
            if prev.get('board_admission') and (canonical_only or not board_current):
                # Once opted in, an unversioned full/legacy result has NO board
                # authority, even with a later computed_seq. Canonical data is
                # independently merged, including current source-read errors.
                if old['lineage'] != bundle['lineage'] or old.get('canonical_seq', 0) >= bundle.get('canonical_seq', 0):
                    return False
                bundle = (projection_module or _projection()).canonical_overlay(old, bundle)
                board_refresh, merged = False, True
            elif not board_current and old.get('computed_seq', 0) > bundle.get('computed_seq', 0):
                if old.get('canonical_seq', 0) >= bundle.get('canonical_seq', 0) or old['lineage'] != bundle['lineage']:
                    return False
                bundle = {**old, **{k: bundle[k] for k in ('canonical_seq', 'outcome_json', 'requests')}}
                merged = True
            elif old.get('canonical_seq', 0) > bundle.get('canonical_seq', 0) and old['lineage'] == bundle['lineage']:
                bundle = ((projection_module or _projection()).canonical_overlay(bundle, old) if board_current else
                          {**bundle, **{k: old[k] for k in ('canonical_seq', 'outcome_json', 'requests')}})
                merged = True
            if not board_current and not board_refresh and bundle.get('canonical_seq') and all(bundle.get(k) == old.get(k) for k in ('outcome_json', 'requests', 'input_errors')):
                prev['bundle'] = {**old, 'canonical_seq': max(old.get('canonical_seq', 0), bundle.get('canonical_seq', 0))}
                return False  # no re-reduction of unchanged canonical data every second
            if merged and bundle.get('canonical_seq'):
                # Evaluate ageing execution against current time, but retain computed_at:
                # a fresh decision is never fresh board evidence or a renewed lease.
                bundle = {**bundle, 'model': (reducer or _model().reduce_panel)(bundle, now=self.clock(), wire=True)}
        h = _hash(bundle)
        rows = bundle['tasks'] + bundle['done']
        refs = set(bundle['cards']) | {str(t.get('id')) for t in rows if t.get('id')}
        # Core's busy fallback succeeds with last-observed rows, not an exception. Keep them visible,
        # but never vouch for that source as fresh; retry even when its file stamps/cursor stay unchanged.
        source_stale = bool(bundle.get('board_errors')) or any(t.get('stale') or t.get('partial_source_unknown') or t.get('_execution_error') for t in rows)
        if source_stale and not board_current:
            self._retry.add(session)
        # Recomputed: its bundle is this feed's own now. One restored from another process takes a new revision even
        # when unchanged: a client may hold a newer copy the old process pushed after it saved (a restart's
        # overlap), which a catch-up would otherwise never replace while this feed vouched for it.
        reissue = board_refresh and session in self._reissue
        if board_refresh:
            self._carried.discard(session)
            self._reissue.discard(session)
        if prev and prev['hash'] == h and not reissue:
            prev.update(bundle=bundle, refs=refs, lineage=set(bundle['lineage']), source_stale=source_stale)
            return False
        self.rev += 1
        self.bundles[session] = {'rev': self.rev, 'hash': h, 'bundle': bundle, 'refs': refs,
                                 'lineage': set(bundle['lineage']), 'source_stale': source_stale,
                                 **({k: prev[k] for k in ('board_admission', 'board_projection') if k in prev} if prev else {})}
        return True

    def _publish(self, computed: dict[str, dict], *, heartbeat: bool = False, canonical_only: bool = False,
                 age_only: bool = False, observations: Optional[dict] = None, watch_tokens=None,
                 _projections=None, _snapshot_store=None, _results=None, authoritative: bool = False) -> dict[str, dict]:
        """Store what was computed and push one batch (or a heartbeat when nothing changed and one is due)."""
        owner = getattr(self, '_source_coordinator', None)
        if owner is not None and not canonical_only and not age_only and _projections is None:
            selected = {s: getattr(b, 'projection') for s, b in computed.items() if hasattr(b, 'projection')}
            if selected:
                _projections, _snapshot_store, _results = selected, owner.store, {}
                computed = {s: b for s, b in computed.items() if s not in selected}
        # Resolve source-backed modules before borrowing the store publication
        # lock. Only immutable inputs and loaded pure helpers enter that borrow.
        projection_module = _projection() if _projections is not None else None
        projection_reducer = _model().reduce_panel if _projections is not None else None
        kind = ('decisions' if canonical_only else 'age' if age_only else 'heartbeat' if heartbeat
                else 'board_projection' if _projections is not None else 'full')
        with self._writer(kind):
            if self.stopped.is_set():
                if _results is not None:
                    _results.update({s: projection_module.PublishResult('stopped') for s in _projections})
                return {}  # stopped (and possibly resumed by a successor): its revision space is no longer ours
            prev = self.rev
            with self.lock:
                if watch_tokens is not None:
                    computed = {s: b for s, b in computed.items()
                                if self._watch_tokens.get(s) is watch_tokens.get(s)
                                and s in self.bundles and self.bundles[s]['bundle']['lineage'] == b['lineage']}
                if age_only:
                    # Reduce exactly the current publication while holding the writer
                    # lock. An age pass cannot race/replace a newer decision or change
                    # input clocks, component sequences, carried trust or retry state.
                    changed = {}
                    reducer = _model().reduce_panel
                    now = self.clock()
                    for s, held in list(self.bundles.items()):
                        if s not in self.interest or 'model' not in held['bundle']:
                            continue
                        base = held['bundle']
                        if observations is not None and not held.get('board_admission'):
                            # Opted-in clocks have their own vector/parent CAS.
                            # Legacy observations may only re-age that bundle;
                            # they cannot mutate its source materialization.
                            observation = observations.get(s)
                            latest = (getattr(self, '_external_observations', None) or {}).get(s)
                            if (observation is None or base.get('computed_seq', 0) > observation[0]
                                    or (latest is not None and latest[0] > observation[0])):
                                continue  # a later source capture owns a newer observation
                            base = self.readers.advance_external(base, observation[1])
                        bundle = {**base, 'model': reducer(base, now=now, wire=True)}
                        digest = _hash(bundle)
                        if digest != held['hash']:
                            self.rev += 1
                            self.bundles[s] = {**held, 'bundle': bundle, 'hash': digest, 'rev': self.rev}
                            changed[s] = bundle
                else:
                    for s, b in computed.items():
                        flight = self._flights.get(s)
                        if flight is not None and flight.bundle is b:
                            self._flights.pop(s)
                    changed = {s: self.bundles[s]['bundle'] for s, b in computed.items() if s in self.interest and self._store(s, b, canonical_only=canonical_only, authoritative=authoritative)}
                    for s, projection in (_projections or {}).items():
                        with _snapshot_store.publication(projection.admission) as current:
                            held_projection = (self.bundles.get(s) or {}).get('board_projection')
                            if (not current or s != projection.admission.chat.session_id
                                    or projection.feed_epoch != self.epoch or projection.feed_owner is not self._projection_owner
                                    or s not in self.interest or projection.watch_token is None
                                    or self._watch_tokens.get(s) is not projection.watch_token
                                    or (projection.observation_parent is not None and (held_projection is None
                                        or held_projection.publication_token is not projection.observation_parent))
                                    or (projection.observation_parent is None and held_projection is not None
                                        and held_projection.feed_epoch == self.epoch
                                        and held_projection.feed_owner is self._projection_owner
                                        and held_projection.watch_token is projection.watch_token
                                        and held_projection.observation_parent is not None
                                        and held_projection.admission == projection.admission)):
                                _results[s] = projection_module.PublishResult('superseded')
                                continue
                            module = sys.modules[type(_snapshot_store).__module__]
                            b = module.thaw(projection.bundle)
                            did_change = self._store(s, b, board_current=True,
                                                     observation_only=projection.observation_parent is not None,
                                                     projection_module=projection_module, reducer=projection_reducer)
                            self.bundles[s]['board_admission'] = projection.admission
                            self.bundles[s]['board_projection'] = projection
                            _results[s] = projection_module.PublishResult('stored' if did_change else 'no_change', did_change,
                                revision=self.bundles[s]['rev'])
                            if did_change:
                                changed[s] = self.bundles[s]['bundle']
                self._publish_wire()
                receipt_entries = self._wire[2] if self.receipt_log is not None else None
            now = self.clock()
            if changed or (heartbeat and now - self.last_emit >= self.heartbeat_s):
                with self.lock:
                    # The chats this push vouches for: held, watched, and last computed without a failure. Clients
                    # re-stamp only these (a dropped or failing chat is never confirmed by a heartbeat).
                    fresh = sorted(s for s in self.interest if self._bundle_fresh(s, now))
                try:
                    payload = {'epoch': self.epoch, 'prev': prev, 'rev': self.rev,
                               'bundles': {s: self._client_bundle(s, b, s in fresh) for s, b in changed.items()}, 'fresh': fresh,
                               **({} if changed else {'heartbeat': True})}
                    if self.receipt_log is None:
                        self.emit(payload)
                    else:
                        assert receipt_entries is not None
                        custody = [receipt_entries[s][3].publication for s in changed
                                   if s in receipt_entries and len(receipt_entries[s]) == 4
                                   and json.loads(receipt_entries[s][2]) == payload['bundles'].get(s)]
                        emitter = getattr(self.emit, 'with_receipt', None)
                        if emitter is None:
                            self.emit(payload)
                            self.receipt_log.record('emit_error', error='no_exact_transformed_payload_adapter',
                                                    sessions=list(changed))
                        else:
                            actual, callback_started, callback_completed = emitter(payload)
                            transformed = json.loads(actual)
                            included = [p for p in custody if p['session'] in (transformed.get('bundles') or {})]
                            self.receipt_log.record('emit_success', publications=included,
                                callback_started_ns=callback_started, callback_completed_ns=callback_completed,
                                observation='server_emission',
                                kind='model' if transformed.get('bundles') else 'invalidation_only' if transformed.get('sessions') else 'heartbeat',
                                **self.receipt_module.payload_fields(actual))
                    self.last_emit = now
                    if _results is not None:
                        for s in changed:
                            if s in _results:
                                prior_result = _results[s]
                                _results[s] = projection_module.PublishResult(prior_result.disposition, prior_result.changed,
                                    True, prior_result.revision)
                    if changed:
                        self._note_publish(changed)
                except Exception as exc:  # no client / transport down: catch-up remains possible
                    if self.receipt_log is not None:
                        self.receipt_log.record('emit_error', error=type(exc).__name__, sessions=list(changed))
                    log.debug('session-panel feed: push failed', exc_info=True)
            return changed

    def _note_publish(self, changed: dict) -> None:
        """Wall-clock record of a push that carried bundles: the latest, and the last RECENT_PUBLISHES with the
        chats each carried and the start of the pass that read them (None for a push outside a pass)."""
        timing = self.__dict__.setdefault('timing', {})
        at = time.time()
        current = getattr(self, '_pass', None)
        entry = {'rev': self.rev, 'at': at, 'sessions': sorted(changed),
                 'pass_started_at': current[1] if current and current[0] == threading.get_ident() else None,
                 # Identity of the exact successfully emitted model, not the later
                 # disk checkpoint or a newer mutable bundle read by an observer.
                 'models': {s: {'revision': (b.get('model') or {}).get('revision'),
                                'computed_seq': b.get('computed_seq'),
                                'computed_at': b.get('computed_at')}
                            for s, b in changed.items()}}
        timing.update(last_publish_at=at, last_publish_rev=self.rev,
                      recent=(list(timing.get('recent') or []) + [entry])[-RECENT_PUBLISHES:])

    def _drain(self) -> None:
        """Tick thread: take in what readers registered since the last look. A chat with no bundle goes to the head
        of the urgent list (the newest request first; inside one request the client's own order, focused chat
        first)."""
        with self.lock:
            for _ in range(MAX_WATCHED):
                try:
                    wanted = self.watch(self._registrations.get_nowait())
                except queue.Empty:
                    break
                cold = [s for s in wanted if s in self.interest and s not in self.bundles]
                self._urgent = cold + [s for s in self._urgent if s not in cold]
                if cold and cold[0] == wanted[0]:  # the chat its client is looking at leads the request
                    self._focus = [cold[0]] + [s for s in self._focus if s != cold[0]]

    def _panel_dirty(self, watched: list[str]) -> set[str]:
        """The watched chats whose outcome/request stamp moved: one batched read when the readers have one."""
        dirty: set[str] = set()
        batch, single = getattr(self.readers, 'panel_stamps', None), getattr(self.readers, 'panel_stamp', None)
        if not callable(batch) and not callable(single):
            return dirty
        # Change-driven: no database is opened while neither file behind these stamps has moved.
        files = getattr(self.readers, 'panel_files_stamp', None)
        moved = files() if callable(files) else None
        if moved is not None and moved == self._panel_files and all(s in self._panel_stamps for s in watched):
            return dirty
        with self.lock:
            lineages = {s: list(self.bundles.get(s, {}).get('bundle', {}).get('lineage', [s])) for s in watched}
        rows = batch(list(dict.fromkeys(i for ids in lineages.values() for i in ids))) if callable(batch) else None
        captured = {}
        for s, ids in lineages.items():
            stamp = tuple(rows[i] if rows is not None else single(i) for i in ids)
            if self._panel_stamps.get(s) != stamp:
                dirty.add(s)
            captured[s] = stamp
        # Publish the cursor only after the entire capture succeeds. A transient
        # failure (including incomplete batch results) must leave the same source
        # event eligible for an explicit retry, without requiring another write.
        self._panel_stamps.update(captured)
        self._panel_files = moved
        return dirty

    def _full_dirty(self, watched, now: float) -> set:
        """(lock held) The chats the safety-net pass recomputes with no event behind them. Open cards carry run
        evidence that expires and lines that count time: those chats every FULL_EVERY_S. A chat with none changes
        only through card events or a link adopted with no event: every QUIET_FULL_EVERY_S."""
        return {s for s in watched if s not in self.bundles
                or any(self.bundles[s]['bundle'].get(k) for k in OPEN_KEYS)
                or now - self.bundles[s]['bundle'].get('computed_at', 0) >= QUIET_FULL_EVERY_S}

    def _backoff(self, attempt: int) -> Optional[float]:
        """When failed work is next retried without a source event: RETRY_STEPS times, then not at all."""
        return self.clock() + 0.1 * (2 ** (attempt - 1)) if attempt <= RETRY_STEPS else None

    def _spend(self, lane: str, failing: Iterable[str], keep: Iterable[str] = ()) -> Optional[float]:
        """One retry budget per chat and lane. Each chat in `failing` spends one retry; a chat in neither
        `failing` nor `keep` has succeeded or gone and starts afresh. Returns when the failing chats that
        still have retries left are next due, None when none has: a chat that fails forever never spends
        another chat's retries, and never keeps a timer alive on its own."""
        budgets = self.__dict__.setdefault('_budgets', {})
        old = budgets.get(lane) or {}
        failing = set(failing)
        counts = {s: old[s] for s in keep if s in old and s not in failing}
        counts.update((s, old.get(s, 0) + 1) for s in failing)
        budgets[lane] = counts
        live = [counts[s] for s in failing if counts[s] <= RETRY_STEPS]
        return self._backoff(min(live)) if live else None

    def _due(self, now: float) -> tuple:
        """A cold, unattempted registration is due; time alone never admits DB work.

        Failed inputs remain unvouched until a source event or explicit request.
        Held presentation ages separately, without renewing input observations.
        """
        # Source failure is not a source event. Keep its stale/unvouched state,
        # but don't turn canonical unknown evidence into an endless two-second
        # read loop. Source writes (or a new explicit cold request) admit retry.
        # Time-derived presentation is reduced from held inputs by beat().
        with self.lock:
            missing = any(s not in self.bundles and s not in self.failures for s in self.interest)
        ats = [at for at in (getattr(self, n, None) for n in
                             ('_admission_retry_at', '_hydrate_retry_at', '_reconcile_retry_at')) if at is not None]
        retry_wait = max(0.0, min(ats) - now) if ats else None
        with self.lock:
            # Failed admission owns its finite retry clock. Keep pending work
            # and successful timestamps intact, but never let an overdue safety
            # or trailing deadline bypass backoff (including exhaustion).
            producer_ats = []
            if not getattr(self, '_admission_failures', 0):
                producer_ats = [at for s, at in self._producer_pending.items() if s in self.interest]
                safety = float(getattr(self.readers, 'reconcile_safety_s', 0))
                if safety:
                    producer_ats.extend(self._producer_at[s] + safety for s in self.interest
                                        if s in self._producer_at)
        if producer_ats:
            wait = max(0.0, min(producer_ats) - now)
            retry_wait = wait if retry_wait is None else min(retry_wait, wait)
        if getattr(self, '_producer_carry', None) and not getattr(self, '_admission_failures', 0):
            retry_wait = 0.0  # producer work a budgeted pass did not reach runs next, not on a source write
        decide_at = getattr(self, '_decision_retry_at', None)
        return (0.0 if missing else retry_wait), (None if decide_at is None else max(0.0, decide_at - now))

    def idle_wait(self) -> float:
        """How long the event-driven loop sleeps when no file is written: to the liveness heartbeat, or sooner when
        `_due` says a pass will be due before it."""
        waits = [w for w in self._due(self.clock()) if w is not None]
        return max(MIN_IDLE_WAIT_S, min([self.heartbeat_s, *waits]))

    def beat(self) -> int:
        """Between data changes: registrations are renewed, expired interest dropped and a heartbeat pushed when
        due. Reads no database and computes nothing: a chat that needs a bundle, a held chat whose time-derived
        state is due (`_due`) or a pending retry wakes a pass instead."""
        self._drain()
        now = self.clock()
        with self.lock:
            self._expire(now)
            self._publish_wire()
        due, decide = self._due(now)
        if due is not None and due <= 0:
            self._wake.set()
        if decide is not None and decide <= 0:
            self._decision_retry_at = None  # handed to the decision lane once; its pass re-arms on failure
            self._decide.set()
        return len(self._publish({}, heartbeat=True, age_only=now - self.last_emit >= self.heartbeat_s))

    def _producer_order(self, todo: list[str], wanted: set) -> list[str]:
        """Producer order under a pass budget: chats on a mounted panel, then chats with fresh source
        evidence, then work still owed (carried, deferred), oldest producer run first."""
        now = self.clock()
        with self.lock:
            visible = {s for s, expires in self._panel_subscribers.values() if expires > now}
            owed = set(self._reconcile_deferred) | set(getattr(self, '_producer_carry', ()))
        at = self._producer_at
        return sorted(todo, key=lambda s: (s not in visible, s in owed or s not in wanted,
                                           at.get(s, float('-inf'))))

    def _run_producer(self, sessions, **kwargs):
        try:
            with accounted_pass('session_panel.reconcile_sources') as acct:
                left = self.readers.reconcile_sources(sessions, **kwargs)
                timings = getattr(self.readers, 'reconcile_timings', ())
                acct.rows(len(timings))
                acct.change(any(not row.get('unchanged') for row in timings))
                acct.fallback(bool(left))
                if timings and all(row.get('unchanged') for row in timings):
                    acct.skip()
                return left
        finally:
            # Rollback of a failed capture must not undo the writer's floor.
            with self.lock:
                self._producer_run_at.update((s, self.clock()) for s in sessions if s in self.interest)

    def _produce(self, watched: list[str], source_seen: Any, admitted: Iterable[str]) -> None:
        """Run the source producer for the admitted chats. A scoped reader's other chats were checked against
        the same source stamp and found unaffected, so they settle on it unreconciled."""
        scoped = bool(getattr(self.readers, 'reconcile_scoped', False))
        wanted = set(admitted)
        began = time.monotonic()
        if scoped:
            todo = [s for s in watched if s in wanted]
        else:
            todo = [s for s in watched if s not in self._reconcile_stamps
                    or self._reconcile_stamps[s] != source_seen or s in wanted]
        # Leading edge plus an owed trailing pass, not a quiet-period debounce:
        # writes arriving during the floor can never move its deadline forward.
        now = self.clock()
        floor = float(getattr(self.readers, 'reconcile_min_interval_s', 0))
        with self.lock:
            blocked = {s for s in todo if now < self._producer_run_at.get(s, float('-inf')) + floor}
            for s in blocked:
                self._producer_pending[s] = self._producer_run_at[s] + floor
            todo = [s for s in todo if s not in blocked]
            for s in todo:
                self._producer_pending.pop(s, None)
        left: set = set()
        unreached: set = set()
        if self.__dict__.get('_producer_carry') is None:
            self._producer_carry = set()
        carry = self._producer_carry
        carry.difference_update(blocked)  # the floor deadline, not an immediate carry wake, owns these
        if todo:
            # A reader that isolates per-chat failures returns the chats it could not finish; one that
            # raises fails the pass (tick() then restores every cursor).
            if _takes_deadline(self.readers):
                # The producer runs before any hydration in a pass. Unbounded, a slow canonical writer
                # (46 chats at 5-50 s each in a contended serve, 2026-11-24: 900 s) held every panel's
                # presentation for 20+ min while the row stripe, computed elsewhere, stayed live. Bounded,
                # the chats not reached carry to the very next pass, most-needed first.
                todo = self._producer_order(todo, wanted)
                left = {s for s in (self._run_producer(
                    todo, deadline=time.monotonic() + PRODUCER_PASS_BUDGET_S) or ()) if s in todo}
                unreached = set(getattr(self.readers, 'reconcile_unreached', None) or ()) & set(todo)
                left -= unreached
            else:
                left = {s for s in (self._run_producer(todo) or ()) if s in todo}
        with self.lock:
            settle = watched if scoped else todo
            self._reconcile_stamps.update((s, source_seen) for s in settle
                                          if s in self.interest and s not in unreached)
            self._producer_at.update((s, self.clock()) for s in todo
                                     if s in self.interest and s not in unreached)
            carry.difference_update(todo)
            carry.update(s for s in unreached if s in self.interest)
            todo = [s for s in todo if s not in unreached]
            self._reconcile_deferred = {s for s in (self._reconcile_deferred - set(todo)) | left
                                        if s in self.interest}
            waiting = set(self._reconcile_deferred)
        if todo:
            retry_at = self._spend('reconcile', left.intersection(waiting), keep=waiting)
            if left or not waiting:
                self._reconcile_retry_at = retry_at
        self._producer_seen = source_seen
        self.__dict__.setdefault('timing', {}).update(producer_s=round(time.monotonic() - began, 4),
                                                      producer_chats=len(todo),
                                                      producer_readers=getattr(self.readers, 'reconcile_timings', []))

    def queue_keyed(self, sessions):
        with self.lock:
            wanted = set(sessions) & set(self.interest)
            if not wanted:
                return
            lane = getattr(self, '_board_lane', None)
            if lane is not None:
                lane._admit(wanted, time.time())
                lane.service_wake.set()
            self.__dict__.setdefault('_keyed_pending', set()).update(wanted)
            self._wake.set()

    def refresh_keyed(self, sessions=None):
        """Hydrate only admitted keys, without consuming global source cursors.

        Canonical reconciliation remains a writer on the feed thread, never a
        reducer side effect. Publication retains the existing revision fence.
        """
        with self.lock:
            if sessions is None:
                sessions = self.__dict__.pop('_keyed_pending', set())
            sessions = sessions & set(self.interest)
            lane = getattr(self, '_board_lane', None)
            if lane is not None and lane.visible_only:
                visible = {s for s, expires in self._panel_subscribers.values() if expires > self.clock()}
                self._board_dirty.update(sessions - visible)
                self._board_failures.update(sessions - visible)
                sessions &= visible
        if not sessions:
            return
        try:
            if callable(getattr(self.readers, 'reconcile_sources', None)):
                self._produce(sorted(sessions), self._producer_seen, set(sessions))
            self._publish(self._compute_all(sorted(sessions), refresh=True))
            with self.lock:
                self._retry.update(s for s in sessions if s in self.failures)
                failed = self._retry.intersection(sessions)
            if failed:
                self._hydrate_retry_at = self._spend('hydrate', failed, keep=set(self._retry))
        except Exception:
            with self.lock:
                self._retry.update(sessions)
            self._admission_retry_at = self.clock() + RETRY_EVERY_S
            raise

    def _producer_admission(self, watched, source_seen, redo, source_dirty, panel_dirty, now, *, lineage=None):
        """Shared legacy/snapshot canonical admission; source reads never authorize consent.

        Keep source-before-write, owed/deferred scopes and the 60s ceiling on
        REAL source changes. Snapshot callers supply acquired lineage, not another
        per-chat board hydration or a newer synthetic source clock.
        """
        read_lineage = self.readers.lineage if lineage is None else lineage
        moved = source_seen != self._producer_seen
        known = self.__dict__.setdefault('_producer_lineage', {})
        safety = float(getattr(self.readers, 'reconcile_safety_s', 0))
        timed_producer = {s for s in watched
                          if (s in self._producer_pending and now >= self._producer_pending[s])
                          or (safety and now - self._producer_at.get(s, now) >= safety)}
        admitted = (set(redo) | timed_producer | set(source_dirty)
                    | {s for s in watched if s not in self._reconcile_stamps}
                    | (getattr(self, '_producer_carry', set()) & set(watched)))
        owed = self.__dict__.setdefault('_producer_owed', set())
        owed.update(panel_dirty)
        lineages = {}
        if moved:
            admitted |= {s for s in watched
                         if now - self._producer_at.get(s, float('-inf')) >= PRODUCER_FULL_EVERY_S}
            with self.lock:
                admitted |= self._reconcile_deferred
                admitted |= {s for s in owed if s in self.interest}
            lineages = {s: tuple(read_lineage(s) or [s]) for s in watched}
            admitted |= {s for s, ids in lineages.items() if known.get(s) != ids}
        else:
            lineages = {s: tuple(read_lineage(s) or [s]) for s in admitted if s not in known}
        if admitted or moved:
            self._produce(watched, source_seen, admitted)
            owed.difference_update(admitted)
            with self.lock:
                known.update((s, ids) for s, ids in lineages.items() if s in self.interest)
            return self._panel_dirty(watched)
        return set()

    def tick(self) -> int:
        with accounted_pass('session_panel.feed_tick') as acct:
            result = self._tick_transaction()
            acct.rows(result)
            if result:
                acct.change()
            else:
                acct.skip()
            return result

    def _tick_transaction(self) -> int:
        """Settle a source pass atomically, with finite recovery of unread changes."""
        import copy
        names = ('_panel_stamps', '_panel_files', '_stamps', '_board_stamps',
                 '_external_stamps', '_external_captures', '_external_observations', '_cursor', '_reconcile_stamps', '_producer_seen',
                 '_producer_lineage', '_producer_owed', '_producer_at', '_producer_carry', '_producer_pending')
        saved = {name: copy.deepcopy(getattr(self, name, None)) for name in names}
        self._admission_retry_at = None
        self._hydrate_retry_at = None  # this pass is the retry; it re-arms below when a chat still fails
        self._attempted = set()
        began, began_wall = time.monotonic(), time.time()
        self._pass = (threading.get_ident(), began_wall)
        try:
            result = self._tick()
        except Exception:
            self._pass = None
            # A later reader can fail after an earlier one captured its stamp.
            # Never consume that event, or keep vouching for the held old bundle.
            for name, value in saved.items():
                setattr(self, name, value)
            with self.lock:
                self._retry.update(self.interest)
                # Cold registrations must not bypass the finite retry budget.
                for s in self.interest:
                    if s not in self.bundles:
                        self.failures.setdefault(s, {'count': 0, 'at': self.clock(), 'error': 'source capture failed'})
            attempt = getattr(self, '_admission_failures', 0) + 1
            self._admission_failures = attempt
            self._admission_retry_at = self._backoff(attempt)
            raise
        self._pass = None
        self._admission_failures = 0
        self.__dict__.setdefault('timing', {}).update(
            ticks=self.timing.get('ticks', 0) + 1, tick_started_at=began_wall,
            tick_duration_s=round(time.monotonic() - began, 4))
        # A chat whose hydration failed was caught inside the pass, after its source event was consumed.
        # Keep it unvouched and due again: the same event must not need another write to be shown.
        # So is one that succeeded with stale rows (core's busy fallback) or joined an older read: it is
        # left in _retry, and nothing but this retry would read it again.
        with self.lock:
            failed = {s for s in self._attempted if s in self.failures and s in self.interest}
            self._retry.update(failed)
            unsettled = {s for s in self._attempted if s in self._retry and s in self.interest}
            waiting = {s for s in self._retry | set(self.failures) if s in self.interest}
        self._hydrate_retry_at = self._spend('hydrate', unsettled, keep=waiting)
        return result

    def _tick(self) -> int:
        """One pass: recompute what changed and push it, else a heartbeat when due. Returns bundles pushed.
        Stored and pushed in chunks, the chats a reader is waiting for first: a click on a chat with no bundle is
        answered after its own compute, not after a whole sweep (requests arriving mid-pass are taken in between
        chunks)."""
        owner = getattr(self, '_source_coordinator', None)
        if owner is not None:
            return owner.tick()
        now = self._last_pass = self.clock()
        self._wake.clear()  # before the drain: a request or write after this point wakes the next pass
        self._cold.clear()
        with self.lock:
            self._expire(now)
        self._drain()
        # Consumed here, whatever this pass goes on to do: a due retry time that outlived its chats
        # must never keep the idle loop awake.
        retry_at = getattr(self, '_reconcile_retry_at', None)
        redo: set = set()
        if retry_at is not None and now >= retry_at:
            redo, self._reconcile_retry_at = set(self._reconcile_deferred), None
        with self.lock:
            watched = list(self.interest)
            self._publish_wire()
        if not watched:
            return 0
        reconcile = getattr(self.readers, 'reconcile_sources', None)
        source_stamp = getattr(self.readers, 'reconciliation_stamp', None)
        reconcile_enabled = callable(reconcile) and callable(source_stamp)
        # Captured BEFORE producer writes. Its own action-store commit is not
        # a source event; a concurrent real source write remains eligible.
        source_seen = source_stamp() if callable(source_stamp) else None
        # A reader with per-chat scope (`reconcile_scoped`) admits the producer only for the chats whose own
        # inputs moved (below, once the per-chat dirty sets are known). Others keep the conservative contract:
        # any source write admits every watched chat.
        scoped = bool(reconcile_enabled and getattr(self.readers, 'reconcile_scoped', False))
        safety = float(getattr(self.readers, 'reconcile_safety_s', 0)) if scoped else 0
        timed_producer = {s for s in watched
                          if (s in self._producer_pending and now >= self._producer_pending[s])
                          or (safety and now - self._producer_at.get(s, now) >= safety)}
        if reconcile_enabled and not scoped:
            self._produce(watched, source_seen, redo)
        panel_dirty = self._panel_dirty(watched)
        stamps = self.readers.stamps()
        external = getattr(self.readers, 'external_stamps', None)
        external_seen = external() if callable(external) else None
        external_dirty = external_seen != getattr(self, '_external_stamps', None)
        full = self._stamps is None  # bootstrap only; wall time never authorizes a DB sweep
        with self.lock:
            retry = self._retry.intersection(watched)
            missing = any(s not in self.bundles for s in watched)
        producer_due = scoped and bool(redo or timed_producer or any(s not in self._reconcile_stamps for s in watched)
                                       or source_seen != self._producer_seen)
        if stamps == self._stamps and not full and not retry and not panel_dirty and not missing and not producer_due:
            self._publish({}, heartbeat=True)
            return 0
        # Board events are read only when a board file moved (or on the safety-net pass): state.db is written all
        # day by every chat, and each of those writes must not open every board.
        boards = getattr(self.readers, 'board_stamps', None)
        seen = boards() if callable(boards) else None
        semantic = getattr(self.readers, 'board_changes', None)
        board_changes = None
        board_dirty = seen is None or seen != self._board_stamps or full or self._cursor is None
        if board_dirty:
            if callable(semantic):
                board_changes, self._cursor = semantic(self._cursor)
                changed_cards = {}
            else:  # injectable/older readers retain their event-only contract
                changed_cards, self._cursor = self.readers.changed_cards(self._cursor)
        else:
            changed_cards = {}
        self._board_stamps = seen
        self._stamps = stamps
        self._external_stamps = external_seen
        if full:
            self._last_full = now
            with self.lock:
                dirty = self._full_dirty(watched, now)
            touched = {v for v in changed_cards.values() if v}
            with self.lock:
                for s in watched:
                    b = self.bundles.get(s)
                    if b and (b['refs'] & set(changed_cards) or b['lineage'] & touched):
                        dirty.add(s)
        else:
            touched = {v for v in changed_cards.values() if v}
            with self.lock:
                dirty = {s for s in watched if s not in self.bundles}
                for s in watched:
                    b = self.bundles.get(s)
                    if b and (b['refs'] & set(changed_cards) or b['lineage'] & touched):
                        dirty.add(s)
        if board_changes is not None:
            with self.lock:
                dirty.update(s for s in watched if s in self.bundles and board_changes.affects(self.bundles[s]))
        # File activity is only admission to capture, not proof that every
        # session's inputs changed. Compare canonical external rows per lineage.
        # Replica observations also live in board shadows. A pull may change only
        # their sync bookkeeping, with no hub/ledger write or task revision.
        if external_dirty or board_dirty:
            capture = getattr(self.readers, 'external_capture', None)
            if callable(capture):
                with self.lock:
                    capture_seq = next(self._seq)
                    held = {s: {'lineage': set(self.bundles[s]['lineage']),
                                'refs': set(self.bundles[s]['refs']),
                                'bundle': self.bundles[s]['bundle']}
                            if s in self.bundles else {'lineage': {s}, 'refs': set()}
                            for s in watched}
                captured = capture(held)
                previous = getattr(self, '_external_captures', None) or {}
                semantic = getattr(self.readers, 'external_semantic', lambda value: value)
                dirty.update(s for s in watched if semantic(captured[s]) != semantic(previous.get(s)))
                diagnostic_stale = getattr(self.readers, 'diagnostic_stale', None)
                if callable(diagnostic_stale):
                    dirty.update(diagnostic_stale(held))
                if callable(getattr(self.readers, 'advance_external', None)):
                    observations = {s: (capture_seq, captured[s]) for s in watched
                                    if s not in dirty and captured[s] != previous.get(s)}
                    with self.lock:
                        old = getattr(self, '_external_observations', None) or {}
                        self._external_observations = {**old, **{s: value for s, value in observations.items()
                                                               if s not in old or value[0] > old[s][0]}}
                    self._publish({}, age_only=True, observations=observations)
                self._external_captures = captured
            elif external_dirty:  # Older readers cannot attest unchanged external inputs.
                dirty.update(watched)
        source_dirty = set(dirty)  # chats whose own source inputs this pass read as changed
        dirty.update(retry)
        dirty.update(panel_dirty)
        if scoped:
            dirty.update(self._producer_admission(watched, source_seen, redo, source_dirty, panel_dirty, now))
        if not dirty:
            return len(self._publish({}, heartbeat=True))
        started = time.monotonic()
        pushed, pending, tried = 0, set(dirty), set()
        while pending and not self.stopped.is_set():
            if tried:
                self._drain()  # a chat asked for while the last chunk computed: next, ahead of the sweep
            with self.lock:
                # Once per pass each: a chat whose compute failed is retried by the next pass, never in a loop here.
                cold = [s for s in self._urgent if s in self.interest and s not in self.bundles and s not in tried]
                pending = {s for s in pending if s in self.interest}
            self._urgent = cold
            pending.update(cold)
            if not pending:
                break
            self._focus = [s for s in self._focus if s in cold]
            # A focused chat alone (its reader waits for exactly it), then the rest asked for, then the sweep.
            chunk = self._focus[:1] or cold[:FIRST_CHUNK] or sorted(pending)[:CHUNK]
            tried.update(chunk)
            self._attempted.update(chunk)
            pending.difference_update(chunk)
            pushed += len(self._publish(self._compute_all(chunk, refresh=True), heartbeat=True))
        self.last_tick_s = time.monotonic() - started
        return pushed

    # ── catch-up ────────────────────────────────────────────────────────────
    def changes(self, sessions: Iterable[str], *, since: int = 0, epoch: Optional[str] = None) -> dict:
        """Watch `sessions` and return every bundle of theirs newer than `since` (all of them when `epoch` is not
        this process's: the client's revisions mean nothing here). Missing bundles are computed now (and pushed, so
        every other client's revision chain stays unbroken). A request that reached a feed a reload has since resumed
        is answered by its successor (its watch and computed bundles must land in the live feed)."""
        if self._successor is not None:
            return self._current().changes(sessions, since=since, epoch=epoch)
        wanted = self.watch(sessions)
        if getattr(self, '_facts_mode', False):
            self._wake.set()
        reset = epoch != self.epoch
        with self.lock:
            missing = [s for s in wanted if s not in self.bundles]
        if missing and not getattr(self, '_facts_mode', False):
            try:
                self._publish(self._compute_all(missing))
            finally:
                release = getattr(self.readers, 'release', None)
                if callable(release):
                    release()  # request threads keep no connections between catch-ups
        if self._successor is not None:  # resumed while this request computed: what it watched lives there
            return self._current().changes(sessions, since=since, epoch=epoch)
        with self._writer('catchup_selection'), self.lock:
            out = {s: self.bundles[s]['bundle'] for s in wanted
                   if s in self.bundles and (reset or self.bundles[s]['rev'] > since)}
            return {'epoch': self.epoch, 'rev': self.rev, 'reset': reset, 'bundles': out}

    def status(self) -> dict:
        with self.lock:
            now = self.clock()
            ages = {s: round(max(0.0, now - b['bundle']['computed_at']), 3)
                    for s, b in self.bundles.items()}
            visible = {s for s, expires in self._panel_subscribers.values() if expires > now}
            return {'epoch': self.epoch, 'rev': self.rev, 'watched': len(self.interest), 'bundles': len(self.bundles),
                    'oldest_bundle_age_s': max(ages.values(), default=None),
                    'visible_chats': {s: {'age_s': ages.get(s),
                        'computed_at': self.bundles.get(s, {}).get('bundle', {}).get('computed_at')}
                        for s in sorted(visible)},
                    'board_visible_only': getattr(getattr(self, '_board_lane', None), 'visible_only', None),
                    'panel_subscribers': sum(expires > self.clock() for _, expires in self._panel_subscribers.values()),
                    'board_dirty': len(self._board_dirty),
                    'failures': {s: dict(f) for s, f in self.failures.items()}, 'last_tick_s': round(self.last_tick_s, 3), 'timing': dict(getattr(self, 'timing', {})),
                    'decision_failures': sorted(self._decision_failures),
                    'stopped': self.stopped.is_set()}


# A subscription route bills nothing per token; price its tokens at the same model's API list rate, so a done
# card shows what its work would have cost (Codex -> OpenAI, Grok OAuth -> xAI).
_LIST_PRICE_PROVIDER = {'openai-codex': 'openai', 'xai-oauth': 'xai'}
_USAGE_KEYS = ('input_tokens', 'output_tokens', 'cache_read_tokens', 'cache_write_tokens')


def _positive(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0


def _run_cost(usage: dict, route: dict) -> Optional[float]:
    """One run's dollars: the worker's reported ``cost_usd``, else its tokens at the routed model's list price.
    None when neither is known (no usage receipt, or an unpriced model)."""
    if _positive(usage.get('cost_usd')):
        return float(usage['cost_usd'])
    model = str(route.get('model') or '')
    if not model or model.startswith('class:') or not any(_positive(usage.get(k)) for k in _USAGE_KEYS):
        return None
    try:
        from agent.usage_pricing import CanonicalUsage, estimate_usage_cost
        provider = str(route.get('provider') or '')
        n = {k: int(usage.get(k) or 0) for k in _USAGE_KEYS}
        tokens = CanonicalUsage(input_tokens=n['input_tokens'], output_tokens=n['output_tokens'],
                                cache_read_tokens=n['cache_read_tokens'], cache_write_tokens=n['cache_write_tokens'])
        result = estimate_usage_cost(model, tokens,
                                     provider=_LIST_PRICE_PROVIDER.get(provider, provider) or None)
    except Exception:
        return None
    return float(result.amount_usd) if result.amount_usd is not None and result.status == 'estimated' else None


def done_run_stats(conn, ids: Iterable[str]) -> dict[str, dict]:
    """``id -> {runs, run_seconds, cost_usd, priced_runs, usage_runs, tokens}`` for finished cards on one board connection.

    ``runs`` counts every run row (so restarts = runs - 1); ``run_seconds`` sums closed runs' wall time;
    ``cost_usd`` sums the runs whose dollars are known (``priced_runs`` of the ``usage_runs`` that reported
    usage; a class-routed run records no concrete model, so its tokens cannot be priced). ``tokens`` sums all
    reported token counters. Read-only, never raises:
    a board that cannot be read yields no entry, and the panel then shows nothing rather than zeros."""
    ids = [str(i) for i in dict.fromkeys(ids) if i]
    out: dict[str, dict] = {}
    if not ids:
        return out
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if 'task_runs' not in tables:
            return out
        marks = ','.join('?' for _ in ids)
        for tid, runs, secs in conn.execute(
                f'SELECT task_id, COUNT(*), TOTAL(CASE WHEN ended_at >= started_at THEN ended_at - started_at END) '
                f'FROM task_runs WHERE task_id IN ({marks}) GROUP BY task_id', ids):
            out[str(tid)] = {'runs': int(runs), 'run_seconds': int(secs), 'cost_usd': 0.0, 'priced_runs': 0, 'usage_runs': 0, 'tokens': 0}
        if 'task_events' not in tables or not out:
            return out
        usage: dict[tuple, dict] = {}
        route: dict[tuple, dict] = {}
        for tid, run_id, kind, raw in conn.execute(
                f"SELECT task_id, run_id, kind, payload FROM task_events WHERE task_id IN ({marks}) "
                f"AND kind IN ('worker_result', 'run_route') AND run_id IS NOT NULL ORDER BY id", ids):
            try:
                payload = json.loads(raw or '{}') or {}
            except (ValueError, TypeError):
                continue
            if not isinstance(payload, dict):
                continue
            if kind == 'run_route':
                route[(tid, run_id)] = payload
            elif isinstance(payload.get('usage'), dict):
                usage[(tid, run_id)] = payload['usage']  # the newest receipt for a run wins
        for (tid, run_id), item in usage.items():
            if str(tid) not in out:
                continue
            out[str(tid)]['usage_runs'] += 1
            out[str(tid)]['tokens'] += sum(int(item[k]) for k in _USAGE_KEYS if _positive(item.get(k)))
            cost = _run_cost(item, route.get((tid, run_id), {}))
            if cost is not None:
                out[str(tid)]['cost_usd'] += cost
                out[str(tid)]['priced_runs'] += 1
        for stats in out.values():
            stats['cost_usd'] = round(stats['cost_usd'], 2)
    except Exception:
        log.debug('session-panel: done run stats unavailable', exc_info=True)
        return {}
    return out


def with_done_run_stats(rows: list[dict], conn) -> list[dict]:
    """Attach ``run_stats`` to the finished rows of one board (in place); open rows are left alone."""
    done = [r for r in rows if r.get('status') in ('done', 'archived') and r.get('id')]
    stats = done_run_stats(conn, [r['id'] for r in done]) if done else {}
    for row in done:
        if str(row['id']) in stats:
            row['run_stats'] = stats[str(row['id'])]
    return rows


def with_terminal_history(tasks: list[dict], history: list[dict], ids: Iterable[str]) -> list[dict]:
    """user-steps' `/session-cards` union with core's receipt projection (plugins/user-steps/dashboard/plugin_api.py
    `_with_terminal_history`, same rules), so `done` is exactly that route's answer."""
    def identity(row):
        if all(row.get(k) for k in ('gateway', 'profile', 'board', 'id')):
            return '/'.join(str(row[k]) for k in ('gateway', 'profile', 'board', 'id'))
        return row.get('key') or None

    out = list(tasks)
    seen = {identity(row) for row in tasks} - {None}
    wanted = set(ids)
    for row in history:
        if row.get('status') not in ('done', 'archived') or row.get('unfinished') is not False:
            continue
        if not wanted.intersection((row.get('session'), row.get('origin_session'))):
            continue
        if not all(row.get(k) for k in ('gateway', 'profile', 'board', 'id', 'origin_gateway', 'origin_profile')):
            continue
        key = identity(row)
        if key in seen:
            continue
        seen.add(key)
        out.append(dict(row, session_id=row['session'], source='origin-history'))
    return out
