"""Sweeping protocol receipts must not turn possible delivery into permission to retry."""
import json
import os

import pytest

from test_send_dedupe import sm, env, send  # noqa: F401 (env is a fixture)


@pytest.mark.parametrize("result", [
    None,
    {"ok": False, "status": "unknown", "delivered": None, "retry_safe": False},
    {"ok": True, "status": "queued", "delivered": True},
], ids=["claimed", "uncertain", "delivered"])
def test_swept_claimed_keyed_request_keeps_key(env, monkeypatch, result):
    base = env[0]
    monkeypatch.setattr(sm.rf, "notify_outbox", lambda base: None)
    monkeypatch.setattr(sm, "_yield_requested", lambda: True)
    first = send()
    assert first["status"] == "pending"
    claimed = base / "claimed" / (first["request_id"] + ".json")
    (base / "outbox" / claimed.name).rename(claimed)
    paths = [claimed]
    if result is not None:
        sm.rf.write_result(first["request_id"], result, base)
        paths.append(base / "results" / claimed.name)
    before = json.loads((base / "send-dedupe.json").read_text())
    old = sm.rf.time.time() - sm.rf.STALE_SECONDS - 10
    for path in paths:
        assert path.exists()
        os.utime(path, (old, old))
    sm.rf.sweep(base)
    assert all(not path.exists() for path in paths)
    assert json.loads((base / "send-dedupe.json").read_text()) == before
    second = send("two")
    assert second["status"] == ("duplicate" if result and result["ok"] else "duplicate_pending")
    assert second["original"]["request_id"] == first["request_id"]
    assert not list((base / "outbox").glob("*.json"))
