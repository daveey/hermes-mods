"""Canonical ask + answer obligations in one read-only action-store snapshot.

Recording a choice transfers responsibility to the agent; it does not finish the
chat. No reconciliation, dispatch, store creation or authority is acquired here.
"""
import json


def collect(us, session_id, legacy):
    if not (us.home() / 'user-steps' / 'actions.sqlite3').is_file():
        return legacy, []
    store = us.ActionStore(read_only=True)
    owner = us.server_owner(session_id)
    sessions = sorted(set(us.lineage(session_id) or [session_id]) | {session_id})
    requests, operations = [], []
    # ActionStore._read pins both inventories before dropping any open ask:
    # a click between reads must never mint done.
    with store._read() as conn:
        for sid in sessions:
            own = dict(owner, storedSessionId=sid)
            namespace = store._ns(own)
            ids = conn.execute('SELECT id FROM requests WHERE namespace=? AND session=? ORDER BY rowid',
                               (namespace, sid)).fetchall()
            requests.extend(store._get(conn, own, row['id']) for row in ids)
            operations.extend(json.loads(row['body']) for row in conn.execute(
                'SELECT body FROM operations WHERE namespace=? AND session=? ORDER BY rowid', (namespace, sid)))
    mirrored = {a['id'] for r in requests for a in r['aliases'] if a['type'] == 'user_step'
                and a['gatewayId'] == owner['gatewayId'] and a['profile'] == owner['profile']}
    opened = [s for s in legacy if s['id'] not in mirrored and s['id'] not in {r['requestId'] for r in requests}]
    for r in requests:
        if r['state'] not in ('open', 'needs_context') or r.get('resolution'):
            continue
        aliases = [a['id'] for a in r['aliases'] if a['type'] == 'user_step'
                   and a['gatewayId'] == owner['gatewayId'] and a['profile'] == owner['profile']]
        opened.append({'id': aliases[0] if aliases else r['requestId'], 'text': r['title'],
                       'request_ref': {'request_id': r['requestId'], **r['owner']},
                       'choices': [{'label': c['label'], 'value': c['id']} for c in r['choices']]})
    pending = []
    for op in operations:
        if op['owner'] != dict(owner, storedSessionId=op['owner']['storedSessionId']):
            raise ValueError('Decision operation owner disagrees with stored namespace')
        if op['owner']['storedSessionId'] not in sessions:
            raise ValueError('Decision operation session disagrees with stored scope')
        if op['dispatchState'] not in ('delivered', 'cancelled'):
            pending.append({'kind': 'goal', 'id': 'decision-answer:' + op['operationId'],
                            'label': 'Answer pending on agent: ' + op['intent']['request']['title'],
                            'state': 'pending'})
    return opened, pending
