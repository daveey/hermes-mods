"""Bounded, payload-free in-process reconciliation attribution.

Wall minus thread CPU is off-CPU (scheduling/GIL/I/O), NOT a DB-wait claim.
Only explicit lock/SQLite boundaries localize it further. No sinks or locks here.
"""
from contextlib import contextmanager, nullcontext
import itertools
import os
import time

_IDS = itertools.count(1)


class Recorder:
    def __init__(self):
        self.call_id = f'{os.getpid()}:{next(_IDS)}'
        self.wall = time.monotonic_ns()
        self.cpu = time.thread_time_ns()
        self.rows = {}
        self.stack = []

    @contextmanager
    def stage(self, name):
        # Names are static code labels, never source values. Aggregation bounds
        # storage independently of rows, owners, queries or contention duration.
        if name not in self.rows and len(self.rows) >= 64:
            name = 'overflow'
        row = self.rows.setdefault(name, [0, 0, 0])
        frame = [time.monotonic_ns(), time.thread_time_ns(), 0, 0]
        self.stack.append(frame)
        try:
            yield
        finally:
            wall = time.monotonic_ns() - frame[0]
            cpu = time.thread_time_ns() - frame[1]
            self.stack.pop()
            row[0] += wall - frame[2]
            row[1] += cpu - frame[3]
            row[2] += 1
            if self.stack:
                self.stack[-1][2] += wall
                self.stack[-1][3] += cpu

    def connection(self, conn, label):
        return Connection(conn, self, label)

    def connect(self, opener, path, label, **kwargs):
        return connect(opener, path, self, label, **kwargs)

    def finish(self):
        wall = time.monotonic_ns() - self.wall
        cpu = time.thread_time_ns() - self.cpu
        rows = {name: {'wall_ns': r[0], 'cpu_ns': r[1], 'calls': r[2]}
                for name, r in self.rows.items()}
        rows['residual'] = {'wall_ns': wall - sum(r[0] for r in self.rows.values()),
                            'cpu_ns': cpu - sum(r[1] for r in self.rows.values()), 'calls': 1}
        return {'version': 1, 'call_id': self.call_id, 'wall_ns': wall,
                'cpu_ns': cpu, 'row_sample_stride': 64, 'stages': rows}


def stage(recorder, name):
    return recorder.stage(name) if recorder is not None else nullcontext()


class Connection:
    """Read adapter: preserve SQL, arguments, transactions and cursor streaming."""
    def __init__(self, conn, recorder, label):
        self.conn, self.recorder, self.label = conn, recorder, label

    def execute(self, *args, **kwargs):
        with stage(self.recorder, self.label):
            cursor = self.conn.execute(*args, **kwargs)
        return Cursor(cursor, self.recorder, self.label)

    def close(self):
        with stage(self.recorder, self.label):
            return self.conn.close()

    def __getattr__(self, name):
        return getattr(self.conn, name)


class Cursor:
    def __init__(self, cursor, recorder, label):
        self.cursor, self.recorder, self.label = cursor, recorder, label
        self.rows = 0

    def __iter__(self):
        return self

    def __next__(self):
        self.rows += 1
        # Row-level probes are systematic 1/64 samples, not estimates of total
        # SQLite work. The enclosing inventory_scan measures every row.
        if self.rows % 64 != 1:
            return next(self.cursor)
        with stage(self.recorder, self.label + '_row_sample'):
            return next(self.cursor)

    def fetchone(self):
        with stage(self.recorder, self.label):
            return self.cursor.fetchone()

    def fetchall(self):
        with stage(self.recorder, self.label):
            return self.cursor.fetchall()

    def __getattr__(self, name):
        return getattr(self.cursor, name)


def connect(opener, path, recorder, label, **kwargs):
    if recorder is None:
        return opener(path, **kwargs)
    with stage(recorder, label):
        conn = opener(path, **kwargs)
    return Connection(conn, recorder, label)
