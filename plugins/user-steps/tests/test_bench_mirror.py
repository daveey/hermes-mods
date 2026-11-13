"""Card L budget for the user-steps card mirror (design t_01a1d563324d7e8e: P1 1.6% of a core -> 0.3%).

Replays a polling Desktop against a busy board: the queue is polled every POLL_EVERY s, an unrelated card moves every
second, a new session lands every SESSION_EVERY s and a bound card changes every BOUND_EVERY s. main runs the full
pass on every poll; card L runs it only when the input key moves (or at the FULL_EVERY_S ceiling). The baseline here
is card L's own full pass, which already reuses kept connections, so it is cheaper than main's and the ratio is
conservative. The ratio of the two, scaled to the P1 share, must stay inside the budget.
"""

import json
import os
import sqlite3
import time

import pytest

from test_kanban_mirror import OWNER
from test_kanban_mirror_keyed import keyed  # noqa: F401  (fixture)

P1_NOW = 1.6
BUDGET = 0.3
SPAN = int(os.environ.get('MIRROR_BENCH_SPAN', 600))
POLL_EVERY = 2
SESSION_EVERY = 20
BOUND_EVERY = 60
TASKS = int(os.environ.get('MIRROR_BENCH_TASKS', 400))
ASKS = 12


class Clock:
    """The module's ``time`` with a simulated monotonic clock (the memo's ceiling runs on it)."""

    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def __getattr__(self, name):
        return getattr(time, name)


def populate(k):
    with sqlite3.connect(k.path) as c:
        # Core's indexes on these tables (hermes_cli/kanban_db.py), so the replay reads the board as production does.
        c.executescript("""
            CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
            CREATE INDEX IF NOT EXISTS idx_events_task ON task_events(task_id);
            CREATE INDEX IF NOT EXISTS idx_events_kind_task_id ON task_events(kind, task_id, id);
            CREATE INDEX IF NOT EXISTS idx_notify_task ON kanban_notify_subs(task_id);
        """)
        hr = json.loads(c.execute("SELECT human_request FROM tasks WHERE id='t_test'").fetchone()[0])
        c.executemany("INSERT INTO tasks VALUES (?,?,?,?,?,NULL)",
                      [(f't_ask{i}', f'ask {i}', 'blocked', 'session-a', json.dumps(dict(hr, scope=f'ask {i}')))
                       for i in range(ASKS)])
        c.executemany("INSERT INTO task_events(task_id,kind) VALUES (?, 'blocked')", [(f't_ask{i}',) for i in range(ASKS)])
        c.executemany("INSERT INTO tasks VALUES (?,?,?,?,NULL,NULL)",
                      [(f't_run{i}', f'work {i}', 'running', f'session-w{i}') for i in range(TASKS)])
        c.executemany("INSERT INTO task_events(task_id,kind) VALUES (?, 'heartbeat')",
                      [(f't_run{i % TASKS}',) for i in range(TASKS * 5)])
    with sqlite3.connect(k.state) as c:
        c.executemany("INSERT INTO sessions VALUES (?, NULL, NULL, 'kanban', 0)", [(f'session-w{i}',) for i in range(TASKS)])


def replay(k, clock, card_l, run):
    cpu, passes, n = 0.0, len(k.passes), 0
    for t in range(SPAN):
        clock.now = 1000.0 + run * SPAN + t  # monotonic across replays
        with sqlite3.connect(k.path) as c:  # unrelated board churn
            c.execute("INSERT INTO task_events(task_id,kind) VALUES (?, 'heartbeat')", (f't_run{t % TASKS}',))
            c.execute("UPDATE tasks SET title=? WHERE id=?", (f'work {t}', f't_run{(t * 7) % TASKS}'))
        if t % SESSION_EVERY == SESSION_EVERY - 1:
            with sqlite3.connect(k.state) as c:
                c.execute("INSERT INTO sessions VALUES (?, NULL, NULL, 'kanban', 0)", (f's-{run}-{t}',))
        if t % BOUND_EVERY == BOUND_EVERY - 1:
            with sqlite3.connect(k.path) as c:
                c.execute("INSERT INTO task_comments(task_id,author,body) VALUES (?, 'worker', 'progress')",
                          (f't_ask{(t // BOUND_EVERY) % ASKS}',))
                c.execute("INSERT INTO task_events(task_id,kind) VALUES (?, 'commented')", (f't_ask{(t // BOUND_EVERY) % ASKS}',))
        if t % POLL_EVERY:
            continue
        start = time.thread_time()
        if card_l:
            k.mirror.reconcile(OWNER)
        else:
            k.mirror._reconcile(OWNER, k.mirror._fenced_core())
        cpu += time.thread_time() - start
        n += 1
    return cpu, len(k.passes) - passes, n


def test_mirror_replay_budget(keyed, monkeypatch):
    k = keyed
    clock = Clock()
    monkeypatch.setitem(k.mod, 'time', clock)
    populate(k)
    k.mirror.reconcile(OWNER)
    assert len(k.store.requests(OWNER)) == ASKS + 1
    old = min(replay(k, clock, card_l=False, run=n)[0] for n in range(2))
    new, full, polls = min(replay(k, clock, card_l=True, run=n) for n in range(2, 4))
    share = P1_NOW * new / old
    print(f'\nuser-steps mirror over {SPAN}s ({polls} polls): main {old * 1000:.0f} ms, card L {new * 1000:.0f} ms of CPU '
          f'({full} full passes); P1 {P1_NOW}% -> {share:.3f}% (budget {BUDGET}%)')
    assert full >= SPAN // 60  # the FULL_EVERY_S ceiling holds: never fewer full passes than one a minute
    assert share <= BUDGET
