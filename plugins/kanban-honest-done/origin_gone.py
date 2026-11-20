"""Read-only, independently checked exception for a destroyed origin gateway.

A note is an audit trail, never authority. Only server-authored Board rehome
proof plus fresh membership and session-absence observations can justify it.
"""
from contextlib import closing
import json
import math
import re
import sqlite3
import time


def runtime():
    from hermes_cli.kanban_origin import _hub_db
    from hermes_cli.kanban_sync_client import Hub, load_identity
    # Do not use last-good/offline membership caches for an absence decision.
    return _hub_db(), Hub(load_identity()).machines()


def recent(value, now):
    value = float(value)
    return math.isfinite(value) and 0 <= now - value <= 180


def evidence(refs, replicas, ro_connect):
    """Map qualified refs to the exact durable note required by Gate D.

    No writes, refreshes, initializations or exception-based permission. Missing
    or stale replicas, malformed observations and transport failures mean unknown.
    """
    result = {}
    refs = [ref for ref in refs if ref[0] and ref[1]]
    if not refs:
        return result
    try:
        hub_path, registry = runtime()
        machines = registry['machines']
        if (not isinstance(machines, list) or any(not isinstance(m, dict)
                or not isinstance(m.get('name'), str) or not m['name'] for m in machines)):
            return result
        # The authenticated /v2/machines endpoint lists active members. Any
        # entry for the gateway vetoes old rehome proof, including re-enrollment.
        active = {m['name'] for m in machines}
        if not registry.get('me') or registry['me'] not in active:
            return result
        now = time.time()
        if not hub_path or not hub_path.is_file():
            return result
        with closing(ro_connect(hub_path)) as hub:
            hub.execute('BEGIN')
            meta = dict(hub.execute('SELECT k,v FROM meta'))
            if (meta.get('offline_since') or not recent(meta.get('last_ok'), now)
                    or meta.get('cursor') is None or meta.get('cursor') != meta.get('head')):
                return result
            # Include every profile/gateway, not just the named origin. A
            # migrated or restored session still has an owner and must hear us.
            sessions = []
            for key, raw in hub.execute("SELECT key,data FROM replica WHERE coll='sessions'"):
                data = json.loads(raw)
                if not isinstance(data, dict):
                    return {}
                sessions.append((key.rsplit('/', 1)[-1], data.get('id')))
            candidates = [ref for ref in refs if ref[0] not in active
                          and not any(ref[2] in pair for pair in sessions)]
            for path in replicas:
                with closing(ro_connect(path)) as board:
                    board.execute('BEGIN')
                    state = board.execute('SELECT mode,pulled_at FROM kanban_sync_state').fetchall()
                    if len(state) != 1 or state[0][0] != 'shadow' or not recent(state[0][1], now):
                        continue
                    for ref in candidates:
                        rows = board.execute(
                            "SELECT uid,payload,created_at FROM task_events WHERE kind='rehomed' "
                            "AND origin_gateway='server' AND json_extract(payload,'$.from')=? "
                            'ORDER BY created_at DESC LIMIT 1', (ref[0],)).fetchall()
                        for uid, raw, at in rows:
                            event = json.loads(raw)
                            proof = event['proof']
                            checked = proof['checked_at']
                            instance = proof['instance_id']
                            if (not isinstance(uid, str) or not uid or event.get('from') != ref[0]
                                    or not event.get('to') or event['to'] == ref[0]
                                    or proof.get('ec2_state') != 'terminated'
                                    or not isinstance(instance, str) or not re.fullmatch(r'i-[0-9a-f]{8,17}', instance)
                                    or type(checked) is not int or not 0 < checked <= at <= now
                                    or at - checked > 300):
                                continue
                            result[ref] = (f'follow-through: origin chat gone @session:{ref[1]}@{ref[0]}/{ref[2]}; '
                                           f'server rehomed {uid}; instance {instance}; terminated checked_at={checked}; '
                                           'fresh registry has no active gateway; fresh session replica has no owner')
        return result
    except Exception:  # health: allow BLE001 -- exception evidence is unknown, never permission
        # Board Resync and HTTPException need not subclass RuntimeError/OSError.
        # Do not leak into the outer plugin hook, whose legacy error policy opens.
        return {}
