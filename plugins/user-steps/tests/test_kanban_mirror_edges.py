"""Edge cases: owner return, stale older-segment operations, externally revised requests."""

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from test_kanban_mirror import fixture, decide, OWNER, core

B = dict(OWNER, storedSessionId="session-b")


def open_requests(store, owner):
    return [r for r in store.requests(owner) if r["state"] in ("open", "needs_context")]


def revise_externally(store, owner, request):
    body = {k: v for k, v in request.items() if k not in ("resolution",)}
    body["scope"] = dict(body["scope"], markdown="Externally revised scope")
    return store.revise(owner, request["requestId"], request["revision"], body)


def test_return_to_withdrawn_owner_reopens_exactly_one_bound_ask(tmp_path):
    mirror, store, path, hr = fixture(tmp_path)
    mirror.tip = lambda sid: "session-a"
    mirror.reconcile(OWNER)
    first = open_requests(store, OWNER)[0]
    mirror.tip = lambda sid: "session-b"
    mirror.reconcile(B)
    mirror.reconcile(OWNER)
    assert not open_requests(store, OWNER)
    mirror.tip = lambda sid: "session-a"  # tip returns to the earlier owner
    mirror.reconcile(OWNER)
    mirror.reconcile(OWNER)
    assert not open_requests(store, B)
    again = open_requests(store, OWNER)
    assert len(again) == 1 and again[0]["requestId"] != first["requestId"]
    assert mirror.binding(OWNER, again[0]["requestId"])
    result = store.dispatch(OWNER, decide(store, again[0])["operationId"], mirror)
    assert result["operation"]["dispatchState"] == "delivered"
    with sqlite3.connect(path) as c:
        assert c.execute("SELECT status FROM tasks").fetchone()[0] == "ready"


def test_concurrent_return_to_owner_publishes_one_bound_incarnation(tmp_path):
    mirror, store, path, hr = fixture(tmp_path)
    mirror.reconcile(OWNER)
    original = open_requests(store, OWNER)[0]
    mirror.tip = lambda sid: "session-b"
    mirror.reconcile(B)
    assert store.get(OWNER, original["requestId"])["state"] == "withdrawn"
    assert len(open_requests(store, B)) == 1

    other = type(mirror)(
        core,
        core.ActionStore(tmp_path / "actions.sqlite3"),
        boards=mirror.boards,
        backend=mirror.backend,
        session_exists=mirror.session_exists,
        tip=lambda sid: "session-a",
    )
    mirror.tip = lambda sid: "session-a"
    barrier = Barrier(2)
    candidates = []

    def synchronized_publish(producer):
        publish = producer._publish

        def racing_publish(owner, board, snap, request):
            # Both producers choose the same next incarnation before either writes.
            candidates.append(request["requestId"])
            barrier.wait(timeout=10)
            return publish(owner, board, snap, request)

        producer._publish = racing_publish

    for producer in (mirror, other):
        synchronized_publish(producer)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(p.reconcile, OWNER) for p in (mirror, other)]
        for future in futures:
            future.result(timeout=20)

    fresh = open_requests(store, OWNER)
    assert len(fresh) == 1
    rid = fresh[0]["requestId"]
    assert candidates == [rid, rid]
    assert rid != original["requestId"]
    assert not open_requests(store, B)
    with store._read() as c:
        assert (
            c.execute(
                "SELECT COUNT(*) FROM kanban_projections WHERE request_id=?", (rid,)
            ).fetchone()[0]
            == 1
        )
    assert mirror.binding(OWNER, rid)["request"] == json.dumps(
        fresh[0], sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    result = store.dispatch(OWNER, decide(store, fresh[0])["operationId"], mirror)
    assert result["operation"]["dispatchState"] == "delivered"
    with sqlite3.connect(path) as c:
        assert c.execute("SELECT status FROM tasks").fetchone()[0] == "ready"
        assert c.execute("SELECT COUNT(*) FROM task_comments").fetchone()[0] == 1


def test_return_to_owner_never_reasks_after_recorded_answer(tmp_path):
    mirror, store, path, hr = fixture(tmp_path)
    mirror.tip = lambda sid: "session-a"
    mirror.reconcile(OWNER)
    decide(store, store.requests(OWNER)[0])
    mirror.reconcile(OWNER)
    mirror.reconcile(OWNER)
    assert len(store.requests(OWNER, include_withdrawn=True)) == 1


def test_older_segment_pending_operation_retired_when_ask_changes(tmp_path):
    mirror, store, path, hr = fixture(tmp_path)
    mirror.tip = lambda sid: "session-a"
    mirror.reconcile(OWNER)
    op = decide(store, store.requests(OWNER)[0])
    mirror.tip = lambda sid: "session-b"
    mirror.reconcile(B)
    assert not store.requests(B)  # recorded answer still owns this exact ask
    changed = dict(hr, scope="A different question now")
    with sqlite3.connect(path) as c:
        c.execute("UPDATE tasks SET human_request=?", (json.dumps(changed),))
        c.execute("INSERT INTO task_events(task_id,kind) VALUES ('t_test','blocked')")
    mirror.reconcile(B)
    state = store.operation(OWNER, op["operationId"])["operation"]["dispatchState"]
    assert state == "cancelled"
    assert len(open_requests(store, B)) == 1
    with sqlite3.connect(path) as c:
        assert c.execute("SELECT status FROM tasks").fetchone()[0] == "blocked"
        assert not c.execute("SELECT * FROM task_comments").fetchall()


def test_older_segment_delivered_receipt_is_preserved_when_ask_changes(tmp_path):
    mirror, store, path, hr = fixture(tmp_path)
    mirror.tip = lambda sid: "session-a"
    mirror.reconcile(OWNER)
    op = decide(store, store.requests(OWNER)[0])
    mirror.deliver(op)  # board committed, outbox acknowledgment still pending
    with sqlite3.connect(path) as c:
        c.execute(
            "UPDATE tasks SET status='blocked',human_request=?",
            (json.dumps(dict(hr, scope="new")),),
        )
        c.execute("INSERT INTO task_events(task_id,kind) VALUES ('t_test','blocked')")
    mirror.tip = lambda sid: "session-b"
    mirror.reconcile(B)
    state = store.operation(OWNER, op["operationId"])["operation"]["dispatchState"]
    assert state != "cancelled"


def test_externally_revised_open_request_is_withdrawn_and_reprojected(tmp_path):
    mirror, store, path, hr = fixture(tmp_path)
    mirror.reconcile(OWNER)
    original = store.requests(OWNER)[0]
    revise_externally(store, OWNER, original)
    mirror.reconcile(OWNER)
    assert store.get(OWNER, original["requestId"])["state"] == "withdrawn"
    fresh = open_requests(store, OWNER)
    assert len(fresh) == 1 and fresh[0]["requestId"] != original["requestId"]
    assert fresh[0]["scope"]["markdown"] != "Externally revised scope"
    assert mirror.binding(OWNER, fresh[0]["requestId"])["request"] == json.dumps(
        fresh[0], sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )


def test_older_segment_revised_decision_retires_without_source_change(tmp_path):
    mirror, store, path, hr = fixture(tmp_path)
    mirror.reconcile(OWNER)
    original = store.requests(OWNER)[0]
    revised = revise_externally(store, OWNER, original)
    op = store.decide(
        OWNER,
        dict(
            requestId=revised["requestId"],
            expectedRevision=revised["revision"],
            choiceId=revised["choices"][1]["id"],
            revisionText=revised["choices"][1]["label"],
            idempotencyKey="older-revised-op",
        ),
    )["operation"]
    mirror.tip = lambda sid: "session-b"
    for _ in range(3):
        mirror.reconcile(B)
    retired = store.operation(OWNER, op["operationId"])["operation"]
    assert retired["dispatchState"] == "cancelled"
    assert (
        retired["retired"]["reason"]
        == "Request no longer matches its frozen server binding"
    )
    fresh = open_requests(store, B)
    assert len(fresh) == 1
    assert mirror.binding(B, fresh[0]["requestId"])
    assert not open_requests(store, OWNER)
    with pytest.raises(ValueError, match="binding"):
        mirror.deliver(op)
    with sqlite3.connect(path) as c:
        assert c.execute("SELECT status,human_request FROM tasks").fetchone() == (
            "blocked",
            json.dumps(hr),
        )
        assert not c.execute("SELECT * FROM task_comments").fetchall()
        assert c.execute("SELECT kind FROM task_events").fetchall() == [("blocked",)]


def test_externally_revised_decision_fails_closed_then_retires(tmp_path):
    mirror, store, path, hr = fixture(tmp_path)
    mirror.reconcile(OWNER)
    original = store.requests(OWNER)[0]
    revised = revise_externally(store, OWNER, original)
    op = store.decide(
        OWNER,
        dict(
            requestId=revised["requestId"],
            expectedRevision=revised["revision"],
            choiceId=revised["choices"][1]["id"],
            revisionText=revised["choices"][1]["label"],
            idempotencyKey="revised-op",
        ),
    )["operation"]
    with pytest.raises(ValueError, match="binding"):
        mirror.deliver(op)
    mirror.reconcile(OWNER)
    assert (
        store.operation(OWNER, op["operationId"])["operation"]["dispatchState"]
        == "cancelled"
    )
    mirror.reconcile(OWNER)
    assert len(open_requests(store, OWNER)) == 1
    with sqlite3.connect(path) as c:
        assert c.execute("SELECT status FROM tasks").fetchone()[0] == "blocked"
        assert not c.execute("SELECT * FROM task_comments").fetchall()
