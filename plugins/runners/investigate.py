"""Investigate button: one Kanban card per machine, placed ON that machine, that finds out why it is saturated.

The card runs the ``machine-investigate`` skill there. Its status comes back through the Board: the
investigation card is found in the sync-shadow board replicas by its idempotency key. Pure functions apart
from the SQLite reads; the route does the create.
"""
import re
import sqlite3
from pathlib import Path

KEY_PREFIX = 'investigate-machine:'
NAME = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$')
OPEN = ('triage', 'todo', 'ready', 'scheduled', 'running', 'review')
# A remote create that has not come back with a card yet is shown as queued for this long.
PENDING_S = 15 * 60


def card_args(machine, load, now_s):
    """kanban_create args for an investigation of ``machine`` with the panel's numbers at click time."""
    nums = []
    load = load if isinstance(load, dict) else {}
    if isinstance(load.get('cpu_load1'), (int, float)):
        cpus = load.get('cpus')
        nums.append(f"CPU load1 {load['cpu_load1']}" + (f" on {cpus} cores ({round(100 * load['cpu_load1'] / cpus)}%)"
                                                         if isinstance(cpus, (int, float)) and cpus else ''))
    if isinstance(load.get('mem_used_pct'), (int, float)):
        nums.append(f"RAM {round(load['mem_used_pct'])}% used")
    if isinstance(load.get('disk_free_gb'), (int, float)):
        total = load.get('disk_total_gb')
        nums.append(f"disk {round(load['disk_free_gb'])} GB free" + (f" of {round(total)} GB" if total else ''))
    body = '\n'.join([
        f"The Runners panel's Investigate button was clicked for **{machine}**.",
        '',
        'Numbers at click time: ' + ('; '.join(nums) if nums else 'not reported') + '.',
        '',
        f'You run on {machine}. Follow the `machine-investigate` skill: run its collector, find what is loading this '
        'machine and what fills its disk, with the agent/card/session behind each process. Write a five-whys '
        'diagnosis. Apply only the fixes the skill allows on its own (package/build caches, processes of finished '
        'cards), and block with ONE grouped ask for anything else. Never delete the only copy of anything.',
        '',
        f'Finish with a summary whose first line is `{machine}: <verdict in 12 words or fewer>`, then before -> after '
        'numbers.',
    ])
    return {
        'title': f'Investigate machine load: {machine}',
        'body': body,
        'assignee': 'default',
        'placement': machine,
        'skills': ['machine-investigate', 'five-whys'],
        # Shell for ps/du/launchctl, files for the report, kanban to block with the asks.
        'requires': ['terminal', 'file', 'kanban'],
        'idempotency_key': f'{KEY_PREFIX}{machine}:{int(now_s)}',
        'max_runtime_seconds': 2700,
    }


def latest(shadow_dirs, names=None):
    """``{machine: {task, board, status, verdict, created_at, completed_at}}``: the newest investigation card
    per machine across the given board directories (sync-shadow replicas and local boards)."""
    best = {}
    for d in shadow_dirs:
        for path in sorted(Path(d).glob('*.db')) + sorted(Path(d).glob('*/kanban.db')):
            try:
                db = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=1)
            except sqlite3.Error:
                continue
            try:
                rows = db.execute(
                    'SELECT id, status, result, idempotency_key, created_at, completed_at, human_request FROM tasks '
                    'WHERE idempotency_key LIKE ? ORDER BY created_at DESC', (KEY_PREFIX + '%',)).fetchall()
            except sqlite3.Error:
                rows = []
            finally:
                db.close()
            for tid, status, result, key, created, completed, ask in rows:
                machine = key[len(KEY_PREFIX):].rsplit(':', 1)[0]
                if names is not None and machine not in names:
                    continue
                cur = best.get(machine)
                if cur is None or (created or 0) > (cur['created_at'] or 0):
                    best[machine] = dict(task=tid, board=_board(path), status=status, verdict=_verdict(result, ask),
                                         created_at=created, completed_at=completed)
    return best


def _board(path):
    """sync-shadow/<slug>.db, kanban/kanban.db (default) or kanban/boards/<slug>/kanban.db."""
    if path.name != 'kanban.db':
        return path.stem
    return path.parent.name if path.parent.parent.name == 'boards' else 'default'


def _verdict(result, ask):
    for text in (result, ask):
        if isinstance(text, str) and text.strip():
            line = text.strip().splitlines()[0].strip()
            return line[:160]
    return None


def is_open(inv):
    return bool(inv) and inv.get('status') in OPEN


def needs_user(inv):
    return bool(inv) and inv.get('status') == 'blocked'


def attach(machines, found, pending, now_s):
    """Put each gateway row's investigation (found card, else a recent queued request) on the row."""
    for m in machines:
        inv = found.get(m['name'])
        p = pending.get(m['name'])
        newer = p and now_s - p['at'] < PENDING_S and (inv is None or p['at'] > (inv.get('created_at') or 0))
        if newer and not (inv and p.get('task') and inv['task'] == p['task']):
            inv = dict(task=p.get('task'), board=None, status='queued', verdict=None, created_at=int(p['at']),
                       completed_at=None)
        m['investigation'] = inv
    return machines
