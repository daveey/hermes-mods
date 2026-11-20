"""Exact, authenticated owner-store absence; a note is audit, never authority."""
from contextlib import closing
import json
import math
import time


def runtime():
    from hermes_cli.kanban_origin import _hub_db
    from hermes_cli.kanban_sync_client import Hub, load_identity
    # Never use last-good/offline membership caches for an absence decision.
    return _hub_db(), Hub(load_identity()).machines()


def recent(value, now):
    value = float(value)
    return math.isfinite(value) and 0 <= now - value <= 180


def coverage(hub, registry, now, terminated=()):
    """Complete current gateway/profile inventory, not the bounded sessions catalog.

    A fresh task-scope heartbeat is emitted by the same collection as profiles.
    A caught-up replica therefore carries that collection's full profile list even
    when the unchanged gateway record itself was first published long ago.
    """
    machines = registry['machines']
    if not isinstance(machines, list) or not machines:
        raise ValueError('missing membership')
    active = {m['name'] for m in machines}
    if len(active) != len(machines) or not registry.get('me') or registry['me'] not in active:
        raise ValueError('incomplete membership')
    for m in machines:
        if not isinstance(m['name'], str) or not m['name'] or not recent(m['last_seen'] / 1000, now):
            raise ValueError('unreachable gateway')
    meta = dict(hub.execute('SELECT k,v FROM meta'))
    if (meta.get('offline_since') or not recent(meta.get('last_ok'), now)
            or meta.get('cursor') is None or meta.get('cursor') != meta.get('head')):
        raise ValueError('unknown replica')
    out = []
    published = {r[0] for r in hub.execute("SELECT DISTINCT gw FROM replica WHERE coll='gateway'")}
    for gw in sorted(active | published):
        rows = hub.execute("SELECT data FROM replica WHERE gw=? AND coll='gateway' AND key='info'", (gw,)).fetchall()
        if len(rows) != 1:
            raise ValueError('missing inventory')
        info = json.loads(rows[0][0])
        if gw not in active:
            # A publishing legacy/non-Board gateway is still an owner candidate.
            # Only an obsolete, stale inventory with explicit terminal proof can
            # be omitted; non-membership alone is never permission.
            fresh = any(s.get('state') == 'ok' and recent(s.get('observed_at'), now)
                        for s in info['task_scopes'])
            if not fresh and gw in terminated:
                continue
        profiles = info['profiles']
        if (info['gateway'] != gw or not isinstance(profiles, list) or not profiles
                or any(not isinstance(p, str) or not p for p in profiles)
                or len(profiles) != len(set(profiles))
                or not any(s.get('state') == 'ok' and recent(s.get('observed_at'), now)
                           for s in info['task_scopes'])):
            raise ValueError('unknown inventory')
        out.extend((gw, p) for p in sorted(profiles))
    return out


def owner_read(hub, gw, profile, sid, now):
    """Validate authenticated sent envelope + receiver echo, never requester prose.

    The transport permits only to_gw to submit the result. notice-receipts reads
    SessionDB.get_session read-only (including archived/unpublished sessions).
    Any recent positive, pending, rejected or malformed read vetoes absence.
    """
    found = []
    for rid, raw, state, answer, created, done in hub.execute(
            'SELECT id,body,state,result,created_at,done_at FROM sent ORDER BY created_at DESC'):
        body = json.loads(raw)
        if (body.get('to_gw'), body.get('coll'), body.get('key'), body.get('action')) != (
                gw, 'sessions', f'{profile}/{sid}', 'notice-receipts'):
            continue
        if not recent(created, now):
            continue
        res = json.loads(answer) if answer else None
        keys = body.get('args', {}).get('keys')
        if (body.get('id') != rid or state != 'done' or not recent(done, now) or done < created
                or not isinstance(keys, str) or not keys
                or not isinstance(res, dict) or res.get('code') != 'notice_receipts'
                or res.get('profile') != profile or res.get('session') != sid
                or res.get('keys') != sorted(set(keys.split(',')))
                or res.get('session_exists') is not False or res.get('delivered') != []):
            raise ValueError('unknown or existing owner')
        found.append((created, done, rid))
    if not found:
        raise ValueError('no exact owner read')
    return sorted(found)


def absent_rounds(hub, required, sid, now):
    reads = [owner_read(hub, gw, p, sid, now) for gw, p in required]
    # The second complete sweep starts only after every first-sweep answer.
    # One unordered sweep can miss a copy-before-delete session migration.
    boundary = max(rows[0][1] for rows in reads)
    first = [rows[0][2] for rows in reads]
    second = []
    for rows in reads:
        later = [r for r in rows if r[0] > boundary]
        if not later:
            raise ValueError('second absence sweep missing')
        second.append(later[0][2])
    return first + second


def terminal(record, now):
    at, uid, event = record
    proof = event.get('proof', {})
    checked = proof.get('checked_at')
    return (isinstance(uid, str) and bool(uid) and isinstance(proof.get('instance_id'), str)
            and proof['instance_id'].startswith('i-') and proof.get('ec2_state') == 'terminated'
            and type(checked) is int and 0 < checked <= at <= now and at - checked <= 300
            and bool(event.get('to')) and event['to'] != event.get('from'))


def evidence(refs, replicas, ro_connect):
    """No writes/refreshes. All errors are unknown, never exception permission."""
    refs = [r for r in refs if r[0] and r[1]]
    if not refs:
        return {}
    try:
        path, registry = runtime()
        now = time.time()
        if not path or not path.is_file() or not replicas:
            return {}
        terminations = {}
        for board_path in replicas:
            with closing(ro_connect(board_path)) as board:
                board.execute('BEGIN')
                rows = board.execute('SELECT mode,pulled_at FROM kanban_sync_state').fetchall()
                if len(rows) != 1 or rows[0][0] != 'shadow' or not recent(rows[0][1], now):
                    return {}
                for uid, raw, at in board.execute("SELECT uid,payload,created_at FROM task_events "
                                                   "WHERE kind='rehomed' AND origin_gateway='server'"):
                    event = json.loads(raw)
                    gw = event.get('from')
                    if gw and (gw not in terminations or at > terminations[gw][0]):
                        terminations[gw] = (at, uid, event)
        with closing(ro_connect(path)) as hub:
            hub.execute('BEGIN')
            dead = {gw for gw, record in terminations.items() if terminal(record, now)}
            required = coverage(hub, registry, now, dead)
            out = {}
            for ref in refs:
                lifecycle = ''
                if ref[0] not in {gw for gw, _ in required}:
                    at, uid, event = terminations.get(ref[0], (0, '', {}))
                    proof = event.get('proof', {})
                    checked = proof.get('checked_at')
                    if (proof.get('ec2_state') != 'terminated' or not proof.get('instance_id')
                            or type(checked) is not int or not 0 < checked <= at <= now
                            or at - checked > 300 or not event.get('to') or event['to'] == ref[0]):
                        continue
                    # This only explains the missing member; it is NOT identity
                    # authority for the SID. Exact absence everywhere else is
                    # mandatory independently, including any reused name.
                    lifecycle = f'; server rehomed {uid}; instance {proof["instance_id"]}; terminated checked_at={checked}'
                ids = absent_rounds(hub, required, ref[2], now)
                out[ref] = (f'follow-through: origin chat gone @session:{ref[1]}@{ref[0]}/{ref[2]}; '
                            'fresh exact owner-store absence on every registered gateway/profile; '
                            'authenticated notice-receipts: ' + ','.join(ids) + lifecycle)
            return out
    except Exception:  # health: allow BLE001 -- evidence errors are unknown, never permission
        return {}
