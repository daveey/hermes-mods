"""Real canonical requests -> core collector -> persisted unified state."""
from types import SimpleNamespace

import pytest
from core_runtime import require_isolated_core

require_isolated_core()
pytest.importorskip('hermes_session_state_requests')
from tests.session_state import conftest as harness
from test_decisions import core, request
from test_withdrawal import ROOT, load_module
from hermes_cli import kanban_db as kb
from hermes_session_state_collect import collect

kanban_root = harness.kanban_root
registry = harness.registry
board = harness.board
db = harness.db
clock = harness.clock
store = harness.store


def setup_reader(tmp_path, monkeypatch):
    adapter = load_module('state_request_adapter', ROOT / 'session_state.py')
    # Real module loading, ActionStore and presenter; only trusted installation
    # identity is pinned, deliberately different from the courier gateway name.
    api = load_module('state_request_api', ROOT / 'dashboard/plugin_api.py')
    actual_core = api._load('core')
    owner = {'gatewayId': 'install:' + 'a' * 32, 'profile': 'default', 'storedSessionId': 'chat'}
    monkeypatch.setattr(actual_core, 'server_owner', lambda sid: dict(owner, storedSessionId=sid))
    monkeypatch.setattr(actual_core, 'home', lambda: tmp_path)
    disposals = []
    adapter.register(SimpleNamespace(on_unload=disposals.append))
    return actual_core, owner, disposals


@pytest.mark.parametrize('same_card', [False, True])
def test_real_reader_store_sibling_resolution(tmp_path, monkeypatch, db, board, store, same_card):
    actions, owner, disposals = setup_reader(tmp_path, monkeypatch)
    try:
        tasks = [kb.create_task(board, title=title, assignee='worker', session_id='chat')
                 for title in (('First deploy',) if same_card else ('First deploy', 'Other deploy'))]
        for tid in tasks:
            assert kb.block_task(board, tid, kind='needs_input', human_request=harness.HUMAN_REQUEST)
        identity = collect(db, 'chat')['parts']['identity']
        action_store = actions.ActionStore(tmp_path / 'user-steps' / 'actions.sqlite3')
        requests = []
        for tid in (tasks * 2 if same_card else tasks):
            payload = request(aliases=[{'type': 'kanban', 'id': tid, 'board': 'default',
                'gatewayId': identity['gateway'], 'profile': identity['profile']}])
            payload['owner'] = owner
            if same_card and requests:
                # Alias lookup is one-to-one; independent asks may reference the
                # same card in their scope without stealing its lookup alias.
                payload['scope']['sourceRefs'] = payload['aliases']
                payload['aliases'] = []
            requests.append(action_store.create(owner, payload))
        captured = collect(db, 'chat')
        assert not captured['errors'], captured['errors']
        assert {r['requestId'] for r in captured['requests']} == {r['requestId'] for r in requests}
        assert all(r['owner'] == owner for r in captured['requests'])
        import hermes_state_outcome as outcome
        outcome.register_outcome_provider('same-canonical-request', lambda sid: {
            'decision_items': [{'id': requests[0]['requestId'], 'question': 'First deploy',
                                'choices': [{'label': 'Approve', 'value': 'approve'}], 'state': 'open'}]})
        before = store.recompute('chat')
        assert {r['id'] for r in before['asks']} == {r['requestId'] for r in requests}
        key = 'default/' + tasks[0]
        store.add_resolution('chat', {'request_id': requests[0]['requestId'], 'card': key,
            'episode': before['cards'][key]['block_episode'], 'action': 'answer',
            'choice_id': 'approve', 'label': 'Approve', 'by': 'you', 'at': before['computed_at'], 'delivery': 'committed'})
        after = store.recompute('chat')
        assert after['cards'][key]['red'] is same_card
        assert after['cards']['default/' + tasks[-1]]['red']
        assert [r['id'] for r in after['asks']] == [requests[1]['requestId']]
        assert store.get('chat')['rev'] == after['rev']
        path = tmp_path / 'user-steps' / 'actions.sqlite3'
        saved = path.with_suffix('.saved')
        path.rename(saved)
        path.write_bytes(b'not sqlite')
        try:
            unavailable = store.recompute('chat')
            assert unavailable['sources']['requests:user-steps']['coverage'] == 'unknown'
            assert 'unblocked by you' not in unavailable['cards'][key]['detail']
        finally:
            saved.replace(path)
        recovered = store.recompute('chat')
        assert [r['id'] for r in recovered['asks']] == [requests[1]['requestId']]
        assert recovered['cards'][key]['red'] is same_card
        second_key = 'default/' + tasks[-1]
        store.add_resolution('chat', {'request_id': requests[1]['requestId'], 'card': second_key,
            'episode': after['cards'][second_key]['block_episode'], 'action': 'answer',
            'choice_id': 'approve', 'label': 'Second approval', 'by': 'you', 'at': after['computed_at'],
            'delivery': 'committed'})
        settled = store.recompute('chat')
        assert not any(card['red'] for card in settled['cards'].values())
        assert settled['asks'] == []
    finally:
        for dispose in disposals:
            dispose()


def test_reader_failure_is_unknown_not_empty(tmp_path, monkeypatch, db, store):
    _, _, disposals = setup_reader(tmp_path, monkeypatch)
    try:
        path = tmp_path / 'user-steps' / 'actions.sqlite3'
        path.parent.mkdir()
        path.write_bytes(b'not sqlite')
        inputs = collect(db, 'chat')
        assert 'requests:user-steps' in inputs['errors']
        result = store.recompute('chat')
        assert result['sources']['requests:user-steps']['coverage'] == 'unknown'
    finally:
        for dispose in disposals:
            dispose()
