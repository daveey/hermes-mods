"""Read-only Gate D evidence from the origin's authenticated courier ledger.

An accepted receipt is not completion or a release. It proves that this exact
origin retained the obligation and can observe the owner's completion. Require
its current published binding too; old, removed or unreadable evidence cannot
substitute for a missing board subscription. Never initialize/refresh a store.
"""
from contextlib import closing
import hashlib
import json
import sqlite3
import time


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False)


def owner_live(source, task, owner, now, ledger):
    """Core's collector-scope contract, including cores before _owner_scope extraction.

    Use its heartbeat window and verified observation decoder, not a second
    interpretation of liveness or a local wall-clock TTL on the receipt itself.
    """
    row = source.execute("SELECT data FROM replica WHERE gw=? AND coll='gateway' AND key='info'",
                         (owner['gateway'],)).fetchone()
    info = json.loads(row[0]) if row else {}
    scopes = info.get('task_scopes')
    if not isinstance(scopes, list) or info.get('gateway') != owner['gateway']:
        return False
    matching = [s for s in scopes if isinstance(s, dict)
                and (s.get('profile'), s.get('board')) == (owner['profile'], owner['board'])]
    if len(matching) != 1 or matching[0].get('state') != 'ok':
        return False
    return (ledger._recent(matching[0].get('observed_at'), now, ledger.scope_window(info))
            or ledger._recent(ledger._owner_evidence(task).get('observed_at'), now))


def reached(context, ro_connect):
    result = set()
    try:
        if not context['ledger'].is_file() or not context['replica'] or not context['replica'].is_file():
            return result
        owner = context['owner']
        if owner['gateway'] == context['gateway']:
            return result  # Local ownership must use the primary board, not its own replica.
        key = '/'.join(owner[k] for k in ('profile', 'board', 'task_id'))
        now = time.time()
        with closing(ro_connect(context['replica'])) as source, closing(ro_connect(context['ledger'])) as ledger:
            source.execute('BEGIN')
            ledger.execute('BEGIN')
            meta = dict(source.execute('SELECT k,v FROM meta'))
            if meta.get('offline_since') or not 0 <= now - float(meta.get('last_ok') or 0) <= 180:
                return result
            row = source.execute("SELECT version,data FROM replica WHERE gw=? AND coll='tasks' AND key=?",
                                 (owner['gateway'], key)).fetchone()
            if not row:
                return result
            version, raw_task = row
            task = json.loads(raw_task)
            if (any(task.get(k) != owner[k] for k in ('gateway', 'profile', 'board'))
                    or task.get('id') != owner['task_id']
                    or not context['owner_live'](source, task, owner, now)):
                return result
            for stored_key, digest, raw, resolved, payload, observed, released in ledger.execute(
                    'SELECT binding_id,binding_hash,binding_json,resolved_session,task_json,'
                    'observation_version,released FROM origin_obligations WHERE released=0'):
                try:
                    binding = json.loads(raw)
                    if (binding.get('owner') != owner or binding.get('task_record_key') != key
                            or set(binding) != {'binding_id', 'version', 'publication_version', 'owner', 'origin', 'task_record_key'}
                            or type(binding['version']) is not int or binding['version'] < 1
                            or type(binding['publication_version']) is not int or binding['publication_version'] < 1
                            or not isinstance(binding['binding_id'], str) or not binding['binding_id']
                            or stored_key != _json([owner, binding['binding_id']])
                            or digest != hashlib.sha256(_json(binding).encode()).hexdigest()
                            or observed != version or json.loads(payload) != task):
                        continue
                    origin = binding['origin']
                    if (set(origin) != {'gateway', 'profile', 'session'}
                            or (origin['gateway'], origin['profile']) != (context['gateway'], context['profile'])
                            or not isinstance(origin['session'], str) or not origin['session']
                            or not isinstance(resolved, str) or not resolved):
                        continue
                    marker = {k: v for k, v in binding.items() if k != 'publication_version'}
                    if marker not in task.get('origin_bindings', []):
                        continue
                    for request, stored_hash, receipt in ledger.execute(
                            'SELECT request_id,binding_hash,receipt_json FROM origin_receipts WHERE binding_hash=?', (digest,)):
                        ack = json.loads(receipt)
                        if (ack.get('status') == 'accepted' and ack.get('request_id') == request
                                and isinstance(request, str) and request
                                and ack.get('binding_hash') == stored_hash == digest
                                and ack.get('binding_id') == binding['binding_id']
                                and ack.get('owner') == owner and ack.get('origin') == origin
                                and ack.get('resolved_session') == resolved
                                and type(ack.get('publication_version')) is int
                                and binding['publication_version'] <= ack['publication_version'] <= version):
                            result.add((origin['gateway'], origin['profile'], origin['session']))
                except (ValueError, TypeError, KeyError, AttributeError):
                    continue
    except (OSError, sqlite3.Error, ValueError, TypeError, KeyError, AttributeError):
        return set()
    return result
