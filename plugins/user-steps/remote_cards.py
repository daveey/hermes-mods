"""Exact-linked remote asks; immutable source binding and authenticated courier receipts.

No board replica is writable here. The owner revalidates the frozen generation
and independently retrieves the recorded source operation before any transition.
"""
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sys


def load_file(path):
    key = '_user_steps_remote_' + hashlib.sha256(str(path).encode()).hexdigest() + str(path.stat().st_mtime_ns)
    if key not in sys.modules:
        spec = importlib.util.spec_from_file_location(key, path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        sys.modules.setdefault(key, mod)
    return sys.modules[key]


def linked_cards(owner):
    """Same core membership reader as the panel, then exact origin/chat filtering.

    Replica/link rows may omit the full ask or portable owner generation. Hydrate
    only those qualified targets from the courier's published owner records.
    """
    # Missing core is unavailable, never evidence that a prior remote ask departed.
    from hermes_cli.kanban_session_activity import _remote_cards
    cards = _remote_cards([owner['storedSessionId']], ('blocked', 'triage'), False)
    selected = {}
    for card in cards:
        if (card.get('session') != owner['storedSessionId']
                and card.get('origin_session') != owner['storedSessionId']):
            continue
        if (card.get('origin_gateway', owner['gatewayId']) != owner['gatewayId']
                or card.get('origin_profile', owner['profile']) != owner['profile']
                or card.get('gateway') == owner['gatewayId']):
            continue
        target = tuple(card.get(k) for k in ('gateway', 'profile', 'board', 'id'))
        if all(isinstance(x, str) and x for x in target):
            selected[target] = card
    if not selected:
        return []
    view_module = load_file(Path(__file__).resolve().parents[1] / 'hub-sync' / 'hubview.py')
    view = view_module.open_view()
    if view is None:
        raise OSError('Remote ask owner projection unavailable')
    try:
        rows = view.rows('tasks', pick=lambda candidates: [c for c in candidates
                         if (c[0], *c[1].split('/')) in selected])
        return [dict(r, session=owner['storedSessionId']) for r in rows]
    finally:
        view.conn.close()


class Courier:
    """Durably enqueue once per operation; retries only read the same request.

    The normal courier sync owns transport/recovery. Never turn a queued envelope
    into a successful user-steps receipt. Read back the authenticated owner record.
    """
    def __init__(self):
        self.view = load_file(Path(__file__).resolve().parents[1] / 'hub-sync' / 'hubview.py')

    def exchange(self, envelope, rid):
        client = self.view.load_client()
        if client is None:
            raise OSError('Authenticated card-answer courier unavailable')
        db = client.open_db(self.view.db_path())
        try:
            if client.mget(db, 'gateway') != envelope['args']['source']['gateway']:
                raise OSError('Card-answer source gateway mismatch')
            client.queue_request(db, envelope['to_gw'], 'tasks', envelope['key'], 'card-answer',
                                 envelope['args'], rid=rid)
            db.commit()
        finally:
            db.close()
        self.view._send_now(client, self.view.db_path(), self.view.SEND_WAIT_S)
        cfg = client.load_config()
        api = client.Api(cfg['hub_url'], client.read_key())
        status, data = api.call('GET', '/v2/sync/requests/' + rid)
        if status != 200 or not isinstance(data, dict):
            raise OSError('Card-answer receipt unavailable; retry the same operation')
        return data.get('request')


class Remote:
    def __init__(self, mirror, *, cards=linked_cards, courier=None):
        self.mirror, self.cards = mirror, cards
        self.courier = courier

    def snapshots(self, owner):
        for row in self.cards(owner):
            hr, generation = row.get('human_request'), row.get('card_answer_generation')
            if (row.get('stale') or row.get('status') not in ('blocked', 'triage')
                    or not isinstance(hr, dict) or not hr.get('action')
                    or hr.get('interaction') not in ('reply', 'approval', 'user_terminal', 'physical', 'external')
                    or not isinstance(generation, str) or not re.fullmatch('[0-9a-f]{64}', generation)):
                continue
            target = dict(zip(('ownerGateway', 'ownerProfile', 'board', 'task'),
                              (row.get(k) for k in ('gateway', 'profile', 'board', 'id'))))
            if (not all(isinstance(x, str) and re.fullmatch('[A-Za-z0-9_.-]+', x) for x in target.values())
                    or target['ownerGateway'] == owner['gatewayId']):
                continue
            target['generation'] = generation
            options = hr.get('choices') or [dict(label='Unblock', value='unblock'), dict(label='Archive', value='archive')]
            yield dict(task=target['task'], session=owner['storedSessionId'], generation=generation,
                       ask=hr, title=row.get('title') or hr['action'],
                       remote=dict(version=1, target=target,
                                   choiceValues={f'card-{i}': ch['value'] for i, ch in enumerate(options)}))

    def reconcile(self, owner):
        m = self.mirror
        try:
            snapshots = list(self.snapshots(owner))  # failure is not departure evidence
        except ImportError:
            return  # core-free installation: no remote decision authority or withdrawal
        live = set()
        for snap in snapshots:
            board = snap['remote']['target']['board']
            request = m._current_request(owner, board, snap)
            if request is not None:
                m._publish(owner, board, snap, request)
                live.add(request['requestId'])
        with m.store._read() as c:
            bindings = [dict(r) for r in c.execute(
                "SELECT * FROM kanban_projections WHERE namespace=? AND session=? AND json_type(snapshot,'$.remote')='object'",
                (m.store._ns(owner), owner['storedSessionId']))]
        for binding in bindings:
            if json.loads(binding['snapshot']).get('remote') and binding['request_id'] not in live:
                request = m.store.get(owner, binding['request_id'])
                if request['state'] in ('open', 'needs_context') and not request.get('resolution'):
                    m._withdraw(owner, request)

    def envelope(self, op, binding):
        snap = json.loads(binding['snapshot'])
        remote = snap['remote']
        choice = remote['choiceValues'].get(op['decision']['choiceId'])
        other = op['decision']['choiceId'] == 'answer-text'
        if other:
            if (op.get('provenance') != dict(type='kanban_other', version=1)
                    or op['intent'].get('executionAuthorized') is not False
                    or op['intent'].get('permissionGrants') != []):
                raise self.mirror.core.ActionError('Bound informational answer required', 409)
            choice = op['intent']['revisionText']
        if choice is None:
            raise self.mirror.core.ActionError('Unknown remote card choice', 409)
        answer = dict(remote['target'], choice=choice, operationId=op['operationId'])
        if other:
            answer['mode'] = 'comment'
        owner = op['owner']
        return dict(to_gw=answer['ownerGateway'], coll='tasks',
                    key='/'.join(answer[k] for k in ('ownerProfile', 'board', 'task')), action='card-answer',
                    args=dict(answer=answer, source=dict(gateway=owner['gatewayId'], profile=owner['profile'],
                                                       session=owner['storedSessionId'])))

    def deliver(self, op, binding):
        envelope = self.envelope(op, binding)
        rid = 'hr-card-answer-' + hashlib.sha256(json.dumps([op['owner'], op['operationId']], sort_keys=True).encode()).hexdigest()
        record = (self.courier or Courier()).exchange(envelope, rid)
        if (not isinstance(record, dict) or record.get('id') != rid
                or any(record.get(k) != v for k, v in envelope.items())
                or record.get('from') != 'gw:' + op['owner']['gatewayId']):
            raise OSError('Card-answer courier envelope mismatch')
        if record.get('state') == 'rejected':
            raise self.mirror.core.ActionError('Remote owner refused the exact card answer', 409)
        if record.get('state') != 'done':
            raise OSError('Remote answer pending; check status or retry this operation')
        receipt = record.get('result')
        answer = envelope['args']['answer']
        if (not isinstance(receipt, dict) or any(receipt.get(k) != v for k, v in answer.items())
                or receipt.get('originGateway') != op['owner']['gatewayId']
                or receipt.get('destination') != 'kanban_ask' or receipt.get('executionStarted') is not False
                or receipt.get('decisionProvenance') != 'recorded_desktop_operation'):
            raise OSError('Invalid remote answer receipt')
        return receipt
