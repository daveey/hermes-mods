"""Hints accelerate fresh work, not existing failures' retry budgets."""
from test_decisions import OWNER, store, request, decision


def test_reconcile_cadence_survives_remount_without_delaying_fresh_work(tmp_path):
    s = store(tmp_path)
    old = s.create(OWNER, request())
    first = s.decide(OWNER, decision(old, key='old'))['operation']
    seen = []
    class Receiver:
        def deliver(self, op):
            seen.append(op['operationId'])
            raise OSError('fixture receiver offline')
    receiver = Receiver()
    s.reconcile(OWNER, receiver, min_retry_interval=15)
    # Construct another store as a remounted router would: cadence is durable.
    s = store(tmp_path)
    fresh = s.create(OWNER, request())
    new = s.decide(OWNER, decision(fresh, key='new'))['operation']
    for _ in range(8):
        s.reconcile(OWNER, receiver, min_retry_interval=15)
    assert seen == [first['operationId'], new['operationId']]
    assert s.operation(OWNER, first['operationId'])['operation']['attempts'] == 1
    # Explicit user Retry retains its preexisting immediate semantics.
    s.dispatch(OWNER, first['operationId'], receiver)
    assert len(seen) == 3


def test_router_delivery_wrapper_serializes_terminal_settlement(tmp_path):
    import concurrent.futures
    import threading
    from test_decisions import ROOT, load_module
    mod = load_module('cadence_mirror', ROOT / 'kanban_mirror.py')
    s = store(tmp_path)
    r = s.create(OWNER, request())
    op = s.decide(OWNER, decision(r, key='exclusive'))['operation']
    entered, release = threading.Event(), threading.Event()
    calls = []
    class Mirror:
        def binding(self, owner, rid):
            return {'request': mod.encoded(op['intent']['request'])}
        def deliver(self, operation):
            calls.append(operation['operationId'])
            entered.set()
            assert release.wait(3)
            return {'id': operation['operationId']}
        def readback(self, operation, receipt):
            return True
    receiver = mod.Delivery(Mirror(), None)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(s.dispatch, OWNER, op['operationId'], receiver)
        try:
            assert entered.wait(2)
            sibling = s.dispatch(OWNER, op['operationId'], receiver)
            assert sibling['operation']['dispatchState'] == 'pending'
            assert len(calls) == 1
        finally:
            release.set()
        assert first.result(3)['operation']['dispatchState'] == 'delivered'
    assert len(calls) == 1
