"""Deterministic read-only fleet inventory. No model calls or owner writes.

Optional $HERMES_HOME/state/red-sweep-sources.json maps gateway names to
{"argv": ["approved-reader", ...]}. Readers return the --local-json snapshot.
Only configure unattended transports already authorized on the monitor host.
Board sync-shadow files are the same replicas used by cross_task; never treat
replica presence/mtime as proof that its owner is online.
"""
import glob
import json
import os
import sqlite3
import subprocess
import time


def snapshot(home, connect, replicas=True):
    cards, sessions, errors = {}, [], []
    paths = [("default", os.path.join(home, "kanban.db"), "local")]
    paths += [(os.path.basename(os.path.dirname(p)), p, "local") for p in
              sorted(glob.glob(os.path.join(home, "kanban", "boards", "*", "kanban.db")))]
    if replicas:
        paths += [(os.path.basename(p)[:-3], p, "replica") for p in
                  sorted(glob.glob(os.path.join(home, "kanban", "sync-shadow", "*.db")))]
    for board, path, source in paths:
        if not os.path.isfile(path):
            if source == "local" and board == "default":
                errors.append("unreachable local board=default missing")
            continue
        try:
            c = connect(path)
            try:
                c.row_factory = sqlite3.Row
                columns = {r[1] for r in c.execute("pragma table_info(tasks)")}
                owner = "coalesce(created_on,'local')" if "created_on" in columns else "'local'"
                rows = c.execute("select id,status,coalesce(block_kind,'') kind,title," + owner +
                                 " owner,(select max(id) from task_events e where e.task_id=t.id "
                                 "and e.kind='blocked') event from tasks t order by id")
                for row in rows:
                    r = dict(row)
                    key = (board, r['id'])
                    # Local rows (including non-red rows) override lagging replicas.
                    if key in cards:
                        continue
                    cards[key] = dict(r, board=board, source=source)
            finally:
                c.close()
        except (OSError, sqlite3.Error):
            errors.append(f"unreachable {source} board={board} unreadable")
    path = os.path.join(home, "state.db")
    try:
        c = connect(path)
        try:
            for sid, title, raw in c.execute("select id,title,outcome_json from sessions "
                                            "where coalesce(archived,0)=0 order by id"):
                try:
                    outcome = json.loads(raw or '{}')
                except (ValueError, TypeError):
                    errors.append(f"unreachable session={sid} invalid-outcome")
                    continue
                if not isinstance(outcome, dict) or outcome.get('kind') != 'decision':
                    continue
                items = outcome.get('items') or []
                if not isinstance(items, list):
                    errors.append(f"unreachable session={sid} invalid-items")
                    items = []
                items = [i for i in items if isinstance(i, dict) and
                         i.get('status') not in ('done', 'resolved', 'withdrawn', 'closed')]
                sessions.append(dict(id=sid, title=title, items=items))
        finally:
            c.close()
    except (OSError, sqlite3.Error):
        errors.append('unreachable local sessions unreadable')
    return dict(cards=[r for r in cards.values() if r['status'] in ('blocked', 'gave_up') or
                       (r['status'] == 'triage' and r['kind'] in ('needs_input', 'capability'))],
                sessions=sessions, errors=errors)


def inventory(home, connect, run=subprocess.run, deadline_s=90, clock=time.monotonic):
    # One overall deadline: hung gateways must not outlast the cron wrapper's timeout and lose every row.
    result = snapshot(home, connect)
    stop = clock() + deadline_s
    config = os.path.join(home, 'state', 'red-sweep-sources.json')
    if not os.path.isfile(config):
        result['errors'].append('unreachable fleet coverage-config-missing')
        return result
    try:
        with open(config) as f:
            sources = json.load(f)
        if not isinstance(sources, dict):
            raise ValueError('sources must be a mapping')
    except (ValueError, OSError):
        result['errors'].append('unreachable fleet coverage-config-invalid')
        return result
    for gateway, spec in sorted(sources.items()):
        left = stop - clock()
        if left <= 0:
            result['errors'].append(f'unreachable {gateway}')
            continue
        try:
            argv = spec['argv']
            if not isinstance(argv, list) or not argv or not all(isinstance(v, str) for v in argv):
                raise ValueError('invalid argv')
            proc = run(argv, capture_output=True, text=True, timeout=min(30, left), check=True)
            remote = json.loads(proc.stdout)
            if not all(isinstance(remote[k], list) for k in ('cards', 'sessions', 'errors')):
                raise ValueError('invalid snapshot')
            for r in remote['cards']:
                if not isinstance(r, dict) or not all(isinstance(r.get(k), str) for k in
                        ('id', 'board', 'owner', 'status', 'kind', 'title', 'source')):
                    raise ValueError('invalid card')
            for s in remote['sessions']:
                if not isinstance(s, dict) or not isinstance(s.get('id'), str) or not isinstance(s.get('items'), list):
                    raise ValueError('invalid session')
                if not all(isinstance(i, dict) for i in s['items']):
                    raise ValueError('invalid items')
            if not all(isinstance(e, str) for e in remote['errors']):
                raise ValueError('invalid errors')
            # Validate the entire response before changing any fallback rows.
            # A successful gateway snapshot supersedes that owner's replica rows.
            if not remote['errors']:
                result['cards'] = [r for r in result['cards'] if not
                                   (r['source'] == 'replica' and r['owner'] == gateway)]
            for r in remote['cards']:
                result['cards'].append(dict(r, owner=gateway if r['owner'] == 'local' else r['owner'],
                                            source='remote', reader=gateway))
            for s in remote['sessions']:
                result['sessions'].append(dict(s, gateway=gateway))
            if remote['errors']:
                result['errors'].append(f'unreachable {gateway} partial-snapshot')
        except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError):
            result['errors'].append(f'unreachable {gateway}')
    return result


def render(data):
    # JSON escaping prevents titles/items from injecting monitor lines; stable ordering
    # excludes read timestamps, mtimes and transport exception text.
    lines = []
    cards = {(r['owner'], r['board'], r['id']): r for r in data['cards']}
    for key, r in sorted(cards.items()):
        lines.append('card ' + json.dumps(r, sort_keys=True, ensure_ascii=True))
    for s in sorted(data['sessions'], key=lambda s: (s.get('gateway', 'local'), s['id'])):
        s = dict(s, items=sorted(s['items'], key=lambda i: json.dumps(i, sort_keys=True)))
        lines.append('decision_session ' + json.dumps(s, sort_keys=True, ensure_ascii=True))
    lines.extend(sorted(set(data['errors'])))
    lines.append(f"red_total={len(cards)} decision_sessions={len(data['sessions'])}")
    return '\n'.join(lines)
