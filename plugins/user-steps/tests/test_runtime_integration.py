"""Optional real-core integration; run with the core canonical test runner.

Only model execution is suppressed by the owning-runtime fixture. Store, REST,
identity, RPC dispatch and session persistence use their production paths.
"""
import sys
import pytest

pytest.importorskip('tui_gateway.server')
from tests.tui_gateway.test_prompt_admission import owner  # noqa: F401
from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_decisions import load_module, ROOT, request


def test_rest_to_owning_runtime_once(owner, tmp_path, monkeypatch):
    db, session, params, started = owner
    core = load_module('integration_actions', ROOT / 'core.py')
    api = load_module('integration_api', ROOT / 'dashboard/plugin_api.py')
    cards = load_module('integration_cards', ROOT / 'cards.py')
    own = core.server_owner(params['stored_session_id'])
    st = core.ActionStore(tmp_path / 'actions.sqlite3')
    payload = request(); payload['owner'] = own
    r = st.create(own, payload)
    app = FastAPI()
    app.include_router(api.create_router(ledger_dir=tmp_path, state_db=db.db_path,
        action_store=st, receiver=cards.DecisionDelivery(tmp_path / 'inbox.sqlite3')))
    body = dict(session=own['storedSessionId'], expectedRevision=1, choiceId='approve', idempotencyKey='integration-key')
    client = TestClient(app)
    result = client.post(f"/requests/{r['requestId']}/decisions", json=body)
    assert result.status_code == 200, result.text
    data = result.json()
    assert data['operation']['dispatchState'] == 'delivered', data
    opid = data['operation']['operationId']
    retry = client.post(f'/operations/{opid}/retry', params={'session': own['storedSessionId']})
    assert retry.status_code == 200, retry.text
    assert retry.json()['operation']['dispatchState'] == 'delivered'
    assert len(started) == 1
    rows = db.get_messages(own['storedSessionId'])
    assert len(rows) == 1
    assert opid in rows[0]['content']
    assert 'No training' in rows[0]['content']
