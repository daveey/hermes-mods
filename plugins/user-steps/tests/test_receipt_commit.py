"""Real endpoint/store boundaries for pending receipts and commit failures."""
import concurrent.futures
import threading
from contextlib import closing

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_decisions import ROOT, OWNER, load_module, store, request
from async_receipt_support import await_operation


@pytest.mark.parametrize('fail_commit', [False, True])
def test_receipt_waits_for_commit_and_commit_failure_never_hints(tmp_path, monkeypatch, fail_commit):
    api = load_module('receipt_commit_api', ROOT / 'dashboard' / 'plugin_api.py')
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    s = store(tmp_path)
    r = s.create(OWNER, request())
    entered, release, delivered = threading.Event(), threading.Event(), threading.Event()
    real_tx = s._tx
    local = threading.local()
    from contextlib import contextmanager
    @contextmanager
    def held_tx():
        with real_tx() as conn:
            yield conn
            if getattr(local, 'deciding', False):
                entered.set()
                assert release.wait(3)
                if fail_commit:
                    raise OSError('fixture commit rejected')
    # Scope the spy to the decision thread, never background recovery transactions.
    real_decide = s.decide
    def decide(owner, body):
        local.deciding = True
        try:
            return real_decide(owner, body)
        finally:
            local.deciding = False
    monkeypatch.setattr(s, '_tx', held_tx)
    monkeypatch.setattr(s, 'decide', decide)
    class Receiver:
        def deliver(self, op):
            delivered.set()
            return {'id': op['operationId']}
        def readback(self, op, receipt):
            return True
    router = api.create_router(ledger_dir=tmp_path/'ledger', action_store=s,
        owner_resolver=lambda sid: dict(OWNER, storedSessionId=sid), receiver=Receiver())
    # Avoid starting recovery under the transaction spy: establish normal hot mount.
    app = FastAPI(); app.include_router(router)
    with closing(TestClient(app, raise_server_exceptions=False)) as client:
        assert client.get('/ping').status_code == 200
        # Join startup before installing the transaction hold in decide.
        router.stop_delivery()
        # Start again through the next request, then wait for recover completion.
        # Recovery has no pending owners; it must not contact this receiver.
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(client.post, f'/requests/{r["requestId"]}/decisions', json=dict(
                session=OWNER['storedSessionId'], expectedRevision=1,
                choiceId='approve', idempotencyKey='commit-boundary'))
            try:
                assert entered.wait(2)
                assert not future.done()
                assert not delivered.is_set()
            finally:
                release.set()
            response = future.result(timeout=3)
        if fail_commit:
            assert response.status_code == 500
            assert not delivered.is_set()
            assert s.view(OWNER, r['requestId']).get('resolution') is None
            assert not s.undelivered(OWNER['gatewayId'], OWNER['profile'])
        else:
            assert response.status_code == 200
            op = await_operation(s, OWNER, key='commit-boundary')['operation']
            assert op['operationId'] == response.json()['operation']['operationId']
        router.stop_delivery()
