"""Kanban wait-chain guard: parking behind a parked card is not a started fix.

kanban_link, kanban_block(kind=dependency) and kanban_block/kanban_schedule(awaiting={kind: card}) used to return
a plain ok when the card waited on was itself parked (todo/scheduled/blocked) behind another parked card, or had
made no progress for a day. Agents read that ok as "an owner has it", so chains of watchers formed with no doer
(defect agent-ff9c2f154c: shepherd t_e3e216d4, 141 runs, behind todo t_7e2ee45c behind scheduled coordinator).

This plugin appends a hint to those results, and refuses the park outright when the waiting card has already
spent ``REFUSE_RUNS`` runs without progress. A parent that is a blocked human ask (``human_request`` set) is the
prescribed place to wait and is never flagged. Read-only; any error fails open.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
import sqlite3
import time
from pathlib import Path

TOOLS = {'kanban_link', 'kanban_block', 'kanban_schedule'}
NESTED = {'tool_call'}
PARKED = {'todo', 'scheduled', 'blocked', 'triage'}
CLOSED = {'done', 'archived'}
# Events that mean the card moved forward. Wake/unblock/claim/run/park/heartbeat/comment/link cycles are not
# progress: 'unblocked' is mostly automated wakes (resolved checks, coordinators), and t_e3e216d4 had one every day.
PROGRESS = ('created', 'completed', 'review_requested', 'changes_requested', 'edited',
            'specified', 'goal_verified', 'pr_acceptance', 'assigned')
OK_RUN_OUTCOMES = ('completed', 'review_requested', 'changes_requested')
# A parent in one of these states has a doer on it now: waiting behind it is a handoff, not a watcher chain.
ACTIVE = {'running', 'ready', 'review'}
IDLE_HOURS = 24.0
REFUSE_RUNS = 20
CHAIN_DEPTH = 4
TAG = 'kanban-wait-chain-guard'
ADVICE = ('parking here is not a started fix: do the step yourself, re-place the card on a gateway with capacity, '
          'complete it with what is proven (say NO_PROOF plainly), or file the one decision for David '
          '(kanban_create initial_status=blocked with human_request) and wait behind that card.')


def _name(tool_name):
    name = str(tool_name or '')
    return name[5:] if name.startswith('mcp__') else name


def _db_path(board=None):
    try:
        from hermes_cli.kanban_db import kanban_db_path  # type: ignore  # honours the HERMES_KANBAN_DB pin
        return Path(kanban_db_path(board))
    except Exception:
        pass
    pinned = (os.environ.get('HERMES_KANBAN_DB') or '').strip()
    if pinned:
        return Path(pinned)
    home = Path(os.environ.get('HERMES_HOME') or Path.home() / '.hermes')
    if board and board != 'default':
        return home / 'kanban' / 'boards' / board / 'kanban.db'
    return home / 'kanban.db'


def _ro_connect(path, timeout=2.0):
    """Read-only open. mode=ro fails on a WAL db while its -wal/-shm is absent (the dispatcher's last close deletes
    them), so fall back to mode=rw + query_only: same data, never writes, never creates a missing db."""
    try:
        conn = sqlite3.connect(f'file:{path}?mode=ro', uri=True, timeout=timeout)
    except sqlite3.OperationalError:
        conn = None
    if conn is not None:
        try:
            conn.execute('select 1 from sqlite_master limit 1')
            return conn
        except sqlite3.OperationalError:
            conn.close()
    conn = sqlite3.connect(f'file:{path}?mode=rw', uri=True, timeout=timeout)
    conn.execute('PRAGMA query_only=ON')
    return conn


def _connect(board=None):
    path = _db_path(board)
    if not path.is_file():
        return None
    conn = _ro_connect(path, timeout=2.0)
    conn.row_factory = sqlite3.Row
    return conn


def _task(conn, tid):
    return conn.execute('SELECT * FROM tasks WHERE id = ?', (tid,)).fetchone()


def _max(conn, sql, params):
    try:
        row = conn.execute(sql, params).fetchone()
    except sqlite3.Error:
        return 0.0  # older/narrower schema: that signal just does not count
    return float((row[0] if row else 0) or 0)


def _own_progress(conn, task):
    """Latest moment ``task`` itself moved forward."""
    tid = task['id']
    marks = ','.join('?' * len(PROGRESS))
    best = _max(conn, f'SELECT MAX(created_at) FROM task_events WHERE task_id = ? AND kind IN ({marks})',
                (tid, *PROGRESS))
    # A run that ended in a handoff (complete / review) is progress even if the event row is missing.
    oks = ','.join('?' * len(OK_RUN_OUTCOMES))
    best = max(best, _max(conn, f'SELECT MAX(COALESCE(ended_at, started_at)) FROM task_runs '
                                f'WHERE task_id = ? AND outcome IN ({oks})', (tid, *OK_RUN_OUTCOMES)))
    # Linking a NEW parent hands the next step to a fresh doer (t_01a188c7c974c529 linked a new reviewer per PR
    # split). The link table's primary key means re-parking behind the same card never writes another 'linked'.
    best = max(best, _max(conn, "SELECT MAX(created_at) FROM task_events WHERE task_id = ? AND kind = 'linked' "
                                "AND (payload IS NULL OR json_extract(payload, '$.child') = ?)", (tid, tid)))
    return best


def _active_parents(conn, tid):
    return [p for p in _open_parents(conn, tid) if p['status'] in ACTIVE]


def _last_progress(conn, task):
    """Own progress, or the progress of the freshest active (running/ready/review) parent it waits on.

    A dependency_wait behind an active parent is a handoff to a doer, so it counts too; behind a parked parent it
    is the watcher loop this guard exists for (t_e3e216d4) and does not.
    """
    best = max(_own_progress(conn, task), float(task['created_at'] or 0))
    active = _active_parents(conn, task['id'])
    if active:
        best = max(best, _max(conn, "SELECT MAX(created_at) FROM task_events WHERE task_id = ? "
                                    "AND kind = 'dependency_wait'", (task['id'],)))
        for p in active:
            best = max(best, _own_progress(conn, p), float(p['created_at'] or 0),
                       _max(conn, 'SELECT MAX(started_at) FROM task_runs WHERE task_id = ?', (p['id'],)))
    return best


def _open_parents(conn, tid):
    rows = conn.execute('SELECT t.* FROM task_links l JOIN tasks t ON t.id = l.parent_id WHERE l.child_id = ?'
                        ' ORDER BY t.id', (tid,)).fetchall()
    return [r for r in rows if r['status'] not in CLOSED]


def _human_ask(task):
    try:
        hr = task['human_request']
    except (IndexError, KeyError):
        return False
    return task['status'] == 'blocked' and str(hr or '').strip() not in ('', 'null', '{}')


def _hours(conn, task, now):
    return max(0.0, (now - _last_progress(conn, task)) / 3600.0)


def _iso(ts):
    return datetime.fromtimestamp(int(ts), timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _wake_at(conn, task, now):
    """Future wake time of a scheduled ``task`` (its latest 'scheduled' event: payload wake_at, else the reason's
    SCHEDULED_UNTIL=), or None."""
    if task is None or task['status'] != 'scheduled':
        return None
    try:
        row = conn.execute("SELECT payload FROM task_events WHERE task_id = ? AND kind = 'scheduled' "
                           "ORDER BY created_at DESC, id DESC LIMIT 1", (task['id'],)).fetchone()
        data = json.loads(row[0]) if row and row[0] else {}
        wake = data.get('wake_at') if isinstance(data, dict) else None
        if not wake:
            m = WAKE.search(str((data or {}).get('reason') or ''))
            if m:
                dt = datetime.fromisoformat(m[1].replace('Z', '+00:00'))
                wake = (dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt).timestamp()
        wake = float(wake) if wake else None
    except (sqlite3.Error, ValueError, TypeError):
        return None
    return wake if wake and wake > now else None


def _time_park(conn, cards, now):
    """agent-0e5f1ab1f9: a scheduled card in the chain wakes on a clock. Name that wake and the park the reply check
    accepts (kind=time until it, or David's decision), not another dependency park behind it."""
    for c in cards:
        wake = _wake_at(conn, c, now)
        if wake:
            iso = _iso(wake)
            return (f" {c['id']} wakes at {iso} (scheduled on a clock). If nothing here can run before then, park "
                    f"this card on that clock instead: kanban_block(kind='time', reason='SCHEDULED_UNTIL={iso} — …') "
                    f"or kanban_schedule; if lifting the gate is David's call, kanban_block(kind='needs_input', "
                    f"human_request=…). A dependency park behind it is not an ending")
    return ''


def _stalled(conn, parent, now):
    """Hint text when ``parent`` is a parked card going nowhere, else None."""
    if parent is None or parent['status'] not in PARKED or _human_ask(parent):
        return None
    idle = _hours(conn, parent, now)
    chain, seen, cur = [], {parent['id']}, parent
    for _ in range(CHAIN_DEPTH):
        nxt = next((p for p in _open_parents(conn, cur['id'])
                    if p['status'] in PARKED and p['id'] not in seen), None)
        if nxt is None:
            break
        chain.append(nxt)
        seen.add(nxt['id'])
        if _human_ask(nxt):
            return None  # the chain ends at David's ask: the prescribed wait, not a watcher loop
        cur = nxt
    if not chain and idle < IDLE_HOURS:
        return None
    clock = _time_park(conn, [parent] + chain, now)
    if chain:
        gp = chain[0]
        text = (f"{parent['id']} is itself waiting on {gp['id']} ({gp['status']}, idle {_hours(conn, gp, now):.0f}h); "
                f"{parent['id']} is {parent['status']}, idle {idle:.0f}h")
        if len(chain) > 1:
            text += '; chain: ' + ' -> '.join([parent['id']] + [c['id'] + f" ({c['status']})" for c in chain])
    else:
        text = f"{parent['id']} has made no progress in {idle:.0f}h (status {parent['status']}, idle {idle:.0f}h)"
    return text + ('.' + clock if clock else '')


def _targets(tool, args):
    """(board, child_id, [parent ids] or None for the child's own open parents) for a park/link call."""
    if tool == 'kanban_link':
        parent, child = args.get('parent_id'), args.get('child_id')
        return (args.get('board'), child, [parent]) if parent and child else None
    if tool not in ('kanban_block', 'kanban_schedule'):
        return None
    child = args.get('task_id') or os.environ.get('HERMES_KANBAN_TASK') or ''
    parents = []
    awaiting = args.get('awaiting')
    if isinstance(awaiting, dict) and awaiting.get('kind') == 'card' and awaiting.get('ref') \
            and not awaiting.get('gateway'):
        parents.append(str(awaiting['ref']))
    dependency = tool == 'kanban_block' and args.get('kind') == 'dependency'
    if not parents and not dependency:
        return None
    return args.get('board'), child, parents or None


REMINDER = re.compile(r'\b(nudge|remind(er)?|re-?ping|ping|chase|follow[- ]?up)\b', re.I)
DURATION = re.compile(r'(\d+(\.\d+)?)\s*(h|hrs?|hours?|d|days?)\b', re.I)
MENTION = re.compile(r'nudge|remind|ping|chase|follow', re.I)
WAKE = re.compile(r'SCHEDULED_UNTIL=([^\s]+)')


def _reminder_verdict(args, now):
    """Read the current wait's first park; never mutate the card or its clock."""
    if MENTION.search(str(args.get('reason') or '')):
        return None
    tid = args.get('task_id') or os.environ.get('HERMES_KANBAN_TASK')
    if not tid:
        return None
    conn = _connect(args.get('board'))
    if conn is None:
        return None
    try:
        task = _task(conn, tid)
        if task is None:
            return None
        candidates = [(float(m[1]) * (24 if m[3].lower().startswith('d') else 1), line)
                      for line in (task['body'] or '').splitlines() if REMINDER.search(line)
                      for m in DURATION.finditer(line)]
        if not candidates:
            return None
        hours, line = min(candidates, key=lambda item: item[0])
        # Event id breaks ties when a reset and its first park share one second.
        events = conn.execute('SELECT kind, created_at FROM task_events WHERE task_id = ? '
                              'ORDER BY created_at, id', (tid,)).fetchall()
        anchor = None
        for event in events:
            if event['kind'] in ('created', 'edited', 'specified', 'unblocked'):
                anchor = None
            elif event['kind'] == 'scheduled' and anchor is None:
                anchor = float(event['created_at'])
        due = (now if anchor is None else anchor) + hours * 3600
        match = WAKE.search(str(args.get('reason') or ''))
        if match:
            dt = datetime.fromisoformat(match[1].replace('Z', '+00:00'))
            wake = dt.replace(tzinfo=timezone.utc).timestamp() if dt.tzinfo is None else dt.timestamp()
        else:
            wake = now + 24 * 3600
        if not (wake > due + 30 * 60 and now <= due + 24 * 3600):
            return None
        due_iso = datetime.fromtimestamp(due, timezone.utc).isoformat().replace('+00:00', 'Z')
        wake_iso = datetime.fromtimestamp(wake, timezone.utc).isoformat().replace('+00:00', 'Z')
        return 'refuse', (
            f'{TAG}: refused. This card owes a reminder after {hours:g} h ("{line[:160]}"), '
            f'due {due_iso}, but this park wakes at {wake_iso}, after it. '
            'The wake_check only fires when they reply, so nothing would wake to send it. '
            f"Park with reason 'SCHEDULED_UNTIL={due_iso} — …' (keep the wake_check), "
            'send the reminder on that wake, then re-park to the deadline. '
            'If it is already past due, send it now first. If no reminder is owed any more '
            '(they replied, or you sent it), say so in the reason (mention the nudge) and retry.')
    finally:
        conn.close()


def _verdict(tool, args, now=None):
    """('refuse'|'hint', message) or None."""
    now = time.time() if now is None else now
    if tool == 'kanban_schedule':
        try:
            reminder = _reminder_verdict(args, now)
        except Exception:
            reminder = None  # fail open without disabling the existing chain check
        if reminder:
            return reminder
    t = _targets(tool, args)
    if t is None:
        return None
    board, child_id, parent_ids = t
    now = time.time() if now is None else now
    conn = _connect(board)
    if conn is None:
        return None
    try:
        child = _task(conn, child_id) if child_id else None
        if parent_ids is None:
            parents = _open_parents(conn, child_id) if child_id else []
        else:
            parents = [_task(conn, pid) for pid in parent_ids]
        hints = [h for h in (_stalled(conn, p, now) for p in parents) if h]
        if not hints:
            return None
        runs = 0
        if child is not None:
            runs = conn.execute('SELECT COUNT(*) FROM task_runs WHERE task_id = ? AND started_at > ?',
                                (child['id'], _last_progress(conn, child))).fetchone()[0]
        body = '; '.join(hints)
        if runs >= REFUSE_RUNS:
            return 'refuse', (f"{TAG}: refused. {child_id} has {runs} runs since its last progress, and "
                              f"{body}. Another park adds a watcher, not a doer: {ADVICE}")
        return 'hint', f'{TAG}: {body}. {ADVICE}'
    finally:
        conn.close()


def _verdicts(tool_name, args):
    tool = _name(tool_name)
    if not isinstance(args, dict):
        return []
    if tool in NESTED:
        calls = args.get('calls')
        return [v for c in (calls if isinstance(calls, list) else []) if isinstance(c, dict)
                for v in _verdicts(c.get('name'), c.get('arguments'))]
    if tool not in TOOLS:
        return []
    try:
        v = _verdict(tool, args)
    except Exception:
        return []  # read-only advisory: never break the tool on a schema/lock surprise
    return [v] if v else []


def pre_tool_call(tool_name='', args=None, **_):
    refusals = [m for kind, m in _verdicts(tool_name, args) if kind == 'refuse']
    if refusals:
        return {'action': 'block', 'message': '\n'.join(refusals)}
    return None


def transform_tool_result(tool_name='', args=None, result=None, **_):
    hints = [m for kind, m in _verdicts(tool_name, args) if kind == 'hint']
    if not hints:
        return None
    hint = '\n'.join(hints)
    if isinstance(result, str):
        if TAG + ':' in result:
            return None
        try:
            data = json.loads(result)
        except (ValueError, TypeError):
            data = None
        if isinstance(data, dict):
            data['warning'] = (str(data['warning']) + '\n' if data.get('warning') else '') + hint
            return json.dumps(data)
        return result + '\n\nWarning: ' + hint
    if isinstance(result, dict):
        if TAG + ':' in str(result.get('warning', '')):
            return None
        return {**result, 'warning': (str(result['warning']) + '\n' if result.get('warning') else '') + hint}
    return None


def register(ctx):
    ctx.register_hook('pre_tool_call', pre_tool_call)
    ctx.register_hook('transform_tool_result', transform_tool_result)
