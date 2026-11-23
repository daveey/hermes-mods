"""HTTP recording must not wait for executor I/O; real store, disposable state."""
import concurrent.futures
import threading
import time

from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_decisions import ROOT, OWNER, load_module, store, request


def test_committed_receipt_returns_while_delivery_is_held(tmp_path, monkeypatch):
    api = load_module('receipt_latency_api', ROOT / 'dashboard' / 'plugin_api.py')
    # Keep all background profile and identity resolution inside the fixture.
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    s = store(tmp_path)
    r = s.create(OWNER, request())
    entered, release = threading.Event(), threading.Event()

    class Receiver:
        def deliver(self, operation):
            entered.set()
            assert release.wait(5), 'fixture delivery was not released'
            return {'id': operation['operationId']}

        def readback(self, operation, receipt):
            return receipt == {'id': operation['operationId']}

    router = api.create_router(
        ledger_dir=tmp_path / 'ledger', state_db=tmp_path / 'state.db',
        action_store=s, owner_resolver=lambda sid: dict(OWNER, storedSessionId=sid),
        receiver=Receiver())
    app = FastAPI()
    app.include_router(router)
    body = dict(session=OWNER['storedSessionId'], expectedRevision=1,
                choiceId='approve', idempotencyKey='held-receiver')
    with TestClient(app) as client, concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        started = time.monotonic()
        future = pool.submit(client.post, f'/requests/{r["requestId"]}/decisions', json=body)
        try:
            assert entered.wait(2), 'delivery never began'
            # At least two seconds of executor I/O remain blocked. Only durable
            # recording, not delivery, belongs on the sub-second receipt path.
            response = future.result(timeout=0.8)
            assert time.monotonic() - started < 1
            assert response.status_code == 200
            result = response.json()
            assert result['operation']['dispatchState'] == 'pending'
            persisted = s.operation(OWNER, key='held-receiver')
            assert persisted['operation']['operationId'] == result['operation']['operationId']
            assert persisted['request']['resolution']['operationId'] == result['operation']['operationId']
        finally:
            release.set()
            future.result(timeout=3)
            stop = getattr(router, 'stop_delivery', None)
            if stop:
                stop()
