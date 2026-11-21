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


class Published:
    def __init__(self, store, contexts, values, cursor):
        self.store, self.contexts, self.values, self.cursor = store, contexts, values, cursor

    def prefetch(self, urls):
        self.store.request(self.contexts, urls)

    def get(self, url):
        return copy.deepcopy(self.values.get(url, {}))


class Observations:
    def __init__(self, factory, ttl=60., clock=time.monotonic):
        self.factory, self.ttl, self.clock = factory, ttl, clock
        self.lock = threading.Lock()
        self.contexts = {}
        self.values = {}  # url -> (context, read-start, observation)
        self.pending = {}
        self.active = {}
        self.thread = None
        self.revision = 0

    def _fresh_values(self):
        now = self.clock()
        return {u: data for u, (ctx, start, data) in self.values.items()
                if self.contexts.get(u) == ctx and now - start < self.ttl}

    def _cursor(self):
        fresh = self._fresh_values()
        due = tuple(sorted(u for u in fresh if self.clock() - self.values[u][1] >= self.ttl / 2))
        return self.revision, tuple(sorted(fresh)), due

    def reader(self, contexts):
        with self.lock:
            if contexts != self.contexts:
                self.contexts = dict(contexts)
                self.values = {u: v for u, v in self.values.items() if contexts.get(u) == v[0]}
                self.pending = {u: c for u, c in self.pending.items() if contexts.get(u) == c}
                self.revision += 1
            return Published(self, dict(contexts), copy.deepcopy(self._fresh_values()), self._cursor())

    def cursor(self):
        with self.lock:
            return self._cursor()

    def request(self, contexts, urls):
        with self.lock:
            now = self.clock()
            # Replace/coalesce by URL; space is bounded by the current board, not requests.
            for url in urls:
                ctx = contexts.get(url)
                if ctx is None or self.contexts.get(url) != ctx or self.active.get(url) == ctx:
                    continue
                old = self.values.get(url)
                if old is not None and old[0] == ctx and now - old[1] < self.ttl / 2:
                    continue
                self.pending[url] = ctx
            if self.pending and self.thread is None:
                self.thread = threading.Thread(target=self._run, name='lanes-pr-observer', daemon=True)
                self.thread.start()

    def _run(self):
        while True:
            with self.lock:
                if not self.pending:
                    self.thread = None
                    return
                job, self.pending = self.pending, {}
                self.active = job
            started = self.clock()
            values = {}
            try:
                reader = self.factory()  # core's whole-batch 30s network budget
                reader.prefetch(job)
                values = {url: copy.deepcopy(reader.get(url)) for url in job}
            except Exception:
                logging.getLogger(__name__).exception('Lanes PR observation failed')
            with self.lock:
                self.active = {}
                before = self._fresh_values()
                for url, ctx in job.items():
                    if self.contexts.get(url) == ctx:
                        self.values[url] = (ctx, started, values.get(url, {}))
                if before != self._fresh_values():
                    self.revision += 1
                # No periodic network work without another projection request.

    def join(self, timeout):
        with self.lock:
            thread = self.thread
        if thread is not None:
            thread.join(timeout)
