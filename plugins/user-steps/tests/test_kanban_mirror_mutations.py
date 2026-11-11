"""Mutation-kill tests for the blocked-card mirror's authority safeguards.

Each test pins one guard that survived the existing suite when its condition was
mutated (see the mutation artifact). Tests drive the public Mirror/ActionStore
surface or call the named private gate directly when the end state of the public
flow is the same with or without the guard.
"""

import copy
import json
import sqlite3
import uuid

import pytest
from test_kanban_mirror import fixture, decide, OWNER, core
from test_kanban_mirror_edges import B, open_requests, revise_externally

MISMATCH = "Request no longer matches its frozen server binding"


def snap_and_request(mirror, owner=OWNER):
    snap = mirror._read_snapshot("default", "t_test", owner)
    return snap, mirror._request(owner, "default", snap)


def insert_event(path, kind, payload):
    with sqlite3.connect(path) as c:
        c.execute(
            "INSERT INTO task_events(task_id,kind,payload) VALUES ('t_test',?,?)",
            (kind, json.dumps(payload)),
        )


# --- receipt replay / fingerprint / readback --------------------------------


def test_conflicting_fingerprint_for_same_operation_is_refused(tmp_path):
    mirror, store, path, hr = fixture(tmp_path)
    mirror.reconcile(OWNER)
    op = decide(store, store.requests(OWNER)[0])
    insert_event(
        path,
        "unblocked",
        {"user_steps_projection": {"operationId": op["operationId"], "fingerprint": "x"}},
    )
    with pytest.raises(core.ActionError, match="operation conflict"):
        mirror.deliver(op)


def test_readback_ignores_receipt_for_another_operation(tmp_path):
    mirror, store, path, hr = fixture(tmp_path)
    mirror.reconcile(OWNER)
    op = decide(store, store.requests(OWNER)[0])
    insert_event(
        path,
        "unblocked",
        {"user_steps_projection": {"operationId": "other-op", "fingerprint": "x"}},
    )
    # Another operation's event (even with a different fingerprint) is not this
    # operation's receipt: readback sees none rather than raising a conflict.
    assert mirror.readback(op, {"operationId": op["operationId"]}) is False


def test_readback_matches_only_the_recorded_receipt(tmp_path):
    mirror, store, path, hr = fixture(tmp_path)
    mirror.reconcile(OWNER)
    op = decide(store, store.requests(OWNER)[0])
    assert mirror.readback(op, {"operationId": op["operationId"]}) is False
    receipt = mirror.deliver(op)
    assert mirror.readback(op, receipt) is True
    assert mirror.readback(op, dict(receipt, value="tampered")) is False


def test_second_delivery_does_not_reenter_the_board_transition(tmp_path):
    mirror, store, path, hr = fixture(tmp_path)
    mirror.reconcile(OWNER)
    op = decide(store, store.requests(OWNER)[0])
    receipt = mirror.deliver(op)
    api = mirror.backend
    calls = []
    real = api.unblock_task
    api.unblock_task = lambda *a, **k: calls.append(1) or real(*a, **k)
    assert mirror.deliver(op) == receipt
    assert calls == []


# --- snapshot transaction fence --------------------------------------------


def test_fence_rejects_task_that_was_reunblocked_without_new_blocked_event(tmp_path):
    mirror, store, path, hr = fixture(tmp_path)
    mirror.reconcile(OWNER)
    op = decide(store, store.requests(OWNER)[0])
    # Same ask text, but the lifecycle moved on: generation must differ.
    insert_event(path, "unblocked", {})
    with pytest.raises(core.ActionError):
        mirror.deliver(op)
    with sqlite3.connect(path) as c:
        assert c.execute("SELECT status FROM tasks").fetchone()[0] == "blocked"
        assert not c.execute("SELECT * FROM task_comments").fetchall()


# --- pending relocation suppression ----------------------------------------


def test_old_owner_is_withdrawn_before_new_owner_is_published(tmp_path):
    mirror, store, path, hr = fixture(tmp_path)
    mirror.tip = lambda sid: "session-a"
    mirror.reconcile(OWNER)
    first = open_requests(store, OWNER)[0]
    mirror.tip = lambda sid: "session-b"
    # Exercise only the relocation step: it must itself retire the old open ask.
    snap = mirror._read_snapshot("default", "t_test", B)
    assert mirror._relocate(B, "default", snap) is True
    assert store.get(OWNER, first["requestId"])["state"] == "withdrawn"


# --- atomic create binding / conflict adoption ------------------------------


def values_for(mirror, snap, request):
    return (
        mirror.store._ns(OWNER),
        request["requestId"],
        OWNER["storedSessionId"],
        "default",
        snap["task"],
        json.dumps(snap, sort_keys=True, separators=(",", ":"), ensure_ascii=False),
        json.dumps(request, sort_keys=True, separators=(",", ":"), ensure_ascii=False),
    )


def test_publish_never_adopts_unbound_preexisting_identical_request(tmp_path):
    mirror, store, path, hr = fixture(tmp_path)
    snap, request = snap_and_request(mirror)
    store.create(OWNER, copy.deepcopy(request))  # exact body, no binding
    with pytest.raises(core.ActionError, match="conflict"):
        mirror._publish(OWNER, "default", snap, request)
    assert mirror.binding(OWNER, request["requestId"]) is None


def test_publish_rejects_binding_row_that_differs_from_frozen_values(tmp_path):
    mirror, store, path, hr = fixture(tmp_path)
    snap, request = snap_and_request(mirror)
    values = list(values_for(mirror, snap, request))
    values[5] = values[5].replace("Choose a color", "Other")

    def bind(c, frozen):
        c.execute("INSERT INTO kanban_projections VALUES (?,?,?,?,?,?,?)", values)

    store.create(OWNER, copy.deepcopy(request), bind_transaction=bind)
    with pytest.raises(core.ActionError, match="conflict"):
        mirror._publish(OWNER, "default", snap, request)


def test_publish_rejects_stored_body_that_differs_from_request(tmp_path):
    mirror, store, path, hr = fixture(tmp_path)
    snap, request = snap_and_request(mirror)
    stored = copy.deepcopy(request)
    stored["title"] = "A different title"
    values = values_for(mirror, snap, request)

    def bind(c, frozen):
        c.execute("INSERT INTO kanban_projections VALUES (?,?,?,?,?,?,?)", values)

    store.create(OWNER, stored, bind_transaction=bind)
    with pytest.raises(core.ActionError, match="conflict"):
        mirror._publish(OWNER, "default", snap, request)


def test_publish_propagates_non_conflict_store_errors(tmp_path, monkeypatch):
    mirror, store, path, hr = fixture(tmp_path)
    snap, request = snap_and_request(mirror)

    def boom(*a, **k):
        raise core.ActionError("store unavailable", 500)

    monkeypatch.setattr(store, "create", boom)
    with pytest.raises(core.ActionError, match="unavailable"):
        mirror._publish(OWNER, "default", snap, request)


# --- new-incarnation terminal / retirement reason gates ---------------------


def test_incarnation_zero_identity_is_the_original_bytes(tmp_path):
    mirror, store, path, hr = fixture(tmp_path)
    snap = mirror._read_snapshot("default", "t_test", OWNER)
    identity = json.dumps(
        [OWNER, "default", snap], sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    expected = str(uuid.uuid5(uuid.NAMESPACE_URL, "hermes:kanban-ask:" + identity))
    assert mirror._request(OWNER, "default", snap)["requestId"] == expected
    assert mirror._request(OWNER, "default", snap, 1)["requestId"] != expected


def test_current_request_returns_the_open_bound_request(tmp_path):
    mirror, store, path, hr = fixture(tmp_path)
    mirror.reconcile(OWNER)
    snap, request = snap_and_request(mirror)
    assert mirror._current_request(OWNER, "default", snap)["requestId"] == request[
        "requestId"
    ]


def test_answered_delivered_request_never_gets_new_incarnation(tmp_path):
    mirror, store, path, hr = fixture(tmp_path)
    mirror.reconcile(OWNER)
    snap, request = snap_and_request(mirror)
    op = decide(store, store.requests(OWNER)[0])
    store.dispatch(OWNER, op["operationId"], mirror)
    assert store.get(OWNER, request["requestId"])["state"] not in (
        "open",
        "needs_context",
        "withdrawn",
        "cancelled",
    )
    assert mirror._current_request(OWNER, "default", snap) is None


def test_cancelled_for_other_reason_never_gets_new_incarnation(tmp_path):
    mirror, store, path, hr = fixture(tmp_path)
    mirror.reconcile(OWNER)
    snap, request = snap_and_request(mirror)
    op = decide(store, store.requests(OWNER)[0])
    store.retire_operation(
        OWNER, op["operationId"], by="system", reason="Source card ask changed", evidence="x"
    )
    assert store.get(OWNER, request["requestId"])["state"] == "cancelled"
    assert mirror._current_request(OWNER, "default", snap) is None


def test_mismatch_retired_request_mints_next_incarnation(tmp_path):
    mirror, store, path, hr = fixture(tmp_path)
    mirror.reconcile(OWNER)
    snap, request = snap_and_request(mirror)
    op = decide(store, store.requests(OWNER)[0])
    store.retire_operation(
        OWNER, op["operationId"], by="system", reason=MISMATCH, evidence="x"
    )
    nxt = mirror._current_request(OWNER, "default", snap)
    assert nxt is not None and nxt["requestId"] == mirror._request(
        OWNER, "default", snap, 1
    )["requestId"]


def test_revised_request_with_recorded_decision_is_not_withdrawn_or_replaced(tmp_path):
    mirror, store, path, hr = fixture(tmp_path)
    mirror.reconcile(OWNER)
    snap, original = snap_and_request(mirror)
    revised = revise_externally(store, OWNER, store.requests(OWNER)[0])
    store.decide(
        OWNER,
        dict(
            requestId=revised["requestId"],
            expectedRevision=revised["revision"],
            choiceId=revised["choices"][1]["id"],
            revisionText=revised["choices"][1]["label"],
            idempotencyKey="k",
        ),
    )
    assert mirror._current_request(OWNER, "default", snap) is None
    stored = store.get(OWNER, original["requestId"])
    assert stored["state"] in ("open", "needs_context") and stored.get("resolution")


def test_revised_request_that_cannot_be_withdrawn_blocks_new_incarnation(tmp_path):
    mirror, store, path, hr = fixture(tmp_path)
    mirror.reconcile(OWNER)
    snap, original = snap_and_request(mirror)
    revise_externally(store, OWNER, store.requests(OWNER)[0])
    mirror._withdraw = lambda *a, **k: None  # withdrawal did not take effect
    assert mirror._current_request(OWNER, "default", snap) is None


# --- reconcile-binding branches ---------------------------------------------


def test_unchanged_ask_withdraws_projection_when_tip_left_owner(tmp_path):
    mirror, store, path, hr = fixture(tmp_path)
    mirror.reconcile(OWNER)
    binding = mirror.binding(OWNER, store.requests(OWNER)[0]["requestId"])
    mirror.tip = lambda sid: "session-b"
    mirror._reconcile_binding(OWNER, binding)
    assert not open_requests(store, OWNER)


def test_retirement_conflict_that_is_not_settled_propagates(tmp_path, monkeypatch):
    mirror, store, path, hr = fixture(tmp_path)
    mirror.reconcile(OWNER)
    binding = mirror.binding(OWNER, store.requests(OWNER)[0]["requestId"])
    decide(store, store.requests(OWNER)[0])
    with sqlite3.connect(path) as c:
        c.execute(
            "UPDATE tasks SET human_request=?",
            (json.dumps(dict(hr, scope="changed")),),
        )

    def refuse(*a, **k):
        raise core.ActionError("busy", 409)

    monkeypatch.setattr(store, "retire_operation", refuse)
    with pytest.raises(core.ActionError, match="busy"):
        mirror._reconcile_binding(OWNER, binding)


def test_withdrawn_request_is_not_touched_by_binding_reconcile(tmp_path, monkeypatch):
    mirror, store, path, hr = fixture(tmp_path)
    mirror.reconcile(OWNER)
    rid = store.requests(OWNER)[0]["requestId"]
    binding = mirror.binding(OWNER, rid)
    mirror._withdraw(OWNER, store.get(OWNER, rid))
    assert store.get(OWNER, rid)["state"] == "withdrawn"
    with sqlite3.connect(path) as c:
        c.execute(
            "UPDATE tasks SET human_request=?",
            (json.dumps(dict(hr, scope="changed")),),
        )
    touched = []
    for name in ("withdraw", "retire_operation"):
        real = getattr(store, name)
        monkeypatch.setattr(
            store, name, lambda *a, _r=real, _n=name, **k: touched.append(_n) or _r(*a, **k)
        )
    mirror._reconcile_binding(OWNER, binding)
    assert touched == []
