"""Combine independent board accounting before projecting any presentation."""
from copy import deepcopy
from contextlib import contextmanager
from pathlib import Path
import logging
import sqlite3
import threading
import time
from time import thread_time


# A board's budget is the CPU its own reader thread spent. A wall-clock deadline
# also charged the board for every other thread's turn at the interpreter lock:
# in a busy backend a healthy 0.3 s read was interrupted after 2 s of waiting.
BUDGET_CPU_SECONDS = 2
# Starvation backstop only: the read snapshot pins the board's WAL, so even a
# reader that is not at fault must not hold it open indefinitely.
SNAPSHOT_WALL_SECONDS = 30
# SQLite calls back into Python (reacquiring the interpreter lock) once per this
# many VM steps. At 1000 a contended reader queued for the lock every few
# microseconds of work, so the check itself consumed the budget it enforced.
CHECK_EVERY_VM_STEPS = 100_000


def arm_deadline(db, *, phase='open'):
    """Each board gets its own query budget, not time spent on sibling boards or other threads."""
    started = time.monotonic()
    owner = threading.get_ident()
    cpu_started = thread_time()
    reported = False

    def progress():
        nonlocal reported, owner, cpu_started
        if threading.get_ident() != owner:
            # Thread CPU clocks are per thread: restart the budget for a new reader.
            owner, cpu_started = threading.get_ident(), thread_time()
        spent = thread_time() - cpu_started
        waited = time.monotonic() - started
        expired = spent > BUDGET_CPU_SECONDS or waited > SNAPSHOT_WALL_SECONDS
        if expired and not reported:
            reported = True
            # SQLite reports only "interrupted" at the next statement. Identify
            # this handler rather than attributing the failure to that statement
            # or to another connection's cancellation/budget.
            logging.getLogger(__name__).warning(
                'lanes board_merge deadline expired: phase=%s wall=%.3fs thread_cpu=%.3fs',
                phase, waited, spent)
        return int(expired)

    db.set_progress_handler(progress, CHECK_EVERY_VM_STEPS)


@contextmanager
def read_board(path, *, snapshot=True):
    """Borrow an independent board without init/migrations or retained evidence."""
    from hermes_cli import kanban_db as kb, kanban_db_sync as sync, kanban_lockguard as guard
    db = guard.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True, timeout=.25)
    try:
        db.row_factory = sqlite3.Row
        db.text_factory = kb._lossy_text
        db.execute('PRAGMA query_only=ON')
        arm_deadline(db)
        sync.register_without_install(db, str(Path(path).resolve()))
        if snapshot:
            db.execute('BEGIN')
        yield db
    finally:
        db.rollback()
        db.close()


def merge(pairs):
    snap, raw = deepcopy(pairs[0])
    raw = raw or {}
    raw.setdefault('cards', [])
    failures = [(other, flow or {}) for other, flow in pairs
                if not flow or flow.get('configured') is False]
    raw['flow_complete'] = not failures
    raw['incomplete_status_lanes'] = sorted({name for other, _ in failures
                                            for name, row in other.get('lanes', {}).items() if row.get('open')})
    raw['partial_reason'] = '; '.join(flow.get('reason') or 'board flow unavailable' for _, flow in failures)
    # First-board failure must not suppress healthy siblings (or certify a later
    # failure as complete merely because the first source succeeded).
    raw['configured'] = any(flow and flow.get('configured') is not False for _, flow in pairs)
    for other, flow in pairs[1:]:
        for name, row in other.get('lanes', {}).items():
            if name not in snap.get('lanes', {}):
                snap.setdefault('lanes', {})[name] = deepcopy(row)
                continue
            dest = snap['lanes'][name]
            for key in ('open', 'running', 'ready', 'held', 'blocked_on_you', 'done', 'run_seconds', 'runs', 'admitted_today', 'cost_usd', 'tokens', 'usage_runs'):
                if isinstance(row.get(key), (int, float)):
                    dest[key] = dest.get(key, 0) + row[key]
        flow = flow or {}
        raw['cards'].extend(flow.get('cards', []))
        for name, row in flow.get('lanes', {}).items():
            if name in raw.setdefault('lanes', {}):
                dest = raw['lanes'][name]
            else:
                raw['lanes'][name] = deepcopy(row)
                continue
            for key in ('run_hours', 'cost_usd', 'done'):
                dest[key] = dest.get(key, 0) + row.get(key, 0)
            for key, value in row.get('usage', {}).items():
                if isinstance(value, (int, float)):
                    dest.setdefault('usage', {})[key] = dest.get('usage', {}).get(key, 0) + value
        attention = flow.get('attention')
        if attention and raw.get('attention'):
            raw['attention']['rows'].extend(attention.get('rows', []))
            raw['attention']['available'] &= attention.get('available', False)
        for lane, series in flow.get('strip_tokens', {}).items():
            old = raw.setdefault('strip_tokens', {}).get(lane, [0] * len(series))
            raw['strip_tokens'][lane] = [a + b for a, b in zip(old, series)]
    total = sum(row.get('run_seconds', 0) for row in snap.get('lanes', {}).values())
    if total:
        for row in snap['lanes'].values():
            row['actual'] = row.get('run_seconds', 0) / total
    raw['strip_actual'] = {name: row.get('actual', 0) for name, row in snap.get('lanes', {}).items()}
    return snap, raw
