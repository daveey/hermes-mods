"""Remote waits against a real, fixture-owned origin ledger (no producer mocks)."""
import json

import pytest
from test_real_core import real  # noqa: F401


@pytest.mark.parametrize("origin_session,profile,gateway,expected", [
    ("chat", "default", "origin", True),
    ("other", "default", "origin", False),
    ("chat", "other", "origin", False),
    ("chat", "default", "elsewhere", False),
])
def test_wait_binding(real, tmp_path, monkeypatch, origin_session, profile, gateway, expected):
    _, plugin = real
    from hermes_cli import kanban_origin_ledger as ledger

    monkeypatch.setenv("HERMES_KANBAN_HOME", str(tmp_path / "boards"))
    monkeypatch.delenv("HERMES_KANBAN_DB", raising=False)
    monkeypatch.setenv("HERMES_PROFILE", "default")
    monkeypatch.setenv("HERMES_HUB_STATE", str(tmp_path / "hub"))
    config = tmp_path / "mail.json"
    config.write_text(json.dumps({"gateway": "origin"}))
    monkeypatch.setenv("HERMES_MAIL_CONFIG", str(config))
    binding = {"binding_id": "fixture-binding", "version": 1, "publication_version": 1,
               "owner": {"gateway": "remote", "profile": "default", "board": "default", "task_id": "t_remote"},
               "origin": {"gateway": gateway, "profile": profile, "session": origin_session},
               "task_record_key": "default/default/t_remote"}
    path = tmp_path / "kanban/origin-ledger.db"
    with ledger._db(path) as conn:
        conn.execute("INSERT INTO origin_obligations VALUES (?,?,?,?,?,?,?)",
                     (ledger._binding_key(binding), ledger.binding_hash(binding), json.dumps(binding),
                      origin_session, "{}", 1, 0))
        conn.commit()
    result = json.loads(plugin.handle({"kind": "wait", "on": [{"kind": "card", "id": "t_remote"}]}, session_id="chat"))
    assert ("t_remote" in result["linked"]) is expected, result
    if expected:
        assert "not_linked" not in result
        assert result["link_notes"] == ["t_remote: linked on remote (origin-bound to this chat)"]
    else:
        assert "link it with:" in result["not_linked"][0]
