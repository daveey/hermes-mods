"""Synthetic broken-session fixtures, one builder per pattern of skills/devops/broken-sessions (positive + negative).

Each builder takes a board connection made by core's `kanban_db_connect.connect` (writable: this is the test's own
board) and `now`, creates its card with core's `kanban_db.create_task`, then sets the broken state with plain SQL
(status, timestamps, events), the same rows a real board holds. A new real example from
`~/.hermes/skills/devops/broken-sessions/examples/<id>/` becomes a builder here (no personal data: synthetic
titles, ids from create_task) and a test in test_patterns.py.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any, Optional

CHAT = 'S1'


def card(conn, title: str, *, status: str, session: Optional[str] = CHAT, parents=(), **cols: Any) -> str:
    from hermes_cli import kanban_db
    tid = kanban_db.create_task(conn, title=title, initial_status='running', session_id=session, assignee='default',
                                parents=list(parents))
    have = {r[1] for r in conn.execute('PRAGMA table_info(tasks)')}
    sets = {'status': status, **{k: v for k, v in cols.items() if k in have}}  # sync columns only on synced boards
    conn.execute(f"UPDATE tasks SET {', '.join(f'{k} = ?' for k in sets)} WHERE id = ?", (*sets.values(), tid))
    conn.commit()
    return tid


def event(conn, tid: str, kind: str, payload: Any = None, *, at: float, run_id: Optional[int] = None) -> int:
    cur = conn.execute('INSERT INTO task_events (task_id, run_id, kind, payload, created_at) VALUES (?, ?, ?, ?, ?)',
                       (tid, run_id, kind, json.dumps(payload or {}), int(at)))
    conn.commit()
    return cur.lastrowid


def age_all_events(conn, tid: str, at: float) -> None:
    """Move the card's creation-time events back to `at` (create_task stamps them now)."""
    conn.execute('UPDATE task_events SET created_at = ? WHERE task_id = ?', (int(at), tid))
    conn.execute('UPDATE tasks SET created_at = ? WHERE id = ?', (int(at), tid))
    conn.commit()


# ── BS-ready-worker-free ─────────────────────────────────────────────────────────────────────────────────────────
def long_wait(conn, now, *, waited_s=3000, kind='ci', status='scheduled'):
    tid = card(conn, 'Waiting for producer', status=status)
    age_all_events(conn, tid, now-waited_s)
    event(conn, tid, 'scheduled', {'awaiting': {'kind':kind, 'ref':'job'}, 'reason':'producer',
                                 'wake_at':now+86400}, at=now-waited_s)
    return tid


def legacy_probe_wait(conn, now, *, waited_s=3000, reason='deploy rollout: waiting for worker update window',
                      wake_check='python3 rollout_ready.py'):
    """Legacy deploy park: no awaiting, an external probe and a future backstop."""
    tid = card(conn, 'Waiting for rollout', status='scheduled')
    age_all_events(conn, tid, now-waited_s)
    event(conn, tid, 'scheduled', {'reason':reason, 'wake_check':wake_check,
                                 'wake_at':now+2700}, at=now-waited_s)
    return tid


def ready_worker_free(conn, now, *, waited_s=2400):
    tid = card(conn, 'Ready, unclaimed, free slot', status='ready', ready_since=int((now - waited_s) * 1000))
    age_all_events(conn, tid, now - waited_s)
    return tid


def ready_leftover_claim(conn, now):
    tid = card(conn, 'Ready but still claimed', status='ready', ready_since=int((now - 3600) * 1000),
               claim_lock='host:123', worker_pid=123)
    age_all_events(conn, tid, now - 3600)
    return tid


def ready_with_old_skip(conn, now):
    """Ready 2 h; the dispatcher recorded a skip 50 min ago (its episode started then): explained, however old."""
    tid = ready_worker_free(conn, now, waited_s=7200)
    event(conn, tid, 'dispatch_skipped', {'reason': 'llm_paced', 'detail': 'pool paced'}, at=now - 3000)
    return tid


# ── BS-overdue-wake ──────────────────────────────────────────────────────────────────────────────────────────────
def overdue_clock(conn, now, *, late_s=3600):
    tid = card(conn, 'Parked on a clock wake', status='scheduled')
    age_all_events(conn, tid, now - late_s - 600)
    event(conn, tid, 'scheduled', {'wake_at': int(now - late_s), 'reason': 'wait for the nightly run'}, at=now - late_s - 600)
    return tid


def scheduled_no_wake(conn, now, *, parked_s=3600):
    tid = card(conn, 'Parked with no wake', status='scheduled')
    age_all_events(conn, tid, now - parked_s)
    event(conn, tid, 'scheduled', {'reason': 'later'}, at=now - parked_s)
    return tid


def overdue_probe(conn, now, *, late_s=3600):
    tid = card(conn, 'Parked on a probe', status='scheduled')
    age_all_events(conn, tid, now - late_s - 600)
    event(conn, tid, 'scheduled', {'reason': 'wait for CI'}, at=now - late_s - 600)
    cols = {r[1] for r in conn.execute('PRAGMA table_info(task_wake_checks)')}
    row = {'task_id': tid, 'cmd': 'true', 'every': 900, 'next_at': int(now - late_s), 'fails': 0}
    if 'purpose' in cols:
        row['purpose'] = 'wake'
    conn.execute(f"INSERT INTO task_wake_checks ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})", tuple(row.values()))
    conn.commit()
    return tid


# ── BS-unexplained-wait ──────────────────────────────────────────────────────────────────────────────────────────
VALID_ASK = {'action': 'Approve the deploy in the console. Unblock = it ships; archive = it is dropped',
             'why_human': 'only the account owner can approve', 'interaction': 'approval',
             'scope': 'one deploy', 'evidence': 'checked the console'}


def blocked_no_reason(conn, now, *, waited_s=3600):
    tid = card(conn, 'Blocked, no reason', status='blocked')
    age_all_events(conn, tid, now - waited_s)
    event(conn, tid, 'blocked', {}, at=now - waited_s)
    return tid


def blocked_reason_in_error(conn, now, *, waited_s=3600):
    tid = card(conn, 'Blocked, reason in error', status='blocked')
    age_all_events(conn, tid, now - waited_s)
    event(conn, tid, 'blocked', {'error': 'git push rejected'}, at=now - waited_s)
    return tid


def blocked_invalid_ask(conn, now):
    tid = card(conn, 'Blocked on an invalid ask', status='blocked', block_kind='needs_input',
               human_request=json.dumps({'action': 'do it'}))
    age_all_events(conn, tid, now - 3600)
    event(conn, tid, 'blocked', {'reason': 'need the user'}, at=now - 3600)
    return tid


def blocked_valid_ask(conn, now):
    tid = card(conn, 'Blocked on a real ask', status='blocked', block_kind='needs_input',
               human_request=json.dumps(VALID_ASK))
    age_all_events(conn, tid, now - 5 * 3600)
    event(conn, tid, 'blocked', {'reason': 'need approval'}, at=now - 5 * 3600)
    return tid


def blocked_agent_owned(conn, now, *, waited_s=3 * 3600):
    tid = card(conn, 'Blocked on a transient error', status='blocked', block_kind='transient')
    age_all_events(conn, tid, now - waited_s)
    event(conn, tid, 'blocked', {'reason': 'rate limited, retry later'}, at=now - waited_s)
    return tid


# ── BS-parent-done ───────────────────────────────────────────────────────────────────────────────────────────────
def todo_parents_done(conn, now, *, done_s=1800):
    parent = card(conn, 'Finished parent', status='done', session=None, completed_at=int(now - done_s))
    tid = card(conn, 'Todo after a finished parent', status='todo', parents=[parent])
    age_all_events(conn, tid, now - done_s - 60)
    return tid


def todo_parent_cancelled(conn, now):
    parent = card(conn, 'Cancelled parent', status='cancelled', session=None, completed_at=int(now - 3600))
    tid = card(conn, 'Todo after a cancelled parent', status='todo', parents=[parent])
    age_all_events(conn, tid, now - 3600)
    return tid


def blocked_dependency_breaker(conn, now):
    """Dependency-blocked, parents done, but at the failure breaker: core's recovery_held keeps it on purpose."""
    parent = card(conn, 'Finished parent', status='done', session=None, completed_at=int(now - 3600))
    tid = card(conn, 'Held at the breaker', status='blocked', block_kind='dependency', parents=[parent],
               consecutive_failures=5)
    age_all_events(conn, tid, now - 3600)
    return tid


def todo_parent_open(conn, now):
    parent = running_healthy(conn, now)
    conn.execute('UPDATE tasks SET session_id = NULL WHERE id = ?', (parent,))
    tid = card(conn, 'Todo waiting on a running parent', status='todo', parents=[parent])
    age_all_events(conn, tid, now - 3600)
    return tid


def scheduled_awaiting_done(conn, now, *, done_s=1800):
    awaited = card(conn, 'Awaited card', status='done', session=None, completed_at=int(now - done_s))
    tid = card(conn, 'Parked on another card', status='scheduled')
    age_all_events(conn, tid, now - 2 * done_s)
    event(conn, tid, 'scheduled', {'wake_at': int(now + 86400), 'reason': f'waits on {awaited}',
                                   'awaiting': {'kind': 'card', 'ref': awaited, 'why': 'its result'}}, at=now - 2 * done_s)
    return tid, awaited


# ── BS-stuck-run ─────────────────────────────────────────────────────────────────────────────────────────────────
def _run(conn, tid, now, *, started_s, heartbeat_s=None, status='running'):
    cur = conn.execute('INSERT INTO task_runs (task_id, profile, status, started_at, last_heartbeat_at) VALUES (?, ?, ?, ?, ?)',
                       (tid, 'default', status, int(now - started_s),
                        int(now - heartbeat_s) if heartbeat_s is not None else None))
    conn.execute('UPDATE tasks SET current_run_id = ? WHERE id = ?', (cur.lastrowid, tid))
    conn.commit()
    return cur.lastrowid


def running_quiet(conn, now, *, quiet_s=2 * 3600):
    tid = card(conn, 'Running, silent', status='running', claim_lock='host:1', claim_expires=int(now + 3600),
               started_at=int(now - quiet_s))
    age_all_events(conn, tid, now - quiet_s)
    _run(conn, tid, now, started_s=quiet_s)
    return tid


def running_claim_expired(conn, now):
    tid = card(conn, 'Running, claim expired', status='running', claim_lock='host:1', claim_expires=int(now - 1800),
               started_at=int(now - 600))
    age_all_events(conn, tid, now - 600)
    _run(conn, tid, now, started_s=600, heartbeat_s=60)
    event(conn, tid, 'heartbeat', {}, at=now - 60)
    return tid


def synced_columns(conn) -> None:
    """The Board-lease columns a synced board has (a fresh local board does not)."""
    have = {r[1] for r in conn.execute('PRAGMA table_info(tasks)')}
    for col, typ in (('lease_holder', 'TEXT'), ('lease_expires', 'INTEGER')):
        if col not in have:
            conn.execute(f'ALTER TABLE tasks ADD COLUMN {col} {typ}')
    conn.commit()


def running_lease_expired(conn, now):
    synced_columns(conn)
    tid = card(conn, 'Running on a worker whose lease lapsed', status='running', started_at=int(now - 3600),
               lease_holder='worker-x', lease_expires=int((now - 1800) * 1000))
    age_all_events(conn, tid, now - 600)
    _run(conn, tid, now, started_s=3600, heartbeat_s=60)
    event(conn, tid, 'heartbeat', {}, at=now - 60)
    return tid


def running_no_claim(conn, now):
    tid = card(conn, 'Running with nothing behind it', status='running', started_at=int(now - 600))
    age_all_events(conn, tid, now - 60)
    return tid


def running_healthy(conn, now):
    tid = card(conn, 'Running, heartbeating', status='running', claim_lock='host:1', claim_expires=int(now + 3600),
               started_at=int(now - 3 * 3600))
    age_all_events(conn, tid, now - 3 * 3600)
    _run(conn, tid, now, started_s=3 * 3600, heartbeat_s=60)
    event(conn, tid, 'heartbeat', {}, at=now - 60)
    return tid


# ── BS-review-no-reviewer ────────────────────────────────────────────────────────────────────────────────────────
def review_idle(conn, now, *, idle_s=2 * 3600):
    tid = card(conn, 'In review, nobody reviewing', status='review')
    age_all_events(conn, tid, now - idle_s)
    event(conn, tid, 'review_requested', {}, at=now - idle_s)
    return tid


def review_running(conn, now):
    tid = review_idle(conn, now)
    _run(conn, tid, now, started_s=600, heartbeat_s=30)
    return tid


# ── BS-broken-chain ──────────────────────────────────────────────────────────────────────────────────────────────
def missing_parent(conn, now):
    tid = card(conn, 'Waits on a card nobody has', status='todo')
    conn.execute('INSERT INTO task_links (parent_id, child_id) VALUES (?, ?)', ('t_00000000deadbeef', tid))
    conn.commit()
    age_all_events(conn, tid, now - 3600)
    return tid


def awaiting_missing(conn, now):
    tid = card(conn, 'Parked on a card nobody has', status='scheduled')
    age_all_events(conn, tid, now - 3600)
    event(conn, tid, 'scheduled', {'wake_at': int(now + 86400), 'reason': 'waits on t_00000000feedface',
                                   'awaiting': {'kind': 'card', 'ref': 't_00000000feedface', 'why': 'its result'}},
          at=now - 3600)
    return tid


def repair_card(conn, now):
    """A card the repair session filed (its idempotency key): broken on purpose, never judged."""
    tid = blocked_no_reason(conn, now)
    conn.execute("UPDATE tasks SET idempotency_key = 'session-repair:BS-x:default/t_1:20261121' WHERE id = ?", (tid,))
    conn.commit()
    return tid


def cycle(conn, now):
    a = card(conn, 'Cycle A', status='todo')
    b = card(conn, 'Cycle B', status='todo', session=None)
    conn.execute('INSERT INTO task_links (parent_id, child_id) VALUES (?, ?)', (b, a))
    conn.execute('INSERT INTO task_links (parent_id, child_id) VALUES (?, ?)', (a, b))
    conn.commit()
    age_all_events(conn, a, now - 3600)
    return a, b


# ── BS-notice-undelivered (state.db) ─────────────────────────────────────────────────────────────────────────────
def state_db(path):
    """A minimal state.db with the columns the notice rule reads (core's schema has many more)."""
    c = sqlite3.connect(path)
    c.executescript('''
        CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, title TEXT, parent_session_id TEXT,
            archived INTEGER DEFAULT 0, muted INTEGER DEFAULT 0, end_reason TEXT, source TEXT DEFAULT 'desktop',
            outcome_json TEXT, model_config TEXT,
            started_at REAL, pinned INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, role TEXT,
            content TEXT, timestamp REAL, display_kind TEXT, display_metadata TEXT, active INTEGER DEFAULT 1);
    ''')
    c.commit()
    return c


def notice_chat(c, sid: str, now: float, *, age_s: float, closeout: Optional[dict] = None, card_id: str = '',
                followed: bool = False, archived: int = 0, blocked_on_human: bool = False):
    c.execute('INSERT INTO sessions (id, title, archived, started_at) VALUES (?, ?, ?, ?)', (sid, f'Chat {sid}', archived, now - 86400))
    c.execute('INSERT INTO messages (session_id, role, content, timestamp) VALUES (?, ?, ?, ?)',
              (sid, 'user', 'do the thing', now - age_s - 600))
    meta = {'kanban_event_keys': [f'default:{card_id}:1'] if card_id else []}
    if closeout is not None:
        meta['closeout'] = closeout
    if blocked_on_human:
        meta['blocked_on_human'] = True
    c.execute('INSERT INTO messages (session_id, role, content, timestamp, display_kind, display_metadata) VALUES (?, ?, ?, ?, ?, ?)',
              (sid, 'user', f'Kanban: card {card_id} completed', now - age_s, 'kanban_notice', json.dumps(meta)))
    if followed:
        c.execute('INSERT INTO messages (session_id, role, content, timestamp) VALUES (?, ?, ?, ?)',
                  (sid, 'assistant', 'done, here is the summary', now - age_s + 30))
    c.commit()
