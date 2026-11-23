import concurrent.futures
import threading
import pytest
from test_decisions import OWNER, store, request, decision


@pytest.mark.parametrize('limits', [dict(min_retry_interval=15), dict(max_attempts=1)])
def test_manual_retry_during_scan_cannot_be_retried_again_inside_cadence(tmp_path, limits):
    s = store(tmp_path)
    a = s.decide(OWNER, decision(s.create(OWNER, request()), key='slow'))['operation']
    b = s.decide(OWNER, decision(s.create(OWNER, request()), key='later'))['operation']
    entered, release = threading.Event(), threading.Event()
    calls = []
    class Receiver:
        exclusive_dispatch = True
        def deliver(self, op):
            calls.append(op['operationId'])
            if op['operationId'] == a['operationId']:
                entered.set()
                assert release.wait(5)
            raise OSError('fixture offline')
    receiver = Receiver()
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(s.reconcile, OWNER, receiver, **limits)
        try:
            assert entered.wait(2)
            s.dispatch(OWNER, b['operationId'], receiver)
        finally:
            release.set()
        future.result(3)
    assert calls.count(b['operationId']) == 1, calls
