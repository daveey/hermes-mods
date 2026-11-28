"""Canonical accepted decision -> committed unblock, identifier-only projection.

Do not join by task status or nearest timestamps. An operation must match the
server-owned projection and the unblock's operation receipt. Read JSON leaves
in SQLite: prompts, answers, frozen intents and credentials never leave sources.
"""
from contextlib import closing
from datetime import datetime
import math

from .collectors import ro_connect, sample
from .spans import validate_span


def definitions_for(definitions):
    return [d for d in definitions if d.get('kind') == 'span'
            and d.get('source') == 'spans'
            and (d.get('start'), d.get('end')) == ('decision.submit', 'unblocked')]


def collect(actions, board_path, board, store, definitions, now,
            host='local', gateway='local', profile=None):
    """Revisit recent receipts: action and board replicas may arrive out of order.

    The activation floor is durable and per definition/board. Never backfill a
    watch with decisions predating instrumentation. Store identities make restarts
    and repeated source scans replay-safe. No writes are made to either source.
    """
    if not definitions:
        return 0
    floors = {}
    for d in definitions:
        key = 'decision-spans-v1:' + d['id'] + ':' + str(board_path.resolve())
        floor = store.meta_get(key)
        if floor is None:
            floor = now
            store.meta_set(key, floor)
        floors[d['id']] = max(floor, d.get('measured_since', floor), now - 30*86400)
    if not actions.exists():
        return 0
    with closing(ro_connect(actions)) as a, closing(ro_connect(board_path)) as b:
        tables = {r[0] for r in a.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {'operations', 'kanban_projections'} <= tables:
            return 0
        events = b.execute('''SELECT id,task_id,created_at,
            json_extract(payload,'$.user_steps_projection.operationId') operation
            FROM task_events WHERE kind='unblocked' AND created_at>=? AND created_at<=?
            AND json_valid(payload) ORDER BY id''', (math.floor(min(floors.values())), now))
        spans = []
        for event in events:
            if not isinstance(event['operation'], str):
                continue
            rows = a.execute('''SELECT o.id, o.request,
                json_extract(o.body,'$.recordedAt') submitted,
                json_extract(o.body,'$.owner.gatewayId') gateway,
                json_extract(o.body,'$.owner.profile') profile,
                json_extract(o.body,'$.owner.storedSessionId') session
                FROM operations o JOIN kanban_projections p
                ON p.namespace=o.namespace AND p.request_id=o.request AND p.session=o.session
                WHERE o.id=? AND p.task=? AND p.board=?
                AND COALESCE(json_extract(p.snapshot,'$.remote'),0)=0''',
                (event['operation'], event['task_id'], board)).fetchall()
            if len(rows) != 1:
                continue
            op = rows[0]
            if op['gateway'] != gateway or (profile and op['profile'] != profile):
                continue
            try:
                stamp = datetime.fromisoformat(op['submitted'])
                if stamp.tzinfo is None:
                    continue
                submitted = stamp.timestamp()
                # Board events have integer-second timestamps. Quantize both
                # endpoints equally, rather than invent subsecond precision or
                # drop fast operations whose end second precedes recordedAt.
                start, end = math.floor(submitted), event['created_at']
                if not math.isfinite(end) or end != math.floor(end) or end < start:
                    continue
                for d in definitions:
                    if not floors[d['id']] <= submitted <= now:
                        continue
                    value = sample(d['id'], (gateway, op['profile'], board,
                                   op['request'], op['id']), start, end,
                                   host, gateway, card=event['task_id'],
                                   profile=op['profile'], board=board, phase='seconds_resolution')
                    # Internal durable-source replay is not HTTP admission (it
                    # can exceed one hour). Reuse the same privacy/OTLP contract.
                    spans.append(validate_span(value, now=start))
            except (ValueError, TypeError, OverflowError):
                continue
    return store.ingest(spans)
