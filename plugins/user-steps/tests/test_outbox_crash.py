"""Process death must preserve exact operation custody and receiver deduplication."""
import os
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_decisions import ROOT, OWNER, load_module, store, request, decision
from async_receipt_support import await_operation


class DurableReceiver:
    def __init__(self, path, crash=False):
        self.path, self.crash = path, crash
    def deliver(self, op):
        with closing(sqlite3.connect(self.path)) as c, c:
            c.execute('CREATE TABLE IF NOT EXISTS effects (id TEXT PRIMARY KEY)')
            c.execute('INSERT OR IGNORE INTO effects VALUES (?)', (op['operationId'],))
        return {'id': op['operationId']}
    def readback(self, op, receipt):
        if self.crash:
            os._exit(0)  # durable receiver effect committed, sender receipt not committed
        with closing(sqlite3.connect(self.path)) as c:
            return c.execute('SELECT id FROM effects WHERE id=?', (receipt['id'],)).fetchone() is not None


@pytest.mark.parametrize('phase', ['before-hint', 'after-effect'])
def test_cold_worker_recovers_same_operation_after_process_death(tmp_path, monkeypatch, phase):
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    s = store(tmp_path)
    r = s.create(OWNER, request())
    child = subprocess.run([sys.executable, __file__, str(tmp_path), r['requestId'], phase],
                           timeout=10, capture_output=True, text=True)
    assert child.returncode == 0, child.stderr
    frozen = s.operation(OWNER, key='crashed')['operation']
    # A later router may run in a new process/home: recover from DB, not any hint.
    api = load_module('crash_recovery_api', ROOT / 'dashboard' / 'plugin_api.py')
    monkeypatch.setattr(api, 'RETRY_INTERVAL_S', 0.01)
    router = api.create_router(ledger_dir=tmp_path/'ledger', action_store=s,
        owner_resolver=lambda sid: dict(OWNER, storedSessionId=sid),
        receiver=DurableReceiver(tmp_path/'effects.db'))
    app = FastAPI(); app.include_router(router)
    try:
        with closing(TestClient(app)) as client:
            assert client.get('/ping').status_code == 200
            settled = await_operation(s, OWNER, key='crashed', timeout=4)
            assert settled['operation']['operationId'] == frozen['operationId']
            repeated = s.decide(OWNER, decision(r, key='crashed'))
            assert repeated['operation']['operationId'] == frozen['operationId']
            with closing(sqlite3.connect(tmp_path/'effects.db')) as c:
                assert c.execute('SELECT id FROM effects').fetchall() == [(frozen['operationId'],)]
    finally:
        router.stop_delivery()


if __name__ == '__main__':
    home, rid, phase = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
    s = store(home)
    op = s.decide(OWNER, decision(s.view(OWNER, rid), key='crashed'))['operation']
    if phase == 'after-effect':
        s.dispatch(OWNER, op['operationId'], DurableReceiver(home/'effects.db', crash=True))
    os._exit(0)
