"""A card ask is projected once and decisions are fenced to that exact block."""

import json
import sqlite3
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from test_withdrawal import ROOT, OWNER, core, load_module


def fixture(tmp_path):
    path = tmp_path / "kanban.db"
    c = sqlite3.connect(path)
    c.executescript("""
    CREATE TABLE tasks(id TEXT PRIMARY KEY,title TEXT,status TEXT,session_id TEXT,
                       human_request TEXT,idempotency_key TEXT);
    CREATE TABLE task_events(id INTEGER PRIMARY KEY,task_id TEXT,kind TEXT,payload TEXT);
    CREATE TABLE task_comments(id INTEGER PRIMARY KEY,task_id TEXT,author TEXT,body TEXT);
    CREATE TABLE kanban_notify_subs(task_id TEXT,platform TEXT,chat_id TEXT,
                                   notifier_profile TEXT,created_at INTEGER);
    """)
    hr = dict(
        action="Pick one. Unblock = continue; archive = abandon.",
        interaction="reply",
        why_human="Only the user knows",
        scope="Choose a color",
        evidence="test",
        choices=[dict(label=x.title(), value=x) for x in ("red", "green", "blue")],
    )
    c.execute(
        "INSERT INTO tasks VALUES (?,?,?,?,?,NULL)",
        ("t_test", "Choose a color", "blocked", "session-a", json.dumps(hr)),
    )
    c.execute("INSERT INTO task_events(id,task_id,kind) VALUES (1,'t_test','blocked')")
    c.commit()
    c.close()

    def connect(path):
        c = sqlite3.connect(path)
        c.row_factory = sqlite3.Row
        return c

    @contextmanager
    def write_txn(c):
        c.execute("BEGIN IMMEDIATE")
        try:
            yield
            c.commit()
        except BaseException:
            c.rollback()
            raise

    def comment(c, tid, author, body):
        c.execute(
            "INSERT INTO task_comments(task_id,author,body) VALUES (?,?,?)",
            (tid, author, body),
        )

    def move(c, tid, status, *, precondition, comment, event_extra):
        assert not c.in_transaction, "core APIs own their transaction"
        with write_txn(c):
            if precondition(c):
                return False
            c.execute(
                "INSERT INTO task_comments(task_id,author,body) VALUES (?,?,?)",
                (tid, *comment),
            )
            c.execute(
                "UPDATE tasks SET status=?,human_request=NULL WHERE id=?", (status, tid)
            )
            c.execute(
                "INSERT INTO task_events(task_id,kind,payload) VALUES (?,?,?)",
                (
                    tid,
                    "archived" if status == "archived" else "unblocked",
                    json.dumps(event_extra),
                ),
            )
        return True

    api = SimpleNamespace(
        connect=connect,
        write_txn=write_txn,
        add_comment=comment,
        unblock_task=lambda c, t, **kw: move(c, t, "ready", **kw),
        archive_task=lambda c, t, **kw: move(c, t, "archived", **kw),
    )
    store = core.ActionStore(tmp_path / "actions.sqlite3")
    mod = load_module("kanban_mirror_test", ROOT / "kanban_mirror.py")
    mirror = mod.Mirror(
        core,
        store,
        boards=[("default", path)],
        backend=api,
        session_exists=lambda sid: sid in ("session-a", "session-b"),
        tip=lambda sid: sid,
    )
    return mirror, store, path, hr


def decide(store, request, index=1):
    ch = request["choices"][index]
    return store.decide(
        OWNER,
        dict(
            requestId=request["requestId"],
            expectedRevision=1,
            choiceId=ch["id"],
            revisionText=ch["label"],
            idempotencyKey="test-operation",
        ),
    )["operation"]


def test_project_three_choices_once_and_deliver_exact_value(tmp_path):
    mirror, store, path, hr = fixture(tmp_path)
    mirror.reconcile(OWNER)
    mirror.reconcile(OWNER)
    requests = store.requests(OWNER)
    assert len(requests) == 1
    request = requests[0]
    assert [c["label"] for c in request["choices"]] == ["Red", "Green", "Blue"]
    op = decide(store, request)
    receipt = mirror.deliver(op)
    assert mirror.readback(op, receipt)
    assert mirror.deliver(op) == receipt
    with sqlite3.connect(path) as c:
        assert c.execute("SELECT status FROM tasks").fetchone()[0] == "ready"
        comments = c.execute("SELECT body FROM task_comments").fetchall()
    assert len(comments) == 1 and "green" in comments[0][0]


@pytest.mark.parametrize("status", ["ready", "archived", "done"])
def test_departed_block_withdraws_request(tmp_path, status):
    mirror, store, path, hr = fixture(tmp_path)
    mirror.reconcile(OWNER)
    with sqlite3.connect(path) as c:
        c.execute("UPDATE tasks SET status=?", (status,))
    mirror.reconcile(OWNER)
    assert store.requests(OWNER) == []
    assert store.requests(OWNER, include_withdrawn=True)[0]["state"] == "withdrawn"


def test_changed_ask_rejects_old_decision_and_publishes_new(tmp_path):
    mirror, store, path, hr = fixture(tmp_path)
    mirror.reconcile(OWNER)
    old = store.requests(OWNER)[0]
    hr["choices"][1]["value"] = "different-value"
    with sqlite3.connect(path) as c:
        c.execute("UPDATE tasks SET human_request=?", (json.dumps(hr),))
    op = decide(store, old)
    with pytest.raises(ValueError, match="changed|stale"):
        mirror.deliver(op)
    with sqlite3.connect(path) as c:
        assert c.execute("SELECT status FROM tasks").fetchone()[0] == "blocked"
        assert not c.execute("SELECT * FROM task_comments").fetchall()
    mirror.reconcile(OWNER)
    assert (
        len(store.requests(OWNER)) == 2
    )  # recorded stale decision retained, new ask distinct


def test_unblock_reblock_same_ask_gets_new_generation(tmp_path):
    mirror, store, path, hr = fixture(tmp_path)
    mirror.reconcile(OWNER)
    first = store.requests(OWNER)[0]
    with sqlite3.connect(path) as c:
        c.execute(
            "INSERT INTO task_events(id,task_id,kind) VALUES (2,'t_test','unblocked')"
        )
        c.execute(
            "INSERT INTO task_events(id,task_id,kind) VALUES (3,'t_test','blocked')"
        )
    mirror.reconcile(OWNER)
    assert len(store.requests(OWNER)) == 1
    assert store.requests(OWNER)[0]["requestId"] != first["requestId"]


@pytest.mark.parametrize("interaction", ["reply", "approval", "user_terminal"])
def test_fallback_choices_and_archive(tmp_path, interaction):
    mirror, store, path, hr = fixture(tmp_path)
    hr.pop("choices")
    hr["interaction"] = interaction
    with sqlite3.connect(path) as c:
        c.execute("UPDATE tasks SET human_request=?", (json.dumps(hr),))
    mirror.reconcile(OWNER)
    request = store.requests(OWNER)[0]
    assert [c["label"] for c in request["choices"]] == ["Unblock", "Archive"]
    assert "No commands run from this panel" in request["scope"]["markdown"]
    if interaction == "user_terminal":
        assert "without a terminal result" in request["scope"]["markdown"]
    mirror.deliver(decide(store, request))
    with sqlite3.connect(path) as c:
        assert c.execute("SELECT status FROM tasks").fetchone()[0] == "archived"


def test_foreign_session_and_dedicated_metadata_cards_do_not_project(tmp_path):
    mirror, store, path, hr = fixture(tmp_path)
    mirror.reconcile(dict(OWNER, storedSessionId="session-b"))
    assert not store.requests(dict(OWNER, storedSessionId="session-b"))
    with sqlite3.connect(path) as c:
        c.execute("UPDATE tasks SET idempotency_key='user-steps:decision-card:example'")
    mirror.reconcile(OWNER)
    assert not store.requests(OWNER)


def test_forged_unbound_request_cannot_deliver(tmp_path):
    mirror, store, path, hr = fixture(tmp_path)
    from test_withdrawal import complete_request

    request = store.create(OWNER, complete_request())
    with pytest.raises(ValueError, match="binding|projection"):
        mirror.deliver(decide(store, request, 0))


def test_desktop_queue_routes_card_decision_and_withdraws_on_archive(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    mirror, store, path, hr = fixture(tmp_path)
    api = load_module("mirror_api", ROOT / "dashboard" / "plugin_api.py")
    api.RETRY_INTERVAL_S = 0
    app = FastAPI()
    app.include_router(
        api.create_router(
            ledger_dir=tmp_path / "ledger",
            state_db=tmp_path / "missing.db",
            action_store=store,
            owner_resolver=lambda sid: dict(OWNER, storedSessionId=sid),
            kanban_boards=[("default", path)],
            kanban_mirror=mirror,
        )
    )
    client = TestClient(app)
    requests = client.get("/requests", params={"session": "session-a"}).json()[
        "requests"
    ]
    assert len(requests) == 1
    request = requests[0]
    payload = dict(
        session="session-a",
        expectedRevision=1,
        choiceId="card-2",
        revisionText="Blue",
        idempotencyKey="api-blue",
    )
    response = client.post(f"/requests/{request['requestId']}/decisions", json=payload)
    assert response.status_code == 200, response.text
    assert response.json()["operation"]["dispatchState"] == "delivered"
    with sqlite3.connect(path) as c:
        assert c.execute("SELECT status FROM tasks").fetchone()[0] == "ready"
        c.execute(
            "UPDATE tasks SET status='blocked',human_request=?", (json.dumps(hr),)
        )
        c.execute(
            "INSERT INTO task_events(id,task_id,kind) VALUES (NULL,'t_test','blocked')"
        )
    requests = client.get("/requests", params={"session": "session-a"}).json()[
        "requests"
    ]
    assert len([r for r in requests if r["state"] == "open"]) == 1
    with sqlite3.connect(path) as c:
        c.execute("UPDATE tasks SET status='archived'")
    requests = client.get("/requests", params={"session": "session-a"}).json()[
        "requests"
    ]
    assert not any(r["state"] == "open" for r in requests)


def test_missing_board_does_not_break_queue_or_invent_withdrawal(tmp_path):
    mirror, store, path, hr = fixture(tmp_path)
    mirror.reconcile(OWNER)
    before = store.requests(OWNER)
    mirror.boards = {}
    mirror.reconcile(OWNER)
    assert store.requests(OWNER) == before
