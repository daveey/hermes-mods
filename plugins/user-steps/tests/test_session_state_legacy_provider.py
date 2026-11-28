"""Registered legacy outcome provider and canonical request intake must agree."""
from types import SimpleNamespace

import pytest
from core_runtime import require_isolated_core

require_isolated_core()
pytest.importorskip('hermes_session_state_requests')
from test_session_state_collector import setup_reader
from test_withdrawal import ROOT, load_module
from test_decisions import request
from tests.session_state import conftest as harness
from hermes_cli import kanban_db as kb
from hermes_session_state_collect import collect

kanban_root = harness.kanban_root
registry = harness.registry
board = harness.board
db = harness.db
clock = harness.clock
store = harness.store


@pytest.mark.parametrize('same_card', [False, True])
@pytest.mark.parametrize('install_ref', [False, True])
def test_registered_legacy_provider_does_not_duplicate_requests(tmp_path, monkeypatch, db, board, store,
                                                               same_card, install_ref):
    actions, owner, disposals = setup_reader(tmp_path, monkeypatch)
    plugin = load_module('state_legacy_outcome', ROOT.parent / 'session-outcome/__init__.py')
    legacy = plugin._user_steps()
    monkeypatch.setattr(legacy, 'home', lambda: tmp_path)
    monkeypatch.setattr(legacy, 'server_owner', lambda sid: dict(owner, storedSessionId=sid))
    plugin.register(SimpleNamespace(register_tool=lambda **kw: None, register_hook=lambda *a: None))
    try:
        tasks = [kb.create_task(board, title=title, assignee='worker', session_id='chat')
                 for title in (('First',) if same_card else ('First', 'Second'))]
        for tid in tasks:
            assert kb.block_task(board, tid, kind='needs_input', human_request=harness.HUMAN_REQUEST)
        identity = collect(db, 'chat')['parts']['identity']
        (tmp_path / 'install_id').write_text('a' * 32 + '\n')
        ref_gateway = owner['gatewayId'] if install_ref else identity['gateway']
        action_store = actions.ActionStore(tmp_path / 'user-steps/actions.sqlite3')
        ledger = legacy.Ledger()
        requests = []
        for index, tid in enumerate(tasks * 2 if same_card else tasks):
            step_id = 'us_legacy_' + str(index)
            payload = request(aliases=[dict(type='user_step', id=step_id,
                gatewayId=owner['gatewayId'], profile=owner['profile'])])
            payload['owner'] = owner
            payload['scope']['sourceRefs'] = [dict(type='kanban', id=tid, board='default',
                gatewayId=ref_gateway, profile=identity['profile'])]
            saved = action_store.create(owner, payload)
            requests.append(saved)
            with ledger.edit('chat') as data:
                data['steps'].append(dict(id=step_id, status='open', session='chat',
                    text=payload['title'], requestId=saved['requestId'], created=index + 1))
        captured = collect(db, 'chat')
        assert not captured['errors'], captured['errors']
        # Ensure the real registered legacy path contributed, not a no-provider fixture.
        assert len(captured['parts']['control_asks']) == 2
        result = store.recompute('chat')
        assert {a['id'] for a in result['asks']} == {r['requestId'] for r in requests}
        assert len(result['asks']) == 2
        assert len(store.get('chat')['asks']) == 2
        from test_decisions import decision
        action_store.decide(owner, decision(requests[0]))
        key = 'default/' + tasks[0]
        store.add_resolution('chat', dict(request_id=requests[0]['requestId'], card=key,
            episode=result['cards'][key]['block_episode'], action='answer', choice_id='approve',
            label='Approve', by='you', at=result['computed_at'], delivery='committed'))
        answered = store.recompute('chat')
        assert [a['id'] for a in answered['asks']] == [requests[1]['requestId']]
        assert answered['cards'][key]['red'] is same_card
        assert answered['header']['status'] != 'done'  # recorded is not delivered
        actions.withdraw_request(action_store, ledger, owner, requests[1]['requestId'],
                                 by='agent', reason='No longer needed')
        # Replay the stale ledger, as an older writer or restored cache could.
        with ledger.edit('chat') as data:
            for step in data['steps']:
                step['status'] = 'open'
        retired = store.recompute('chat')
        assert retired['asks'] == []
        assert collect(db, 'chat')['parts']['control_asks'] == []
        assert store.get('chat')['asks'] == []
        assert kb.unblock_task(board, tasks[0])
        assert kb.block_task(board, tasks[0], kind='needs_input', human_request=harness.HUMAN_REQUEST)
        payload = request()
        payload['owner'] = owner
        payload['scope']['sourceRefs'] = [dict(type='kanban', id=tasks[0], board='default',
            gatewayId=ref_gateway, profile=identity['profile'])]
        new = action_store.create(owner, payload)
        reblocked = store.recompute('chat')
        assert reblocked['cards'][key]['block_episode'] != result['cards'][key]['block_episode']
        assert reblocked['cards'][key]['red']
        assert [a['id'] for a in reblocked['asks']] == [new['requestId']]
        path = tmp_path / 'user-steps/actions.sqlite3'
        saved_path = path.with_suffix('.saved')
        path.rename(saved_path)
        path.write_bytes(b'not sqlite')
        try:
            unknown = store.recompute('chat')
            assert unknown['sources']['requests:user-steps']['coverage'] == 'unknown'
            assert unknown['sources']['controls']['coverage'] == 'unknown'
            assert unknown['header']['status'] != 'done'
        finally:
            saved_path.replace(path)
        assert [a['id'] for a in store.recompute('chat')['asks']] == [new['requestId']]
    finally:
        for dispose in disposals:
            dispose()
