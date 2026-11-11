"""Exercise projection delivery with real core transactions, never a live board."""

import json
from pathlib import Path

import pytest
from core_runtime import require_isolated_core
from test_withdrawal import ROOT, OWNER, core, load_module


@pytest.mark.parametrize("archive", [False, True])
def test_projection_real_core_atomic_transition_and_retry(
    tmp_path, monkeypatch, archive
):
    require_isolated_core()
    from hermes_cli import kanban_db as kb
    from hermes_cli.kanban_db_connect import connect
    from hermes_constants import set_hermes_home_override, reset_hermes_home_override
    from agent.delegation_context import kanban_path_is_fenced

    path = tmp_path / "kanban.db"
    if kanban_path_is_fenced(path):
        pytest.skip("Run this integration in the top-level worker")
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    token = set_hermes_home_override(str(tmp_path))
    try:
        kb.init_db(path)
        c = connect(path)
        hr = dict(
            action="Pick a color. Unblock = resume; archive = abandon.",
            interaction="reply",
            why_human="Only the user knows the required color.",
            scope="Synthetic color selection",
            evidence="Fixture",
        )
        if not archive:
            hr["choices"] = [
                dict(label=x.title(), value=x) for x in ("red", "green", "blue")
            ]
        task = kb.create_task(
            c,
            title="Synthetic color selection",
            assignee="default",
            session_id="session-a",
        )
        assert kb.block_task(
            c, task, kind="needs_input", reason=hr["action"], human_request=hr
        )
        c.close()
        store = core.ActionStore(tmp_path / "actions.sqlite3")
        mod = load_module("real_mirror", ROOT / "kanban_mirror.py")
        mirror = mod.Mirror(
            core,
            store,
            boards=[("default", path)],
            session_exists=lambda sid: sid == "session-a",
            tip=lambda sid: sid,
        )
        mirror.reconcile(OWNER)
        request = store.requests(OWNER)[0]
        ch = request["choices"][1]
        op = store.decide(
            OWNER,
            dict(
                requestId=request["requestId"],
                expectedRevision=1,
                choiceId=ch["id"],
                revisionText=ch["label"],
                idempotencyKey="integration-op",
            ),
        )["operation"]
        receipt = mirror.deliver(op)
        assert mirror.readback(op, receipt)
        # Model a crash after the board commit, before acknowledging the outbox.
        mirror.reconcile(OWNER)
        result = store.dispatch(OWNER, op["operationId"], mirror)
        assert result["operation"]["dispatchState"] == "delivered"
        assert mirror.deliver(op) == receipt
        c = connect(path)
        try:
            assert kb.get_task(c, task).status == ("archived" if archive else "ready")
            comments = c.execute(
                "SELECT body FROM task_comments WHERE task_id=? AND author='user-steps'",
                (task,),
            ).fetchall()
            assert len(comments) == 1
            assert ("archive" if archive else "green") in comments[0][0]
            events = c.execute(
                "SELECT payload FROM task_events WHERE task_id=? AND kind=?",
                (task, "archived" if archive else "unblocked"),
            ).fetchall()
            assert any(
                json.loads(r[0]).get("user_steps_projection") == receipt for r in events
            )
        finally:
            c.close()
    finally:
        reset_hermes_home_override(token)
