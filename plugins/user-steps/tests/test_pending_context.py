"""The per-turn list must follow canonical lifecycle, never stale revision bodies."""

import pytest
from test_withdrawal import ROOT, OWNER, complete_request, decision, load_module


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
