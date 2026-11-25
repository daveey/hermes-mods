"""Bounded, display-only PR observations off the projection request path.

Entries bind to each PR's own card evidence, never the busy board's global
cursor. New card evidence invalidates immediately; TTL starts before network
reads. The CLI Reader still confirms heads/checks; this is not ship authority.
"""
from __future__ import annotations

import copy
import logging
import threading
import time

# Display-only data: each PR costs ~4 GitHub GraphQL requests per read, and the
# 5000/h bucket is shared per GitHub user. An entry is re-read only after it
# is older than TTL_S; a failed/rate-limited batch backs off BACKOFF_MIN_S
# doubling to BACKOFF_MAX_S.
TTL_S = 600
TERMINAL_TTL_S = 3600
BACKOFF_MIN_S = 60
BACKOFF_MAX_S = 900


class Published:
    def __init__(self, store, contexts, values, cursor):
        self.store, self.contexts, self.values, self.cursor = store, contexts, values, cursor

    def prefetch(self, urls):
        self.store.request(self.contexts, urls)

    def get(self, url):
        return copy.deepcopy(self.values.get(url, {}))


class Observations:
    def __init__(self, factory, ttl=TTL_S, clock=time.monotonic):
        self.factory, self.ttl, self.clock = factory, ttl, clock
        self.lock = threading.Lock()
        self.contexts = {}
        self.values = {}  # url -> (context, read-start, observation)
        self.terminal = set()
        self.pending = {}
        self.active = {}
        self.thread = None
        self.revision = 0
        self.backoff_until = 0.
        self.backoff_delay = 0.

    def _fresh_values(self):
        now = self.clock()
        return {u: data for u, (ctx, start, data) in self.values.items()
                if self.contexts.get(u) == ctx and now - start < (TERMINAL_TTL_S if u in self.terminal else self.ttl)}

    def _cursor(self):
        fresh = self._fresh_values()
        # Refresh is due only at expiry (fresh set shrinks), so nothing is ever
        # fresh-and-due; the stream wakes when an entry expires.
        # One transition at cooldown expiry wakes a quiet stream even when all
        # cached observations expired earlier. No periodic timer/network loop.
        retry_ready = bool(self.backoff_until and self.clock() >= self.backoff_until)
        return self.revision, tuple(sorted(fresh)), retry_ready

    def reader(self, contexts):
        with self.lock:
            if contexts != self.contexts:
                self.contexts = dict(contexts)
                self.values = {u: v for u, v in self.values.items() if contexts.get(u) == v[0]}
                self.terminal.intersection_update(self.values)
                self.pending = {u: c for u, c in self.pending.items() if contexts.get(u) == c}
                self.revision += 1
            return Published(self, dict(contexts), copy.deepcopy(self._fresh_values()), self._cursor())

    def cursor(self):
        with self.lock:
            return self._cursor()

    def request(self, contexts, urls):
        with self.lock:
            now = self.clock()
            if now < self.backoff_until:
                return  # rate-limited/failing: serve existing values until ttl
            # Replace/coalesce by URL; space is bounded by the current board, not requests.
            fresh = self._fresh_values()
            for url in urls:
                ctx = contexts.get(url)
                if ctx is None or self.contexts.get(url) != ctx or self.active.get(url) == ctx:
                    continue
                if url in fresh:
                    continue
                self.pending[url] = ctx
            if self.pending and self.thread is None:
                self.thread = threading.Thread(target=self._run, name='lanes-pr-observer', daemon=True)
                self.thread.start()

    def _run(self):
        while True:
            with self.lock:
                if not self.pending or self.clock() < self.backoff_until:
                    self.thread = None
                    return
                job, self.pending = self.pending, {}
                self.active = job
            started = self.clock()
            values = {}
            terminal = set()
            failed = False
            try:
                reader = self.factory()  # core's whole-batch 30s network budget
                reader.prefetch(job)
                values = {url: copy.deepcopy(reader.get(url)) for url in job}
                states = getattr(reader, "states", {})
                terminal = {url for url in job if states.get(url) in ("MERGED", "CLOSED")
                            or values[url].get("merged") is True}
            except Exception:
                failed = True
                logging.getLogger(__name__).exception('Lanes PR observation failed')
            with self.lock:
                self.active = {}
                if failed or not (terminal or any(values.get(u) for u in job)):
                    self.backoff_delay = min(self.backoff_delay * 2 or BACKOFF_MIN_S, BACKOFF_MAX_S)
                    self.backoff_until = self.clock() + self.backoff_delay
                else:
                    self.backoff_delay = self.backoff_until = 0.
                before = self._fresh_values()
                for url, ctx in job.items():
                    if self.contexts.get(url) == ctx and (values.get(url) or url in terminal):
                        self.values[url] = (ctx, started, values[url])
                        self.terminal.discard(url)
                        if url in terminal:
                            self.terminal.add(url)
                if before != self._fresh_values():
                    self.revision += 1
                # No periodic network work without another projection request.

    def join(self, timeout):
        with self.lock:
            thread = self.thread
        if thread is not None:
            thread.join(timeout)
