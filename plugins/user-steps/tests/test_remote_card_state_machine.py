"""docs/remote-card-answer-states.md, one test per transition."""
import copy
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from test_remote_card_courier import remote_row
from test_kanban_mirror import fixture, decide
from test_withdrawal import OWNER

RETIRING = 'remote-retiring'


def receipt_record(envelope, rid, state='done'):
    receipt = dict(copy.deepcopy(envelope['args']['answer']), originGateway='fixture-sync',
                   destination='kanban_ask', executionStarted=False,
                   decisionProvenance='recorded_desktop_operation')
    return dict(copy.deepcopy(envelope), id=rid, state=state, result=receipt, **{'from': 'gw:fixture-sync'})


class Courier:
    """mode: absent | done | in-flight | rejected | expired | failed ; local: courier journal state."""

    def __init__(self, mode='absent', local=None):
        self.mode, self.local = mode, local

    def read(self, envelope, rid):
        if self.mode == 'absent':
            return None
        return receipt_record(envelope, rid, 'queued' if self.mode == 'in-flight' else self.mode)

    def local_state(self, rid):
        return self.local


def setup(tmp_path, mode='absent', local=None):
    mirror, store, _, hr = fixture(tmp_path)
    row = remote_row(hr)
    mirror.remote.cards = lambda owner: [row]
    mirror.remote.reconcile(OWNER)
    old = store.requests(OWNER)[0]
    op = decide(store, old)
    mirror.remote.courier = Courier(mode, local)
    return mirror, store, row, old, op


def depart(mirror, row, departure=True):
    mirror.remote.cards = lambda owner: [] if departure else [dict(row, card_answer_generation='b' * 64)]
    mirror.remote.reconcile(OWNER)


def operation(store, op):
    return store.operation(OWNER, op['operationId'])['operation']


def age(store, op, seconds):
    with store._tx() as c:
        body = json.loads(c.execute('SELECT body FROM operations WHERE id=?', (op['operationId'],)).fetchone()[0])
        body['retired']['at'] = (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat()
        c.execute('UPDATE operations SET body=? WHERE id=?', (json.dumps(body), op['operationId']))


def opens(store):
    return [r for r in store.requests(OWNER) if r['state'] == 'open']


@pytest.mark.parametrize('mode,local', [('absent', None), ('absent', 'sent'), ('in-flight', 'sent')])
def test_pending_to_retiring_holds_target_without_remint(tmp_path, mode, local):
    mirror, store, row, old, op = setup(tmp_path, mode, local)
    depart(mirror, row, departure=False)
    done = operation(store, op)
    assert done['dispatchState'] == 'cancelled' and done['retired']['evidence'] == RETIRING
    assert opens(store) == []  # target held: no replacement tile
    shown = mirror.present(store.get(OWNER, old['requestId']))
    assert shown['delivery']['text'] == 'Delivering…' and shown['delivery']['unconfirmed'] is False


def test_retiring_refuses_new_proofs(tmp_path):
    """The cancelled op is what the source proof reader checks (dispatchState != cancelled)."""
    mirror, store, row, old, op = setup(tmp_path)
    depart(mirror, row)
    assert operation(store, op)['dispatchState'] == 'cancelled'
    assert store.undelivered(OWNER['gatewayId'], OWNER['profile']) == []  # nothing retries it either


def test_retiring_to_delivered_on_late_receipt(tmp_path):
    mirror, store, row, old, op = setup(tmp_path)
    depart(mirror, row, departure=False)
    mirror.remote.courier = Courier('done')
    depart(mirror, row, departure=False)
    done = operation(store, op)
    assert done['dispatchState'] == 'delivered' and 'retired' not in done
    assert done['receipt']['operationId'] == op['operationId']
    assert store.get(OWNER, old['requestId'])['state'] == 'resolved'
    shown = mirror.present(store.get(OWNER, old['requestId']))
    assert 'delivery' not in shown


def test_late_receipt_needs_independent_readback(tmp_path):
    mirror, store, row, old, op = setup(tmp_path)
    depart(mirror, row)
    reads = []

    class Flaky(Courier):
        def read(self, envelope, rid):
            reads.append(1)
            if len(reads) == 1:
                return receipt_record(envelope, rid)
            return None

    mirror.remote.courier = Flaky('done')
    depart(mirror, row)
    assert operation(store, op)['dispatchState'] == 'cancelled' and len(reads) == 2


@pytest.mark.parametrize('state', ['rejected', 'expired', 'failed'])
def test_retiring_to_cancelled_on_terminal_refusal_remints(tmp_path, state):
    mirror, store, row, old, op = setup(tmp_path)
    depart(mirror, row, departure=False)
    assert opens(store) == []
    mirror.remote.courier = Courier(state)
    depart(mirror, row, departure=False)
    done = operation(store, op)
    assert done['dispatchState'] == 'cancelled' and done['retired']['evidence'] != RETIRING
    assert len(opens(store)) == 1 and opens(store)[0]['requestId'] != old['requestId']


@pytest.mark.parametrize('local', [None, 'rejected'])
def test_retiring_absent_with_stable_proof_refusal_and_envelope_gone_remints(tmp_path, local):
    mirror, store, row, old, op = setup(tmp_path, 'absent', local)
    depart(mirror, row, departure=False)
    depart(mirror, row, departure=False)       # not yet stable
    assert operation(store, op)['retired']['evidence'] == RETIRING and opens(store) == []
    age(store, op, 600)
    depart(mirror, row, departure=False)
    assert operation(store, op)['retired']['evidence'] != RETIRING
    assert len(opens(store)) == 1


@pytest.mark.parametrize('local', ['local', 'sent', 'queued', 'accepted'])
def test_retiring_with_envelope_still_possible_never_remints_however_old(tmp_path, local):
    mirror, store, row, old, op = setup(tmp_path, 'absent', local)
    depart(mirror, row, departure=False)
    age(store, op, 10 ** 6)
    depart(mirror, row, departure=False)
    assert operation(store, op)['retired']['evidence'] == RETIRING and opens(store) == []


def test_unconfirmed_tile_after_retry_window_adds_no_action_and_no_remint(tmp_path):
    mirror, store, row, old, op = setup(tmp_path, 'absent', 'sent')
    depart(mirror, row, departure=False)
    age(store, op, 2 * 3600)
    depart(mirror, row, departure=False)
    request = store.get(OWNER, old['requestId'])
    shown = mirror.present(request)
    assert shown['delivery']['unconfirmed'] is True
    assert shown['delivery']['text'].startswith('Delivery to remote not confirmed (')
    assert opens(store) == [] and request['state'] == 'cancelled'
    assert 'Cancelled' not in shown['delivery']['text']


def test_unverifiable_observation_never_enters_retiring(tmp_path):
    mirror, store, row, old, op = setup(tmp_path)

    def offline(*a):
        raise OSError('offline')

    mirror.remote.courier = SimpleNamespace(read=offline)
    depart(mirror, row)
    assert operation(store, op)['dispatchState'] == 'pending'


def test_late_success_after_terminal_cancellation_is_not_reopened(tmp_path):
    """Once terminal (authenticated refusal) the remint stands; a delivered op is never touched again."""
    mirror, store, row, old, op = setup(tmp_path, 'rejected')
    depart(mirror, row, departure=False)
    first = operation(store, op)
    mirror.remote.courier = Courier('done')
    depart(mirror, row, departure=False)
    assert operation(store, op) == first
