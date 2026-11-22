"""Independent reviewer probes for PR347 @18324d11 R1 (not part of the PR)."""
import pytest
from test_remote_card_state_machine import setup, depart, operation, Courier, age, opens, RETIRING
from test_kanban_mirror import decide
from test_withdrawal import OWNER


class Counting(Courier):
    calls = 0

    def read(self, *a):
        type(self).calls += 1
        return super().read(*a)


def back(mirror, row, gen=None):
    r = dict(row, card_answer_generation=gen) if gen else row
    mirror.remote.cards = lambda owner: [r]
    mirror.remote.reconcile(OWNER)


def test_a_b_a_generation_return_terminal_refusal_remints_once_and_is_answerable(tmp_path):
    mirror, store, row, old, op = setup(tmp_path)
    depart(mirror, row, departure=False)                      # A -> B, retiring, held
    assert operation(store, op)['retired']['evidence'] == RETIRING and opens(store) == []
    mirror.remote.courier = Courier('rejected')
    back(mirror, row)                                         # B -> A with authenticated refusal
    done = operation(store, op)
    assert done['dispatchState'] == 'cancelled' and done['retired']['evidence'] != RETIRING
    new = opens(store)
    assert len(new) == 1 and new[0]['requestId'] != old['requestId']
    for _ in range(3):
        back(mirror, row)
    assert [r['requestId'] for r in opens(store)] == [new[0]['requestId']]
    ch = new[0]['choices'][1]                                 # reminted tile takes exactly one new decision
    op2 = store.decide(OWNER, dict(requestId=new[0]['requestId'], expectedRevision=1, choiceId=ch['id'],
                                   revisionText=ch['label'], idempotencyKey='reviewer-second'))['operation']
    assert op2['operationId'] != op['operationId']
    back(mirror, row)
    assert operation(store, op2)['dispatchState'] not in ('cancelled',)
    assert opens(store) == [] or all(r.get('resolution') for r in opens(store))
    assert 'delivery' not in mirror.present(store.get(OWNER, old['requestId']))


def test_same_generation_return_absent_envelope_needs_stable_refusal(tmp_path):
    mirror, store, row, old, op = setup(tmp_path)             # absent, local None
    depart(mirror, row)
    back(mirror, row)                                         # age < 120 s: held
    assert operation(store, op)['retired']['evidence'] == RETIRING and opens(store) == []
    age(store, op, 119)
    back(mirror, row)
    assert operation(store, op)['retired']['evidence'] == RETIRING and opens(store) == []
    age(store, op, 121)
    back(mirror, row)
    assert operation(store, op)['retired']['evidence'] == 'Courier envelope absent with stable proof refusal'
    assert len(opens(store)) == 1


@pytest.mark.parametrize('local', ['sent', 'queued', 'accepted', 'local'])
def test_same_generation_return_in_flight_envelope_never_remints(tmp_path, local):
    mirror, store, row, old, op = setup(tmp_path, local=local)
    depart(mirror, row)
    age(store, op, 100000)
    for _ in range(3):
        back(mirror, row)
    assert operation(store, op)['retired']['evidence'] == RETIRING and opens(store) == []
    shown = mirror.present(store.get(OWNER, old['requestId']))
    assert shown['delivery']['unconfirmed'] is True


def test_unknown_row_is_not_observed_and_keeps_retiring(tmp_path):
    mirror, store, row, old, op = setup(tmp_path)
    depart(mirror, row)
    Counting.calls = 0
    mirror.remote.courier = Counting('rejected')
    mirror.remote.cards = lambda owner: [dict(row, stale=True)]
    mirror.remote.reconcile(OWNER)
    assert Counting.calls == 0
    assert operation(store, op)['retired']['evidence'] == RETIRING and opens(store) == []


def test_unreadable_observation_on_same_generation_return_holds(tmp_path):
    mirror, store, row, old, op = setup(tmp_path)
    depart(mirror, row)

    class Down(Courier):
        def read(self, *a):
            raise OSError('offline')
    mirror.remote.courier = Down()
    age(store, op, 100000)
    back(mirror, row)
    assert operation(store, op)['retired']['evidence'] == RETIRING and opens(store) == []


def test_late_done_on_same_generation_return_never_leaves_second_tile(tmp_path):
    mirror, store, row, old, op = setup(tmp_path)
    depart(mirror, row)
    mirror.remote.courier = Courier('done')
    for _ in range(3):
        back(mirror, row)
    assert operation(store, op)['dispatchState'] == 'delivered'
    assert opens(store) == []
    assert 'delivery' not in mirror.present(store.get(OWNER, old['requestId']))


def test_non_retiring_same_generation_control_reads_nothing(tmp_path):
    mirror, store, row, old, op = setup(tmp_path)
    Counting.calls = 0
    mirror.remote.courier = Counting('rejected')
    back(mirror, row)                                         # never departed: pending op is not observed here
    assert Counting.calls == 0
    assert operation(store, op)['dispatchState'] not in ('cancelled', 'delivered')
