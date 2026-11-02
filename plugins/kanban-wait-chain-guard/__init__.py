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


def _connect(board=None):
    path = _db_path(board)
    if not path.is_file():
        return None
    conn = sqlite3.connect(f'file:{path}?mode=ro', uri=True, timeout=2.0)
    conn.row_factory = sqlite3.Row
    return conn


def _task(conn, tid):
    return conn.execute('SELECT * FROM tasks WHERE id = ?', (tid,)).fetchone()


def _last_progress(conn, task):
    marks = ','.join('?' * len(PROGRESS))
    row = conn.execute(f'SELECT MAX(created_at) FROM task_events WHERE task_id = ? AND kind IN ({marks})',
                       (task['id'], *PROGRESS)).fetchone()
    return float(row[0] or task['created_at'] or 0)


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
    if chain:
        gp = chain[0]
        text = (f"{parent['id']} is itself waiting on {gp['id']} ({gp['status']}, idle {_hours(conn, gp, now):.0f}h); "
                f"{parent['id']} is {parent['status']}, idle {idle:.0f}h")
        if len(chain) > 1:
            text += '; chain: ' + ' -> '.join([parent['id']] + [c['id'] + f" ({c['status']})" for c in chain])
    else:
        text = f"{parent['id']} has made no progress in {idle:.0f}h (status {parent['status']}, idle {idle:.0f}h)"
    return text


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


def _verdict(tool, args, now=None):
    """('refuse'|'hint', message) or None."""
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
