"""Bounded, change-driven board hydration, independent of canonical reconciliation.

The coordinator never joins a slow reader. Urgent mitigation serializes readers
and coalesces each watched chat until three seconds after its last read ends.
No connections, transactions or source snapshots survive a worker invocation.
"""
from __future__ import annotations

import contextlib
import logging
import os
import queue
import threading
import time

log = logging.getLogger('session-panel')

# One worker hydrates every chat that is due together, under one reader and one shared hydration scope
# (`CoreReaders.batch`). Read one at a time, with a new reader each, the 26 chats a Desktop watches took about 30 s
# of every 30 s recovery round in the MacBook gateway (2026-11-23: 256 reads in 315 s, 92% published nothing);
# the same rows read as one batch cost under a second in an idle process. HERMES_PANEL_BOARD_BATCH=0 restores
# one chat per worker. Only the serialized lane batches (capacity 1): the legacy four-worker lane is unchanged.
BATCH_MAX = 16


class BoardLane:
    def __new__(cls, feed, reader_factory, **kwargs):
        with feed.lock:
            owner = getattr(feed, '_source_coordinator', None)
            if owner is not None:
                # A genuine saved predecessor entry never calls today's startup
                # helpers. Registration itself must establish the opted-in owner;
                # manual BoardLane probes still start no service.
                if (getattr(feed, '_board_service_required', False)
                        or getattr(feed, '_board_thread', None) is threading.current_thread()):
                    owner.start_service()
                return owner
        return super().__new__(cls)

    def __init__(self, feed, reader_factory, *, capacity=None):
        self.feed, self.reader_factory = feed, reader_factory
        self.cooldown = 0.0 if os.environ.get(
            'HERMES_PANEL_BOARD_COALESCE', '1').lower() in ('0', 'false', 'off', 'no') else 3.0
        self.capacity = (1 if self.cooldown else 4) if capacity is None else capacity
        self.visible_only = os.environ.get('HERMES_PANEL_BOARD_VISIBLE_ONLY', '1').lower() not in ('0', 'false', 'off', 'no')
        self.batch_max = 1 if os.environ.get(
            'HERMES_PANEL_BOARD_BATCH', '1').lower() in ('0', 'false', 'off', 'no') else BATCH_MAX
        self.read_ends = {}
        self.cursor = self.stamp = None
        self.pending, self.active, self.retries = {}, {}, {}
        self.completed = queue.SimpleQueue()
        self.capture_failures = 0
        self.capture_retry_at = None
        self.attempted_stamp = object()
        self.external_captures = {}
        self.service_wake = threading.Event()
        self.service_stop = threading.Event()
        self.service_worker = None
        # A saved predecessor API can construct this lane without starting the
        # new service. Expose the fully initialized owner for current API repair.
        with feed.lock:
            feed._board_lane = self
            # Natural hot reload can run only the saved predecessor entry: no
            # current API call is guaranteed. The registered capture owner itself
            # therefore establishes service, while manually driven lanes stay inert.
            if (getattr(feed, '_board_service_required', False)
                    or getattr(feed, '_board_thread', None) is threading.current_thread()):
                self.start_service()

    def start_service(self):
        """Refill admitted slots on completions, never behind source recapture.

        The capture thread remains the sole cursor/external-snapshot owner.
        The service thread does no source I/O; scheduler state shares feed.lock.
        """
        with self.feed.lock:
            if self.service_worker is not None or self.service_stop.is_set() or self.feed.stopped.is_set():
                return
            self.service_worker = threading.Thread(target=self._serve,
                name='session-panel-card-service', daemon=True)
            self.service_worker.start()
        self.service_wake.set()

    def _serve(self):
        while not self.service_stop.is_set():
            with self.feed.lock:
                now = time.monotonic()
                due = [end + self.cooldown for session, (token, end) in self.read_ends.items()
                       if session in self.pending and session not in self.active
                       and self.feed._watch_tokens.get(session) is token
                       and len(self.active) < self.capacity]
            self.service_wake.wait(max(0.0, min(due) - now) if due else None)
            self.service_wake.clear()
            if self.service_stop.is_set() or self.feed.stopped.is_set():
                break
            self.service()

    def close(self):
        # Reader ownership stays in each worker, even across unload. Publication
        # is stopped by Feed.stopped; never close another thread's SQLite handles.
        self.service_stop.set()
        self.service_wake.set()
        if self.service_worker is not None:
            self.service_worker.join(2)
        with self.feed.lock:
            self.pending.clear()
            workers = set(self.active.values())  # a batch worker holds one entry per chat: join it once
        for worker in workers:
            worker.join(2)

    def retry_at(self):
        with self.feed.lock:
            ats = [at for _, at in self.retries.values() if at is not None]
            if self.capture_retry_at is not None:
                ats.append(self.capture_retry_at)
            return min(ats) if ats else None

    def service(self):
        """Drain completions and refill FIFO slots without recapturing sources."""
        f = self.feed
        with f.lock:
            if self.service_stop.is_set() or f.stopped.is_set():
                return
            now = f.clock()
            requested_now = set()
            while True:
                try:
                    subscriber, session, expires = f._panel_updates.get_nowait()
                except queue.Empty:
                    break
                if session and expires > now:
                    f._panel_subscribers[subscriber] = (session, expires)
                    requested_now.add(session)
                else:
                    f._panel_subscribers.pop(subscriber, None)
            f._panel_subscribers = {k: v for k, v in f._panel_subscribers.items() if v[1] > now}
            visible = {v[0] for v in f._panel_subscribers.values()}
            while True:
                try:
                    sessions, expires = f._board_demands.get_nowait()
                except queue.Empty:
                    break
                if expires > now:
                    f._board_requested.update({s: expires for s in sessions})
                    requested_now.update(sessions)
            f._board_requested = {s: expires for s, expires in f._board_requested.items() if expires > now}
            demanded = visible | set(f._board_requested)
            # Old/restored bundles need a real board read, even without a new delta.
            f._board_dirty.update(s for s in requested_now if s in f.bundles and
                (s in f._carried or now - f.bundles[s]['bundle']['computed_at'] > f.full_every_s)
                and s not in self.active and s not in self.pending)
            f._board_dirty.intersection_update(f.interest)
            for session in f._board_dirty & demanded:
                if session in f.interest and session in f.bundles:
                    self.pending.setdefault(session, time.time())
                    self.read_ends.pop(session, None)  # attach is immediately actionable
            f._board_dirty.difference_update(self.pending)
            while True:
                try:
                    session, token, error, ended = self.completed.get_nowait()
                except queue.Empty:
                    break
                self.active.pop(session, None)
                if f._watch_tokens.get(session) is not token:
                    self.retries.pop(session, None)
                    continue
                self.read_ends[session] = (token, ended)
                if error:
                    count = self.retries.get(session, (0, None))[0] + 1
                    self.retries[session] = (count, f._backoff(count))
                else:
                    self.retries.pop(session, None)
            for session, (_, at) in list(self.retries.items()):
                if at is not None and now >= at:
                    self.pending.setdefault(session, time.time())
                    self.retries[session] = (self.retries[session][0], None)
            self.read_ends = {s: value for s, value in self.read_ends.items()
                              if f._watch_tokens.get(s) is value[0]}
            batching = self.capacity == 1 and self.batch_max > 1
            batch = []
            for session in sorted(self.pending, key=lambda s: (s not in visible, s not in demanded, self.pending[s])):
                if len(self.active) >= self.capacity or len(batch) >= self.batch_max:
                    break
                if session in self.active:
                    continue
                held = f.bundles.get(session)
                token = f._watch_tokens.get(session)
                if session not in f.interest or not held or token is None:
                    self.pending.pop(session, None)
                    continue
                if self.visible_only and session not in demanded:
                    self.pending.pop(session, None)
                    f._board_dirty.add(session)
                    self.retries.pop(session, None)
                    continue
                last = self.read_ends.get(session)
                if last is not None and time.monotonic() < last[1] + self.cooldown:
                    continue  # Retain oldest pending demand for one trailing read.
                admitted_at = self.pending.pop(session)
                if batching:
                    batch.append((session, token, held['bundle'], admitted_at))
                    continue
                worker = threading.Thread(target=self._read,
                    args=(session, token, held['bundle'], admitted_at),
                    name='session-panel-card-' + session[:12], daemon=True)
                self.active[session] = worker
                worker.start()
            if batch:
                # Visible and demanded chats lead the batch (the sort above), so a watched panel is still served
                # first. Every chat here was taken from `pending` before the worker's first read, so no read can
                # predate the event that admitted it. An event during the batch re-admits its chat: before its
                # turn it is read fresh after the worker ends, during or after its read it gets a trailing read.
                worker = threading.Thread(target=self._read_batch, args=(batch,),
                    name='session-panel-card-' + batch[0][0][:12], daemon=True)
                for item in batch:
                    self.active[item[0]] = worker
                worker.start()

    def step(self):
        f = self.feed
        f._board.clear()  # writes/completions during capture wake another pass
        now = f.clock()
        self.service()
        capture_started = time.time()
        capture_timing: dict = {'started_at': capture_started}
        rd = self.reader_factory()
        stamp = None
        try:
            board_stamp = rd.board_stamps() if callable(getattr(rd, 'board_stamps', None)) else rd.stamps()
            external = getattr(rd, 'external_stamps', None)
            stamp = (board_stamp, external() if callable(external) else None)
            due = self.capture_retry_at is not None and now >= self.capture_retry_at
            moved = stamp != self.attempted_stamp
            if moved or (self.cursor is None and not self.capture_failures) or due:
                if moved:
                    self.capture_failures = 0
                self.attempted_stamp = stamp
                # Commit cursor and stamp only after every affected owner is admitted.
                semantic = getattr(rd, 'board_changes', None)
                if callable(semantic):
                    changed, cursor = semantic(self.cursor)
                else:
                    changed, cursor = rd.changed_cards(self.cursor)
                at = capture_started
                with f.lock:
                    held = {s: b for s, b in f.bundles.items() if s in f.interest}
                    capture_seq = next(f._seq)
                    if callable(semantic):
                        dirty = {s for s, b in held.items() if changed.affects(b)}
                    else:
                        dirty = {s for s, b in held.items()
                                 if b['refs'].intersection(changed) or b['lineage'].intersection(v for v in changed.values() if v)}
                # A known local delta is independently actionable. Strict external
                # inventory can take longer than the freshness budget; it must not
                # hold this admission hostage. Keep the capture cursor uncommitted
                # until external capture succeeds, so failures remain retryable.
                self._admit(dirty, at)
                capture_timing.update(local_admitted_at=time.time(), local_sessions=sorted(dirty),
                                      capture_seq=capture_seq)
                self.service_wake.set()
                self.service()
                capture = getattr(rd, 'external_capture', None)
                if os.environ.get('HERMES_PANEL_EXTERNAL_VISIBLE_ONLY', '1').lower() not in ('0', 'false', 'off', 'no'):
                    with f.lock:
                        visible = {s for s, expires in f._panel_subscribers.values() if expires > f.clock()}
                        # External inputs were not captured for hidden panels. Retain
                        # dirty demand so attachment hydrates a current snapshot.
                        hidden = set(held) - visible
                        f._board_dirty.update(hidden)
                        f._board_failures.update(hidden)  # stale until attach hydration succeeds
                        held = {s: b for s, b in held.items() if s in visible}
                captured = self.external_captures
                external_dirty = set()
                if callable(capture):
                    external_started = time.monotonic()
                    try:
                        captured = capture(held)
                    finally:
                        capture_timing['external_s'] = round(time.monotonic() - external_started, 4)
                    compare = getattr(rd, 'external_semantic', lambda value: value)
                    external_dirty = {s for s in held if compare(captured[s]) != compare(self.external_captures.get(s))}
                    diagnostic_stale = getattr(rd, 'diagnostic_stale', None)
                    if callable(diagnostic_stale):
                        external_dirty.update(diagnostic_stale(held))
                    dirty.update(external_dirty)
                    if callable(getattr(rd, 'advance_external', None)):
                        observations = {s: (capture_seq, captured[s]) for s in held
                                        if s not in dirty and captured[s] != self.external_captures.get(s)}
                        with f.lock:
                            old = getattr(f, '_external_observations', None) or {}
                            f._external_observations = {**old, **{s: value for s, value in observations.items()
                                                               if s not in old or value[0] > old[s][0]}}
                        f._publish({}, age_only=True, observations=observations)
                elif callable(external) and (self.stamp is None or stamp[1] != self.stamp[1]):
                    external_dirty.update(held)  # old adapters cannot attest unchanged external inputs
                # Even a locally admitted chat needs a successor if this later
                # external snapshot changed: its first read may predate that input.
                self._admit(external_dirty, at)
                capture_timing.update(external_admitted_at=time.time(), external_sessions=sorted(external_dirty))
                self.external_captures = captured
                f._board_capture_failures.difference_update(held)
                self.cursor, self.stamp = cursor, stamp
                self.capture_failures, self.capture_retry_at = 0, None
        except Exception:
            capture_timing['failed'] = True
            self.attempted_stamp = stamp
            self.capture_failures += 1
            self.capture_retry_at = f._backoff(self.capture_failures)
            with f.lock:
                # Hydration can already be running from the local delta. Its
                # success cannot vouch for a strict capture that failed later.
                f._board_capture_failures.update(f.interest)
            log.warning('session-panel board capture failed', exc_info=True)
        finally:
            rd.release()
            if 'capture_seq' in capture_timing or capture_timing.get('failed'):
                capture_timing['finished_at'] = time.time()
                with f.lock:
                    f.timing['board_captures'] = (f.timing.get('board_captures', []) + [capture_timing])[-128:]
        self.service_wake.set()
        self.service()

    def _admit(self, sessions, at):
        with self.feed.lock:
            for session in sessions:
                if self.visible_only and self.feed._board_requested.get(session, 0) <= self.feed.clock() and not any(s == session and expires > self.feed.clock()
                                                 for s, expires in self.feed._panel_subscribers.values()):
                    self.feed._board_dirty.add(session)
                else:
                    self.pending.setdefault(session, at)  # oldest outstanding generation wins service
                self.feed._board_failures.add(session)
                self.retries.pop(session, None)

    def _read_batch(self, items):
        """Hydrate `items` in order with one reader and one hydration scope; each chat publishes on its own.

        Every item leaves `active` on every path: read (its completion is queued by `_read`), dropped (its watch
        ended), deferred (re-admitted before its turn: left pending for a fresh read) or handed back to `pending`
        unread (a visible chat became due and must not wait out the batch; on a stop it is simply released). A
        deferred or handed-back chat keeps its oldest admission time and takes no cooldown. The worker's first
        chat is always read, never deferred or cut, so every worker makes progress."""
        f = self.feed
        todo = list(items)
        rd = None
        reads = 0
        try:
            try:
                rd = self.reader_factory()
            except Exception:
                log.warning('session-panel board batch reader unavailable; reading one chat per reader', exc_info=True)
            # Trust ages from before the first shared read, never from a later chat's turn in the batch.
            read_at = f.clock() if rd is not None else None
            with contextlib.ExitStack() as stack:
                scope = getattr(rd, 'batch', None)
                if len(todo) > 1 and callable(scope):
                    try:
                        stack.enter_context(scope([item[0] for item in todo]))
                    except Exception:
                        log.warning('session-panel board batch scope unavailable; reading unshared', exc_info=True)
                while todo:
                    session, _token, _base, admitted_at = todo[0]
                    with f.lock:
                        if self.service_stop.is_set() or f.stopped.is_set() or (reads and self._visible_waits(todo)):
                            break
                        # The bundle and watch token as they are at this chat's turn, not at batch start.
                        held, token = f.bundles.get(session), f._watch_tokens.get(session)
                        if session not in f.interest or not held or token is None:
                            self.active.pop(session, None)  # unwatched: dropped
                            todo.pop(0)
                            self.service_wake.set()
                            continue
                        if session in self.pending:
                            if reads:
                                # Re-admitted by an event since the batch began: its turn here could reuse rows
                                # the scope cached before that event, so it is left pending (no read, so no
                                # cooldown), keeps its place, and is read fresh as soon as this worker ends.
                                self.pending[session] = min(self.pending[session], admitted_at)
                                self.active.pop(session, None)
                                todo.pop(0)
                                self.service_wake.set()
                                continue
                            # The worker's first read: nothing is cached yet beyond lineages, so this read
                            # serves the newer event too.
                            admitted_at = min(self.pending.pop(session), admitted_at)
                        base = held['bundle']
                    todo.pop(0)
                    reads += 1
                    self._read(session, token, base, admitted_at, reader=rd, read_at=read_at, batch=len(items))
        finally:
            with f.lock:
                stopping = self.service_stop.is_set() or f.stopped.is_set()
                for session, _token, _base, admitted_at in todo:
                    self.active.pop(session, None)
                    if not stopping:
                        self.pending[session] = min(self.pending.get(session, admitted_at), admitted_at)
            if todo:
                self.service_wake.set()  # before the release: a failing close must not strand the hand-back
            if rd is not None:
                rd.release()

    def _visible_waits(self, todo):
        """(lock held) The next turn is a hidden chat while a visible one is due: cut, so `service()` leads the
        next worker with it. The visible chat may be waiting in `pending` or sit later in this batch.

        Never when the next turn is itself visible: with more visible chats due than one batch holds, the 17th
        would otherwise cut every batch. Never for a chat whose read this worker has not yet handed back
        (`active`): its cooldown is not recorded until `service()` drains the completion."""
        f = self.feed
        now, clock = time.monotonic(), f.clock()
        visible = {session for session, expires in f._panel_subscribers.values() if expires > clock}
        if todo[0][0] in visible:
            return False
        if any(item[0] in visible for item in todo[1:]):
            return True
        for session in visible:
            if session not in self.pending or session in self.active:
                continue
            last = self.read_ends.get(session)
            if last is None or now >= last[1] + self.cooldown:
                return True
        return False

    def _read(self, session, token, base, admitted_at, reader=None, read_at=None, batch=1):
        f, rd = self.feed, None
        error = None
        started = time.time()
        stages = {}
        try:
            rd = self.reader_factory() if reader is None else reader
            read_at = f.clock() if read_at is None else read_at
            bundle = f.compute(session, readers=rd, base=base, stages=stages)
            bundle['computed_at'] = read_at
            # No shared flights: joining a sweep could adopt a pre-event snapshot.
            # Publication and interest-token validation share the writer fence.
            changed = f._publish({session: bundle}, watch_tokens={session: token})
            with f.lock:
                error = bool(f.bundles.get(session, {}).get('source_stale'))
                if not error and session not in self.pending and session not in f._board_dirty:
                    f._board_failures.discard(session)
            finished = time.time()
            entry = {'session': session, 'admitted_at': admitted_at, 'pass_started_at': started,
                     'at': finished, 'latency_s': round(finished - admitted_at, 4),
                     'reader_s': round(finished - started, 4), 'stages': stages, 'published': bool(changed),
                     'batch': batch}
            with f.lock:
                f.timing['board_recent'] = (f.timing.get('board_recent', []) + [entry])[-256:]
        except Exception as exc:
            error = str(exc)
            with f.lock:
                f._board_failures.add(session)
            log.warning('session-panel board hydration failed: %s', session, exc_info=True)
        finally:
            if rd is not None and reader is None:
                rd.release()  # a batch releases its shared reader once, after its last chat
            self.completed.put((session, token, error, time.monotonic()))
            self.service_wake.set()
            f._board.set()
