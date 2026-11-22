"""Retirement is a read-only authenticated observation, never a new send."""
import copy
import json
import subprocess
import sys
from types import SimpleNamespace

import pytest
from test_remote_card_courier import remote_row
from test_kanban_mirror import fixture, decide
from test_withdrawal import OWNER, core


@pytest.mark.parametrize('state', ['absent', 'done', 'rejected', 'expired', 'failed', 'pending', 'bad', 'offline'])
@pytest.mark.parametrize('departure', [False, True])
def test_retirement_respects_exact_owner_receipt(tmp_path, state, departure):
    mirror, store, _, hr = fixture(tmp_path)
    row = remote_row(hr)
    mirror.remote.cards = lambda owner: [row]
    mirror.remote.reconcile(OWNER)
    old = store.requests(OWNER)[0]
    op = decide(store, old)
    reads = []

    def read(envelope, rid):
        reads.append(rid)
        if state == 'offline':
            raise OSError('offline')
        if state == 'absent':
            return None
        receipt = dict(copy.deepcopy(envelope['args']['answer']), originGateway='fixture-sync',
            destination='kanban_ask', executionStarted=False, decisionProvenance='recorded_desktop_operation')
        if state == 'bad':
            receipt['operationId'] = 'another-operation'
        return dict(copy.deepcopy(envelope), id=rid, state='done' if state == 'bad' else state,
                    result=receipt, **{'from': 'gw:fixture-sync'})

    mirror.remote.courier = SimpleNamespace(read=read)  # deliberately no exchange/send method
    mirror.remote.cards = lambda owner: [] if departure else [dict(row, card_answer_generation='b' * 64)]
    mirror.remote.reconcile(OWNER)
    result = store.operation(OWNER, op['operationId'])['operation']
    expected = ('delivered' if state == 'done' else 'cancelled'
                if state in ('absent', 'rejected', 'expired', 'failed', 'pending') else 'pending')
    retiring = state in ('absent', 'pending')  # no terminal fact: proof refusal on, tile held, no remint
    assert result['dispatchState'] == expected
    assert reads
    if state == 'done':
        assert result['receipt']['operationId'] == op['operationId']
        assert len(reads) == 2  # fresh independent readback before settlement
    opens = [r for r in store.requests(OWNER) if r['state'] == 'open']
    if retiring:
        assert result['retired']['evidence'] == 'remote-retiring'
        assert opens == []
    elif expected == 'pending':
        assert [r['requestId'] for r in opens] == [old['requestId']]
    else:
        assert len(opens) == (0 if departure else 1)


def test_operation_lease_cross_process_and_exception_release(tmp_path):
    mirror, store, _, hr = fixture(tmp_path)
    mirror.remote.cards = lambda owner: [remote_row(hr)]
    mirror.remote.reconcile(OWNER)
    op = decide(store, store.requests(OWNER)[0])
    key = __import__('hashlib').sha256(core._json([OWNER, op['operationId']]).encode()).hexdigest()
    script = tmp_path / 'observe_lease.py'
    script.write_text('import fcntl,sys\n'
                      'with open(sys.argv[1], "a+") as lock:\n'
                      ' try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)\n'
                      ' except BlockingIOError: sys.exit(3)\n')
    lock = store.path.parent / 'delivery-leases' / key
    with pytest.raises(RuntimeError):
        with store.delivery_lease(OWNER, op['operationId']) as held:
            assert held
            child = subprocess.run([sys.executable, str(script), str(lock)], timeout=10)
            assert child.returncode == 3
            with store.delivery_lease(OWNER, op['operationId']) as second:
                assert not second
            raise RuntimeError('receiver crashed')
    assert subprocess.run([sys.executable, str(script), str(lock)], timeout=10).returncode == 0


def test_inflight_dispatch_defers_even_receipt_observation_and_direct_retirement(tmp_path):
    mirror, store, _, hr = fixture(tmp_path)
    mirror.remote.cards = lambda owner: [remote_row(hr)]
    mirror.remote.reconcile(OWNER)
    row = store.requests(OWNER)[0]
    op = decide(store, row)
    def forbidden(*args):
        raise AssertionError('Do not observe or retire until the active dispatch settles')
    mirror.remote.courier = SimpleNamespace(read=forbidden)
    mirror.remote.cards = lambda owner: []
    with store.delivery_lease(OWNER, op['operationId'], dispatch=True) as held:
        assert held
        mirror.remote.reconcile(OWNER)
        with pytest.raises(core.ActionError, match='Delivery in flight'):
            store.retire_operation(OWNER, op['operationId'], by='system', reason='departed', evidence='fixture')
    assert store.operation(OWNER, op['operationId'])['operation']['dispatchState'] == 'pending'
    assert store.get(OWNER, row['requestId'])['state'] == 'open'


def test_presentation_contains_preexisting_malformed_binding(tmp_path):
    mirror, store, _, hr = fixture(tmp_path)
    mirror.remote.cards = lambda owner: [remote_row(hr)]
    mirror.remote.reconcile(OWNER)
    row = store.requests(OWNER)[0]
    binding = mirror.binding(OWNER, row['requestId'])
    snap = json.loads(binding['snapshot'])
    snap['ask']['action'] = {'invalid': 'legacy'}
    with store._tx() as c:
        c.execute('DELETE FROM kanban_projection_display')
        c.execute('UPDATE kanban_projections SET snapshot=?', (json.dumps(snap),))
    assert mirror.present(row) == row
