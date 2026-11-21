"""The per-turn list must follow canonical lifecycle, never stale revision bodies."""

import pytest
from test_withdrawal import ROOT, OWNER, complete_request, decision, load_module


@pytest.mark.parametrize('kind', ['approval', 'choice', 'input', 'external_step'])
def test_needs_context_is_agent_repair_not_a_user_decision(tmp_path, monkeypatch, kind):
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    monkeypatch.delenv('HERMES_KANBAN_TASK', raising=False)
    plugin = load_module('repair_context_hook', ROOT / '__init__.py')
    monkeypatch.setattr(plugin.core, 'home', lambda: tmp_path)
    monkeypatch.setattr(plugin.core, 'kanban_root', lambda: tmp_path)
    monkeypatch.setattr(plugin.core, 'server_owner', lambda sid: dict(OWNER, storedSessionId=sid))
    monkeypatch.setattr(plugin.chat, 'reconcile', lambda *a, **kw: None)
    store = plugin.core.ActionStore()
    request = complete_request(title='Needs agent context')
    request.update(kind=kind, state='needs_context')
    request['scope']['complete'] = False
    if kind != 'approval':
        request['choices'] = [dict(id='answer', label='Answer', effect='answer')]
    pending = store.create(OWNER, request)
    note = plugin.pre_llm_call(session_id='session-a', platform='desktop')['context']
    assert '[Agent work:' in note
    repair = note.split('[Agent work:', 1)[1]
    assert pending['requestId'] in repair
    assert 'expectedRevision' in repair
    assert 'Do not ask the user to request' in repair
    listed = note.split('[Canonical requests awaiting a user decision:', 1)[-1]
    assert not listed.startswith(' this list'), 'repair-only requests must not be labeled user decisions'
    assert '[Open asks from this chat' not in note
    assert store.get(OWNER, pending['requestId'])['state'] == 'needs_context'
    seen = []
    monkeypatch.setattr(plugin, '_presenter', lambda _store: type('Presenter', (), {'present': lambda self, r: r})())
    monkeypatch.setattr(plugin, '_panel_letter_hint', lambda message, requests: seen.extend(requests) or '')
    plugin.pre_llm_call(session_id='session-a', platform='desktop', user_message='a')
    assert seen == []
    ready = store.create(OWNER, complete_request(title='Actual user decision'))
    note = plugin.pre_llm_call(session_id='session-a', platform='desktop', user_message='a')['context']
    user_list = note.split('[Canonical requests awaiting a user decision:', 1)[1].split('[Agent work:', 1)[0]
    assert ready['requestId'] in user_list
    assert pending['requestId'] not in user_list
    assert [r['requestId'] for r in seen] == [ready['requestId']]


@pytest.mark.parametrize("state", ["resolved", "cancelled", "superseded", "withdrawn"])
def test_terminal_request_not_in_agent_pending_context(tmp_path, monkeypatch, state):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.delenv("HERMES_KANBAN_TASK", raising=False)
    plugin = load_module("closed_request_hook", ROOT / "__init__.py")
    monkeypatch.setattr(plugin.core, "home", lambda: tmp_path)
    monkeypatch.setattr(plugin.core, "kanban_root", lambda: tmp_path)
    monkeypatch.setattr(
        plugin.core, "server_owner", lambda sid: dict(OWNER, storedSessionId=sid)
    )
    monkeypatch.setattr(plugin.chat, "reconcile", lambda *a, **kw: None)
    store = plugin.core.ActionStore()
    closed = store.create(OWNER, complete_request(title="Closed historical ask"))
    keep = store.create(OWNER, complete_request(title="Current question"))
    # Only the current row changes. Its immutable revision still says "open".
    with store._tx() as c:
        c.execute(
            "UPDATE requests SET state=? WHERE id=?", (state, closed["requestId"])
        )
    result = plugin.pre_llm_call(session_id="session-a", platform="desktop")
    assert result and keep["requestId"] in result["context"]
    assert closed["requestId"] not in result["context"]


def test_recorded_but_undelivered_decision_not_listed_as_open(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.delenv("HERMES_KANBAN_TASK", raising=False)
    plugin = load_module("pending_request_hook", ROOT / "__init__.py")
    monkeypatch.setattr(plugin.core, "home", lambda: tmp_path)
    monkeypatch.setattr(plugin.core, "kanban_root", lambda: tmp_path)
    monkeypatch.setattr(
        plugin.core, "server_owner", lambda sid: dict(OWNER, storedSessionId=sid)
    )
    monkeypatch.setattr(plugin.chat, "reconcile", lambda *a, **kw: None)
    store = plugin.core.ActionStore()
    recorded = store.create(OWNER, complete_request(title="Recorded question"))
    store.decide(OWNER, decision(recorded))
    keep = store.create(OWNER, complete_request(title="Still unanswered question"))
    result = plugin.pre_llm_call(session_id="session-a", platform="desktop")
    assert result and keep["requestId"] in result["context"]
    assert recorded["requestId"] not in result["context"]
