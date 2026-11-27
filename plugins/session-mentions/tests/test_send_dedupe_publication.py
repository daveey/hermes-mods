"""Reservation durability, failed publication rollback, and target identity."""
import json

import pytest

from test_send_dedupe import sm, env, send  # noqa: F401  (env is a fixture)


def test_reservation_is_on_disk_before_outbox_publication(env, monkeypatch):
    write = sm.rf.atomic_write
    published = []

    def spy(path, payload):
        if path.parent.name == "outbox":
            ledger = json.loads((env[0] / "send-dedupe.json").read_text())
            assert any(entry["request_id"] == payload["id"] and entry["state"] == "pending"
                       for entry in ledger.values())
            published.append(payload["id"])
        return write(path, payload)

    monkeypatch.setattr(sm.rf, "atomic_write", spy)
    assert send()["status"] == "queued"
    assert published == [env[1][0]["id"]]


def test_failed_outbox_publication_releases_reservation(env, monkeypatch):
    write = sm.rf.atomic_write

    def fail_outbox(path, payload):
        if path.parent.name == "outbox":
            raise OSError("synthetic outbox failure")
        return write(path, payload)

    with monkeypatch.context() as patch:
        patch.setattr(sm.rf, "atomic_write", fail_outbox)
        with pytest.raises(OSError, match="synthetic outbox failure"):
            send()
    assert json.loads((env[0] / "send-dedupe.json").read_text()) == {}
    assert not list((env[0] / "outbox").glob("*.json"))
    assert send("two")["status"] == "queued"
    assert len(env[1]) == 1


def test_same_key_distinct_profiles_publish_two_requests(env):
    assert send(target="@session:alt@remote/x")["status"] == "queued"
    assert send("two", target="@session:default@remote/x")["status"] == "queued"
    assert len(env[1]) == 2
    assert len({req["id"] for req in env[1]}) == 2
