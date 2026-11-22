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

    def close(self):
        # Reader ownership stays in each worker, even across unload. Publication
        # is stopped by Feed.stopped; never close another thread's SQLite handles.
        self.pending.clear()
        for worker in list(self.active.values()):
            worker.join(2)

    def retry_at(self):
        ats = [at for _, at in self.retries.values() if at is not None]
        if self.capture_retry_at is not None:
            ats.append(self.capture_retry_at)
        return min(ats) if ats else None

    def step(self):
        f = self.feed
        f._board.clear()  # writes/completions during capture wake another pass
        now = f.clock()
        while True:
            try:
                session, token, error = self.completed.get_nowait()
            except queue.Empty:
                break
            self.active.pop(session, None)
            with f.lock:
                current = f._watch_tokens.get(session) is token
            if not current:
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
        capture_started = time.time()
        rd = self.reader_factory()
        stamp = None
        try:
            stamp = rd.board_stamps() if callable(getattr(rd, 'board_stamps', None)) else rd.stamps()
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
                    if callable(semantic):
                        dirty = [s for s, b in held.items() if changed.affects(b)]
                    else:
                        dirty = [s for s, b in held.items()
                                 if b['refs'].intersection(changed) or b['lineage'].intersection(v for v in changed.values() if v)]
                for session in dirty:
                    with f.lock:
                        self.pending[session] = at
                        f._board_failures.add(session)
                    self.retries.pop(session, None)  # a real source event renews finite recovery
                self.cursor, self.stamp = cursor, stamp
                self.capture_failures, self.capture_retry_at = 0, None
        except Exception:
            self.attempted_stamp = stamp
            self.capture_failures += 1
            self.capture_retry_at = f._backoff(self.capture_failures)
            with f.lock:
                f._board_failures.update(f.interest)
            log.warning('session-panel board capture failed', exc_info=True)
        finally:
            rd.release()
        for session in sorted(self.pending, key=self.pending.get, reverse=True):
            if len(self.active) >= self.capacity:
                break
            if session in self.active:
                continue
            with f.lock:
                held = f.bundles.get(session)
                token = f._watch_tokens.get(session)
                if session not in f.interest or not held or token is None:
                    self.pending.pop(session, None)
                    continue
                base = held['bundle']
            admitted_at = self.pending.pop(session)
            worker = threading.Thread(target=self._read, args=(session, token, base, admitted_at),
                                      name='session-panel-card-' + session[:12], daemon=True)
            self.active[session] = worker
            worker.start()

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
            f._board.set()
