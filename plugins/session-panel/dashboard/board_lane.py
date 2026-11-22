"""Bounded, change-driven board hydration, independent of canonical reconciliation.

The coordinator never joins a slow reader. At most four independently owned reader
scopes run concurrently; one pending generation per watched chat coalesces writes.
No connections, transactions or source snapshots survive a worker invocation.
"""
from __future__ import annotations

import logging
import queue
import threading
import time

log = logging.getLogger('session-panel')


class BoardLane:
    def __init__(self, feed, reader_factory, *, capacity=4):
        self.feed, self.reader_factory = feed, reader_factory
        self.capacity = capacity
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
            self.service_wake.wait()
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
            workers = list(self.active.values())
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
            while True:
                try:
                    session, token, error = self.completed.get_nowait()
                except queue.Empty:
                    break
                self.active.pop(session, None)
                if f._watch_tokens.get(session) is not token:
                    self.retries.pop(session, None)
                    continue
                if error:
                    count = self.retries.get(session, (0, None))[0] + 1
                    self.retries[session] = (count, f._backoff(count))
                else:
                    self.retries.pop(session, None)
            for session, (_, at) in list(self.retries.items()):
                if at is not None and now >= at:
                    self.pending.setdefault(session, time.time())
                    self.retries[session] = (self.retries[session][0], None)
            for session in sorted(self.pending, key=self.pending.get):
                if len(self.active) >= self.capacity:
                    break
                if session in self.active:
                    continue
                held = f.bundles.get(session)
                token = f._watch_tokens.get(session)
                if session not in f.interest or not held or token is None:
                    self.pending.pop(session, None)
                    continue
                admitted_at = self.pending.pop(session)
                worker = threading.Thread(target=self._read,
                    args=(session, token, held['bundle'], admitted_at),
                    name='session-panel-card-' + session[:12], daemon=True)
                self.active[session] = worker
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
                self.cursor, self.stamp = cursor, stamp
                self.capture_failures, self.capture_retry_at = 0, None
        except Exception:
            capture_timing['failed'] = True
            self.attempted_stamp = stamp
            self.capture_failures += 1
            self.capture_retry_at = f._backoff(self.capture_failures)
            with f.lock:
                f._board_failures.update(f.interest)
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
                self.pending.setdefault(session, at)  # oldest outstanding generation wins service
                self.feed._board_failures.add(session)
                self.retries.pop(session, None)

    def _read(self, session, token, base, admitted_at):
        f, rd = self.feed, None
        error = None
        started = time.time()
        stages = {}
        try:
            rd = self.reader_factory()
            read_at = f.clock()
            bundle = f.compute(session, readers=rd, base=base, stages=stages)
            bundle['computed_at'] = read_at
            # No shared flights: joining a sweep could adopt a pre-event snapshot.
            # Publication and interest-token validation share the writer fence.
            changed = f._publish({session: bundle}, watch_tokens={session: token})
            with f.lock:
                error = bool(f.bundles.get(session, {}).get('source_stale'))
                if not error and session not in self.pending:
                    f._board_failures.discard(session)
            finished = time.time()
            entry = {'session': session, 'admitted_at': admitted_at, 'pass_started_at': started,
                     'at': finished, 'latency_s': round(finished - admitted_at, 4),
                     'reader_s': round(finished - started, 4), 'stages': stages, 'published': bool(changed)}
            with f.lock:
                f.timing['board_recent'] = (f.timing.get('board_recent', []) + [entry])[-256:]
        except Exception as exc:
            error = str(exc)
            with f.lock:
                f._board_failures.add(session)
            log.warning('session-panel board hydration failed: %s', session, exc_info=True)
        finally:
            if rd is not None:
                rd.release()
            self.completed.put((session, token, error))
            self.service_wake.set()
            f._board.set()
