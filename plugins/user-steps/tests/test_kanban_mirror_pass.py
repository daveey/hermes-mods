"""One feed producer pass checks every other owner's binding once, not once per owner it reconciles.

Every owner's reconcile also re-checks all OTHER owners' bindings of the namespace ("older owners may never
reconcile again"), so a pass over N chats checked each binding N times. Live (hub serve, 48 watched chats) that
sweep was ~30% of the producer's py-spy samples while the pass took 196 s."""
import pytest

from test_kanban_mirror import fixture, OWNER, core

OTHER = dict(OWNER, storedSessionId="session-b")


def counting(mirror, monkeypatch):
    seen = []
    real = mirror._reconcile_binding

    def spy(owner, binding):
        seen.append((owner["storedSessionId"], binding["session"]))
        return real(owner, binding)

    monkeypatch.setattr(mirror, "_reconcile_binding", spy)
    return seen


def test_without_a_pass_every_owner_rechecks_the_other_owners_binding(tmp_path, monkeypatch):
    mirror, store, path, hr = fixture(tmp_path)
    mirror.reconcile(OWNER)
    seen = counting(mirror, monkeypatch)
    for _ in range(3):
        mirror.reconcile(OTHER)
    assert seen == [("session-a", "session-a")] * 3


def test_inside_a_pass_an_owners_binding_is_checked_once(tmp_path, monkeypatch):
    mirror, store, path, hr = fixture(tmp_path)
    mirror.reconcile(OWNER)
    seen = counting(mirror, monkeypatch)
    mod = type(mirror).reconcile.__globals__
    with mod["reconcile_pass"]():
        for _ in range(3):
            mirror.reconcile(OTHER)
        assert seen == [("session-a", "session-a")]
        mirror.reconcile(OWNER)  # its own binding is always checked, whatever an earlier owner did
        assert seen == [("session-a", "session-a")] * 2
    mirror.reconcile(OTHER)
    assert len(seen) == 3, "nothing outlives the pass"


def test_a_failed_check_is_not_recorded_so_the_next_owner_retries_it(tmp_path, monkeypatch):
    mirror, store, path, hr = fixture(tmp_path)
    mirror.reconcile(OWNER)
    real = mirror._reconcile_binding
    attempts = []

    def flaky(owner, binding):
        attempts.append(owner["storedSessionId"])
        if len(attempts) == 1:
            raise OSError("busy")
        return real(owner, binding)

    monkeypatch.setattr(mirror, "_reconcile_binding", flaky)
    mod = type(mirror).reconcile.__globals__
    with mod["reconcile_pass"]():
        mirror.reconcile(OTHER)
        mirror.reconcile(OTHER)
        mirror.reconcile(OTHER)
    assert len(attempts) == 2, "retried once, then recorded"


def test_a_pass_still_withdraws_a_departed_ask_of_another_owner(tmp_path):
    import sqlite3
    mirror, store, path, hr = fixture(tmp_path)
    mirror.reconcile(OWNER)
    assert [r["state"] for r in store.requests(OWNER)] == ["open"]
    with sqlite3.connect(path) as c:
        c.execute("UPDATE tasks SET status='ready', human_request=NULL")
    mod = type(mirror).reconcile.__globals__
    with mod["reconcile_pass"]():
        mirror.reconcile(OTHER)
        mirror.reconcile(OTHER)
    assert store.requests(OWNER) == [], "the older owner's request was withdrawn by the other owner's sweep"
