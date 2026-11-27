"""Settlement must preserve uncertainty without breaking unrelated relay results."""
import json

import pytest

from test_send_dedupe import sm, env, send


def test_unexpected_submit_status_stays_pending(env):
    env[2][0] = {"ok": False, "delivered": False, "retry_safe": False,
                 "status": "accepted", "error": "Unexpected submit status: accepted"}
    send()
    assert send("two")["status"] == "duplicate_pending"
    assert len(env[1]) == 1


@pytest.mark.parametrize("ledger", ["{broken", "[]", '{"bad": {}}'])
def test_bad_ledger_does_not_block_result(env, ledger, caplog):
    base = env[0]
    (base / "send-dedupe.json").write_text(ledger)
    assert sm.rf.write_result("unrelated", {"ok": True}, base)
    assert json.loads((base / "results/unrelated.json").read_text()) == {"ok": True}
    assert "dedupe" in caplog.text
    with pytest.raises(Exception):
        send()
    assert not env[1]


def test_bad_ledger_does_not_break_takeback(env, caplog):
    base = env[0]
    (base / "send-dedupe.json").write_text("{broken")
    path = base / "outbox/withdraw.json"
    path.write_text("{}")
    assert sm.rf.take_back(path)
    assert not path.exists()
    assert "dedupe" in caplog.text


def test_unwritable_ledger_does_not_block_result(env, monkeypatch, caplog):
    base = env[0]
    send()
    original = sm.rf.atomic_write
    def fail_ledger(path, payload):
        if path.name == "send-dedupe.json":
            raise PermissionError("synthetic ledger write failure")
        return original(path, payload)
    monkeypatch.setattr(sm.rf, "atomic_write", fail_ledger)
    assert sm.rf.write_result("unrelated", {"ok": True}, base)
    assert (base / "results/unrelated.json").exists()
    assert "dedupe" in caplog.text


def test_expired_unpicked_request_releases_key(env, monkeypatch):
    base = env[0]
    monkeypatch.setattr(sm.rf, "notify_outbox", lambda base: None)
    monkeypatch.setattr(sm, "_yield_requested", lambda: True)
    first = send()
    now = sm.time.time()
    monkeypatch.setattr(sm.time, "time", lambda: now + 700)
    assert sm.rf.claim_pending(base) == []
    result = json.loads((base / "results" / (first["request_id"] + ".json")).read_text())
    assert result["retry_safe"] is True
    assert not json.loads((base / "send-dedupe.json").read_text())
    assert send("two")["status"] == "pending"
