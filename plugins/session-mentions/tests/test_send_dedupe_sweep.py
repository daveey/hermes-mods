"""A never-claimed keyed request deleted by sweep() must release its key."""
import os

from test_send_dedupe import sm, env, send  # noqa: F401  (env is a fixture)


def test_swept_unclaimed_keyed_request_releases_key(env, monkeypatch):
    base = env[0]
    monkeypatch.setattr(sm.rf, "notify_outbox", lambda base: None)  # no courier claims it
    monkeypatch.setattr(sm, "_yield_requested", lambda: True)  # the sender yields before pickup
    first = send()
    assert first["status"] == "pending"
    out = base / "outbox" / (first["request_id"] + ".json")
    assert out.exists()
    old = sm.rf.time.time() - sm.rf.STALE_SECONDS - 10
    os.utime(out, (old, old))
    sm.rf.sweep(base)
    assert not out.exists()  # deleted while unclaimed: never delivered
    second = send("two")
    assert second["status"] == "pending", second
    assert len(list((base / "outbox").glob("*.json"))) == 1
