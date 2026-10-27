"""Actual REST -> core failed scheduling -> ordinary history -> fresh revision."""
import copy
import pytest

pytest.importorskip('tui_gateway.server')
from tests.tui_gateway.test_prompt_admission import owner, call  # noqa: F401
from tui_gateway import server
from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_decisions import load_module, ROOT, request


def test_failed_dispatch_rest_recovery_without_approval_leak(owner, tmp_path, monkeypatch):
    db, session, params, started = owner
    core = load_module('cancel_integration_core', ROOT / 'core.py')
    api = load_module('cancel_integration_api', ROOT / 'dashboard/plugin_api.py')
    cards = load_module('cancel_integration_cards', ROOT / 'cards.py')
    own = core.server_owner(params['stored_session_id'])
    st = core.ActionStore(tmp_path / 'actions.sqlite3')
    payload = request(); payload['owner'] = own
    r = st.create(own, payload)
    receiver = cards.DecisionDelivery(tmp_path / 'inbox.sqlite3')
    app = FastAPI()
    app.include_router(api.create_router(ledger_dir=tmp_path, state_db=db.db_path,
                                        action_store=st, receiver=receiver))
    attempts = []
    def fail(target, **kwargs):
        attempts.append(target)
        return None
    monkeypatch.setattr(server, '_start_session_work', fail)
    client = TestClient(app)
    body = dict(session=own['storedSessionId'], expectedRevision=1,
                choiceId='approve', idempotencyKey='cancel-key')
    result = client.post(f"/requests/{r['requestId']}/decisions", json=body)
    assert result.status_code == 200, result.text
    data = result.json()
    assert data['operation']['dispatchState'] == 'cancelled', data
    assert data['request']['state'] == 'cancelled'
    oid = data['operation']['operationId']
    for _ in range(3):
        retry = client.post(f'/operations/{oid}/retry', params={'session': own['storedSessionId']})
        assert retry.json() == data
    assert len(attempts) == 1 and not started
    assert receiver.inbox.context(own) == ''
    assert not db.get_messages_as_conversation(own['storedSessionId'])
    monkeypatch.setattr(server, '_start_session_work', lambda target, **kw: started.append(target) or True)
    sid = next(sid for sid, s in server._sessions.items() if s is session)
    assert 'result' in call('prompt.submit', {'session_id': sid, 'text': 'Please prepare a replacement plan'})
    assert [row['content'] for row in db.get_messages(own['storedSessionId'])] == ['Please prepare a replacement plan']
    replacement = copy.deepcopy(r)
    replacement['scope']['markdown'] = 'Fresh exact scope'
    revised = st.revise(own, r['requestId'], 1, replacement)
    assert revised['revision'] == 2 and 'resolution' not in revised
    fresh = st.decide(own, dict(requestId=r['requestId'], expectedRevision=2,
                              choiceId='approve', idempotencyKey='fresh-key'))
    assert fresh['operation']['operationId'] != oid
    # No implicit execution or approval reuse merely from publishing a revision.
    assert len(attempts) == 1 and not started
