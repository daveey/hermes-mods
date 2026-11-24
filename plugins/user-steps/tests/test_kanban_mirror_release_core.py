"""The Desktop Unblock click releases a spend/scope-stopped card through real core, never a live board."""

import json
import sys
from pathlib import Path

import pytest
from core_runtime import require_isolated_core
from test_withdrawal import ROOT, OWNER, core, load_module

ASK = dict(
    action="Decide how to handle: stopped work. Unblock = release it to finish; archive = leave it.",
    interaction="approval",
    why_human="Only the user can authorize more work on a stopped card.",
    scope="Synthetic stopped card",
    evidence="Fixture",
)


@pytest.fixture
def real(tmp_path, monkeypatch):
    """(kb, connect, board path, action store, mirror module) on an isolated core home."""
    require_isolated_core()
    pytest.importorskip("hermes_cli.kanban_release", reason="this core has no `hermes kanban release`")
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
        store = core.ActionStore(tmp_path / "actions.sqlite3")
        yield kb, connect, path, store, load_module("release_mirror", ROOT / "kanban_mirror.py")
    finally:
        reset_hermes_home_override(token)


def offer(c, task, ask=ASK):
    """``ask`` as core's stranded-park brief files it: stamped with the release it offers."""
    from hermes_cli import kanban_release
    stamp = kanban_release.offer(c, task)
    assert stamp is not None
    return dict(ask, release_target=stamp)


def stopped(kb, c, *, cause="scope_stopped", session="session-a", ask=ASK, stamped=True):
    """A card held in triage by a stop, carrying the stranded-park ask when ``ask`` is given. ``stamped=False``
    is an ask the brief did not file: an agent's, or one from before the release offer existed."""
    task = kb.create_task(c, title="Synthetic stopped card", assignee="default", session_id=session)
    with kb.write_txn(c):
        c.execute("UPDATE tasks SET status='triage' WHERE id=?", (task,))
        kb._append_event(c, task, "spend_budget_triaged",
                         {"reason": "stop", **({"cause": cause} if cause else {})})
        if ask:
            c.execute("UPDATE tasks SET human_request=? WHERE id=?",
                      (json.dumps(offer(c, task, ask) if stamped else ask), task))
    return task


def decision_card(kb, c, root, *, stamped=True):
    """The decision card filed for ``root``'s pending stop, blocked on the brief's ask in session-a."""
    from hermes_cli import kanban_spend_decision as decision
    decision.ensure_producer(c, root)
    card = c.execute("SELECT id FROM tasks WHERE created_by='kanban-spend-budget'").fetchone()[0]
    with kb.write_txn(c):
        c.execute("UPDATE tasks SET status='blocked', session_id='session-a', human_request=? WHERE id=?",
                  (json.dumps(offer(c, card) if stamped else ASK), card))
    return card


@pytest.fixture(autouse=True)
def no_instant_review(monkeypatch):
    """The spend reviewer would take an ownerless budget stop before a decision card is filed."""
    try:
        from hermes_cli import kanban_spend_reviewer as reviewer
    except ImportError:
        return
    monkeypatch.setattr(reviewer, "queue", lambda *a, **kw: False)


def click(mod, store, path, label="Unblock"):
    mirror = mod.Mirror(core, store, boards=[("default", path)],
                        session_exists=lambda sid: sid == "session-a", tip=lambda sid: sid)
    mirror.reconcile(OWNER)
    request = store.requests(OWNER)[0]
    choice = next(ch for ch in request["choices"] if ch["label"] == label)
    op = store.decide(OWNER, dict(requestId=request["requestId"], expectedRevision=1, choiceId=choice["id"],
                                  revisionText=choice["label"], idempotencyKey="release-op"))["operation"]
    return mirror, op


def status(c, task):
    return c.execute("SELECT status FROM tasks WHERE id=?", (task,)).fetchone()[0]


def mandates(c, task):
    return [r[0] for r in c.execute(
        "SELECT body FROM task_comments WHERE task_id=? AND body LIKE 'Released to finish%'", (task,))]


def test_default_unblock_on_a_scope_stopped_card_releases_it(real):
    kb, connect, path, store, mod = real
    c = connect(path)
    try:
        task = stopped(kb, c)
        mirror, op = click(mod, store, path)
        receipt = mirror.deliver(op)
        assert receipt["released"] == task and receipt["outcome"] == "released"
        assert receipt["operationId"] == op["operationId"] and receipt["value"] == "unblock"
        assert mirror.readback(op, receipt) and mirror.deliver(op) == receipt  # a retry writes nothing more
        assert status(c, task) == "ready"
        (mandate,) = mandates(c, task)
        assert mandate.endswith("Desktop Unblock, operation " + op["operationId"])
        # Recorded as the human act the click is, stated by the caller, not read from the environment.
        writers = [json.loads(r[0]).get("writer_kind") for r in c.execute(
            "SELECT payload FROM task_events WHERE task_id=? AND kind='commented'", (task,))]
        assert "human" in writers
        renewed = json.loads(c.execute("SELECT payload FROM task_events WHERE task_id=? AND kind='scope_renewed'",
                                       (task,)).fetchone()[0])
        assert renewed["authority_kind"] == "human"
        assert c.execute("SELECT 1 FROM task_events WHERE task_id=? AND kind='promoted_manual'", (task,)).fetchone()
    finally:
        c.close()


def test_default_unblock_on_a_decision_card_releases_its_root(real):
    kb, connect, path, store, mod = real
    from hermes_cli import kanban_spend_decision as decision
    c = connect(path)
    try:
        root = stopped(kb, c, cause=None, session=None, ask=None)
        card = decision_card(kb, c, root)
        mirror, op = click(mod, store, path)
        receipt = mirror.deliver(op)
        assert receipt["released"] == root and mirror.readback(op, receipt)
        assert status(c, root) == "ready" and status(c, card) == "archived"
        assert len(mandates(c, root)) == 1 and not mandates(c, card)
    finally:
        c.close()


def test_custom_choice_ask_is_unchanged(real, monkeypatch):
    kb, connect, path, store, mod = real
    from hermes_cli import kanban_release
    monkeypatch.setattr(kanban_release, "release",
                        lambda *a, **kw: pytest.fail("a custom-choice answer must not release"))
    c = connect(path)
    try:
        ask = dict(ASK, choices=[dict(label="Unblock", value="unblock"), dict(label="Later", value="later")])
        task = stopped(kb, c, ask=ask)
        mirror, op = click(mod, store, path)
        with pytest.raises(core.ActionError):  # today's behavior: core's unblock refuses a pending decision
            mirror.deliver(op)
        assert status(c, task) == "triage" and not mandates(c, task)
    finally:
        c.close()


def _without_release(real_api, monkeypatch, package):
    """The mirror's core API as an older core gives it: the release import fails inside _api."""
    with monkeypatch.context() as patch:
        patch.delattr(package, "kanban_release", raising=False)
        patch.setitem(sys.modules, "hermes_cli.kanban_release", None)
        return real_api()


def test_core_without_release_keeps_todays_unblock(real, monkeypatch):
    kb, connect, path, store, mod = real
    import hermes_cli
    c = connect(path)
    try:
        task = stopped(kb, c)
        mirror, op = click(mod, store, path)
        # From here the import fails, as on a core that predates release: the mirror must not need it.
        mirror._api = lambda real=mirror._api: _without_release(real, monkeypatch, hermes_cli)
        assert mirror._api().release is None
        with pytest.raises(core.ActionError):
            mirror.deliver(op)
        assert status(c, task) == "triage" and not mandates(c, task)
        assert not c.execute("SELECT 1 FROM task_events WHERE task_id=? AND kind='scope_renewed'", (task,)).fetchone()
    finally:
        c.close()


def test_safety_refusal_leaves_the_ask_and_records_the_reason(real):
    kb, connect, path, store, mod = real
    c = connect(path)
    try:
        task = stopped(kb, c)  # the ask offered the release; the provider refusal arrived afterwards
        episode = offer(c, task)["release_target"]["episode"]
        kb._append_event(c, task, "decision_hold_restricted", {"cause": "safety_refusal", "episode": episode})
        c.commit()
        mirror, op = click(mod, store, path)
        for _ in range(2):  # a redelivery of the same operation does not repeat the comment
            with pytest.raises(core.ActionError, match="safety_restricted"):
                mirror.deliver(op)
        row = c.execute("SELECT status, human_request FROM tasks WHERE id=?", (task,)).fetchone()
        assert row[0] == "triage" and json.loads(row[1])["action"] == ASK["action"] and not mandates(c, task)
        notes = [r[0] for r in c.execute(
            "SELECT body FROM task_comments WHERE task_id=? AND author='user-steps'", (task,))]
        assert len(notes) == 1 and "did not release" in notes[0] and "safety_restricted" in notes[0]
        assert op["operationId"] in notes[0]
    finally:
        c.close()


def test_default_unblock_on_a_card_no_stop_holds_is_todays_unblock(real):
    kb, connect, path, store, mod = real
    c = connect(path)
    try:
        task = kb.create_task(c, title="Plain ask", assignee="default", session_id="session-a")
        assert kb.block_task(c, task, kind="needs_input", reason=ASK["action"], human_request=ASK)
        mirror, op = click(mod, store, path)
        receipt = mirror.deliver(op)
        assert "released" not in receipt and status(c, task) == "ready" and not mandates(c, task)
        assert c.execute("SELECT 1 FROM task_events WHERE task_id=? AND kind='unblocked'", (task,)).fetchone()
    finally:
        c.close()


@pytest.mark.parametrize("on_decision_card", [False, True])
def test_retry_after_a_crash_between_release_and_receipt_records_the_receipt(real, on_decision_card):
    kb, connect, path, store, mod = real
    from hermes_cli import kanban_release
    from hermes_cli import kanban_spend_decision as decision
    c = connect(path)
    try:
        if on_decision_card:
            root = stopped(kb, c, cause=None, session=None, ask=None)
            bound = decision_card(kb, c, root)
        else:
            root = bound = stopped(kb, c)
        mirror, op = click(mod, store, path)
        # The first delivery released the card and died before its receipt was written.
        out = kanban_release.release(c, bound, writer=kanban_release.human_writer("desktop-operation:x"),
                                     author="user-steps", note="Desktop Unblock, operation " + op["operationId"])
        assert out["outcome"] == "released" and status(c, root) == "ready"
        assert mirror._receipt(c, mirror._bound(op), op) is None
        receipt = mirror.deliver(op)
        assert receipt["released"] == root and receipt["outcome"] == "released"
        assert mirror.readback(op, receipt) and mirror.deliver(op) == receipt
        assert len(mandates(c, root)) == 1  # nothing was released twice
        # Another operation's note is not this click's release: no receipt is invented for it.
        assert kanban_release.released_by(c, bound, "Desktop Unblock, operation someone-else") is None
    finally:
        c.close()


@pytest.mark.parametrize("author", ["agent", "old_text"])
def test_an_ask_that_did_not_offer_a_release_keeps_the_plain_unblock(real, monkeypatch, author):
    """An agent-written no-choices ask and an ask filed before the release offer existed carry no stamp: the
    click is today's unblock (core refuses it on a pending spend decision) and nothing is released."""
    kb, connect, path, store, mod = real
    from hermes_cli import kanban_release
    monkeypatch.setattr(kanban_release, "release",
                        lambda *a, **kw: pytest.fail("an ask without the offer must not release"))
    ask = dict(ASK, action="Unblock = I archive the root card." if author == "agent" else
               "Decide how to handle: stopped work. Unblock = ask the owner to record a bounded decision; "
               "the button cannot release this hold")
    c = connect(path)
    try:
        task = stopped(kb, c, ask=ask, stamped=False)
        mirror, op = click(mod, store, path)
        with pytest.raises(core.ActionError):
            mirror.deliver(op)
        assert status(c, task) == "triage" and not mandates(c, task)
    finally:
        c.close()


def test_a_stamp_an_agent_block_supplies_is_dropped_by_core(real):
    kb, connect, path, store, mod = real
    c = connect(path)
    try:
        held = stopped(kb, c, ask=None)
        forged = dict(ASK, release_target=offer(c, held)["release_target"])
        card = kb.create_task(c, title="Agent ask", assignee="default", session_id="session-a")
        assert kb.block_task(c, card, kind="needs_input", reason=ASK["action"], human_request=forged)
        stored = json.loads(c.execute("SELECT human_request FROM tasks WHERE id=?", (card,)).fetchone()[0])
        assert "release_target" not in stored
    finally:
        c.close()


def test_a_stale_decision_card_does_not_release_a_later_stop(real):
    kb, connect, path, store, mod = real
    from hermes_cli import kanban_spend_decision as decision
    c = connect(path)
    try:
        root = stopped(kb, c, cause=None, session=None, ask=None)
        card = decision_card(kb, c, root)
        with kb.write_txn(c):  # promoted, then stopped again: the first stop's decision card is stale
            kb._append_event(c, root, "promoted_manual", {"actor": "someone", "reason": "one more"})
            kb._append_event(c, root, "spend_budget_triaged", {"reason": "stop again"})
        assert decision.pending(c, root)
        mirror, op = click(mod, store, path)
        receipt = mirror.deliver(op)  # today's plain unblock of the decision card itself
        assert "released" not in receipt and status(c, card) != "blocked"
        assert status(c, root) == "triage" and not mandates(c, root) and decision.pending(c, root)
    finally:
        c.close()
