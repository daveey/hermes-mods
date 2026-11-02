"""A recorded decision that keeps failing delivery opens one agent-owned Kanban card instead of retrying silently
forever, and a reviewer can retire it with a reason (t_01a199c0a2e3ea8b: five approvals sat undelivered for days,
up to 369 attempts, with only a WARNING log nobody read)."""
import importlib.util
import json
from pathlib import Path

import pytest

from test_decisions import OWNER, decision, request

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location('udw_' + name, ROOT / (name + '.py'))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


sw = load('stale_watch')
core = sw._load('core')
NOW = 1_800_000_000.0


class Failing:
    def deliver(self, op): raise RuntimeError('Card binding CAS conflict')
    def readback(self, op, receipt): return False


class Closed:
    def deliver(self, op): raise RuntimeError('exact session has no unique live owner on this runtime')
    def readback(self, op, receipt): return False


class Board:
    def __init__(self): self.cards = {}
    def __call__(self, c): self.cards.setdefault(c['idempotency_key'], c)


def recorded(tmp_path, *, key='k1'):
    s = core.ActionStore(tmp_path / 'actions.sqlite3')
    r = s.create(OWNER, request())
    return s, s.decide(OWNER, decision(r, key=key))['operation']


def age(s, oid, seconds):
    from datetime import datetime, timezone
    with s._tx() as c:
        op = json.loads(c.execute('SELECT body FROM operations WHERE id=?', (oid,)).fetchone()[0])
        op['recordedAt'] = datetime.fromtimestamp(NOW - seconds, timezone.utc).isoformat()
        c.execute('UPDATE operations SET body=? WHERE id=?', (json.dumps(op), oid))


def test_repeated_real_failures_open_one_card_with_the_error(tmp_path):
    s, op = recorded(tmp_path)
    age(s, op['operationId'], 3600)
    for _ in range(sw.UNDELIVERED_MIN_ATTEMPTS - 1):
        s.dispatch(OWNER, op['operationId'], Failing())
    board = Board()
    assert sw.undelivered_decisions(core, s, now=NOW) == []  # one short of the threshold, < 24 h old
    s.dispatch(OWNER, op['operationId'], Failing())
    found = sw.undelivered_decisions(core, s, now=NOW)
    assert [f['operationId'] for f in found] == [op['operationId']]
    opened = sw.run(core, load('chat'), s, db=tmp_path / 'missing-state.db', now=NOW, create=board)
    opened += sw.run(core, load('chat'), s, db=tmp_path / 'missing-state.db', now=NOW, create=board)
    assert opened.count('undelivered:' + op['operationId']) == 2  # rerun returns the same key
    assert list(board.cards) == ['user-steps-undelivered:' + op['operationId']]
    c = board.cards['user-steps-undelivered:' + op['operationId']]
    assert 'Card binding CAS conflict' in c['body'] and '--retire-operation ' + op['operationId'] in c['body']
    assert c['assignee'] == 'default'


def test_a_closed_chat_wait_opens_a_card_only_after_a_day(tmp_path):
    s, op = recorded(tmp_path)
    for _ in range(50):
        s.dispatch(OWNER, op['operationId'], Closed())
    age(s, op['operationId'], sw.UNDELIVERED_MAX_WAIT - 60)
    assert sw.undelivered_decisions(core, s, now=NOW) == []  # waits never count as attempts
    age(s, op['operationId'], sw.UNDELIVERED_MAX_WAIT + 60)
    [found] = sw.undelivered_decisions(core, s, now=NOW)
    assert found['ownerWaits'] == 50 and found['attempts'] == 0


def test_delivered_and_fresh_decisions_are_not_flagged(tmp_path):
    s, op = recorded(tmp_path)
    age(s, op['operationId'], 60)
    assert sw.undelivered_decisions(core, s, now=NOW) == []  # fresh, never dispatched
    age(s, op['operationId'], 10 * 86400)
    assert len(sw.undelivered_decisions(core, s, now=NOW)) == 1  # recorded, never delivered, 10 days old

    class Ok:
        def deliver(self, op): return {'ok': True}
        def readback(self, op, receipt): return True
    assert s.dispatch(OWNER, op['operationId'], Ok())['operation']['dispatchState'] == 'delivered'
    assert sw.undelivered_decisions(core, s, now=NOW) == []


def test_retire_operation_settles_it_with_a_reason_and_stops_retries(tmp_path):
    s, op = recorded(tmp_path)
    s.dispatch(OWNER, op['operationId'], Failing())
    got = sw.retire_operation(core, s, op['operationId'], reason='chat deleted', evidence='state.db has no row',
                              local_gateway=OWNER['gatewayId'])
    assert got['operation']['dispatchState'] == 'cancelled'
    assert got['operation']['retired']['reason'] == 'chat deleted'
    assert got['request']['state'] == 'cancelled'
    assert s.pending_owners() == [] and s.reconcile(OWNER, Failing()) == []
    age(s, op['operationId'], 10 * 86400)
    assert sw.undelivered_decisions(core, s, now=NOW) == []
    with pytest.raises(core.ActionError, match='already settled'):
        sw.retire_operation(core, s, op['operationId'], reason='again', evidence='x',
                            local_gateway=OWNER['gatewayId'])


def test_retire_operation_needs_reason_evidence_and_the_local_gateway(tmp_path):
    s, op = recorded(tmp_path)
    with pytest.raises(core.ActionError, match='Reason'):
        sw.retire_operation(core, s, op['operationId'], reason=' ', evidence='e', local_gateway=OWNER['gatewayId'])
    with pytest.raises(core.ActionError, match='Evidence'):
        sw.retire_operation(core, s, op['operationId'], reason='r', evidence='', local_gateway=OWNER['gatewayId'])
    with pytest.raises(core.ActionError, match='not on this gateway'):
        sw.retire_operation(core, s, op['operationId'], reason='r', evidence='e', local_gateway='other')
    assert s.operation(OWNER, oid=op['operationId'])['operation']['dispatchState'] == 'pending'
