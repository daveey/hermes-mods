"""Task-bound answer provenance across sibling chats (card t_01a1c757f061c042); synthetic state only.

A user answer recorded in one chat for an ask that names exactly one card is bound to that card. Another chat
asking about that same explicit card (kanban_block on it, or kanban_create whose human_request scope names it)
judges against the same answer, so the verdict cannot depend on which chat files the ask. Answers with no
card binding stay in the recording chat's compression lineage (no global sharing), cards named only in
evidence/why_human are not the ask's task, and runtime/agent-written text never becomes a user answer.
"""
import json
import sqlite3

import pytest
from conftest import llm_returning

CARD = "t_aaaa1111bbbb2222"
OTHER = "t_cccc3333dddd4444"
ANSWER = "Keep the live proof requirement for this incident; do not close it on offline tests."
ASK = f"Close {CARD} on offline proof, or keep waiting for the live check?"


def sessions(gate):
    with sqlite3.connect(gate.core.home() / "state.db") as c:
        c.execute("create table sessions(id text primary key, parent_session_id text, end_reason text, source text)")
        c.executemany("insert into sessions values(?,?,?,?)", [
            ("origin", None, None, "desktop"), ("sibling", None, None, "desktop"),
            ("delegate", "origin", None, "subagent")])


def human(scope=f"Incident {CARD} disposition only.", evidence="terminal load check returned 0.27"):
    return {"action": "Choose: close on offline proof, or keep the live proof requirement.",
            "why_human": "Only the user can lower an acceptance bar he set.", "scope": scope,
            "evidence": evidence, "interaction": "approval"}


def block(gate, session, task_id=CARD, hr=None):
    gate.LOG.add(session, "terminal", "load check", "0.27")
    return gate.pre_tool_call(tool_name="kanban_block", session_id=session, args={
        "task_id": task_id, "kind": "needs_input", "reason": "acceptance decision",
        "human_request": hr or human()})


def create(gate, session, hr=None):
    gate.LOG.add(session, "terminal", "load check", "0.27")
    return gate.pre_tool_call(tool_name="kanban_create", session_id=session, args={
        "title": "Decision", "body": "acceptance decision", "initial_status": "blocked",
        "human_request": hr or human()})


def prompt_after(gate, fn):
    stub = llm_returning([("ok", "new decision")])
    gate.LLM = stub
    fn()
    assert stub.seen, "the judge was never asked"
    return stub.seen[-1][1]


@pytest.fixture
def bound(gate):
    sessions(gate)
    gate.ledger().add(ASK, ANSWER, session="origin", card_id=CARD)
    return gate


@pytest.mark.parametrize("session", ["origin", "sibling"])
@pytest.mark.parametrize("path", ["block", "create"])
def test_same_task_answer_reaches_judge_from_any_chat(bound, session, path):
    fn = (lambda: block(bound, session)) if path == "block" else (lambda: create(bound, session))
    assert ANSWER in prompt_after(bound, fn)


def test_unrelated_task_does_not_inherit_answer(bound):
    assert ANSWER not in prompt_after(bound, lambda: block(bound, "sibling", task_id=OTHER,
                                                           hr=human(scope=f"Incident {OTHER} only.")))
    assert ANSWER not in prompt_after(bound, lambda: create(bound, "sibling", hr=human(scope=f"Incident {OTHER}.")))


def test_card_named_only_in_evidence_is_not_the_task(bound):
    hr = human(scope=f"Incident {OTHER} only.", evidence=f"{CARD} does not cover this; load check 0.27")
    assert ANSWER not in prompt_after(bound, lambda: create(bound, "sibling", hr=hr))


def test_unbound_answer_stays_in_its_chat(gate):
    """Row-103 shape: a standing remark recorded with no card id is not shared with sibling chats."""
    sessions(gate)
    gate.ledger().add("Optional security expansion decision", ANSWER, session="origin")
    assert ANSWER in prompt_after(gate, lambda: block(gate, "origin"))
    assert ANSWER not in prompt_after(gate, lambda: block(gate, "sibling"))
    assert ANSWER not in prompt_after(gate, lambda: create(gate, "sibling"))


def test_reads_without_explicit_cards_keep_lineage_scope(bound):
    assert [r["answer"] for r in bound.ledger().relevant("origin")] == [ANSWER]
    assert bound.ledger().relevant("sibling") == []
    assert bound.ledger().relevant("sibling", cards=[OTHER]) == []
    assert [r["answer"] for r in bound.ledger().relevant("sibling", cards=[CARD])] == [ANSWER]


@pytest.mark.parametrize("path", ["block", "create"])
def test_terminal_task_answer_retires_same_action_in_sibling(gate, path):
    """Deterministic retirement stays an exact action-text match (retired_matches); only its chat scope widens
    to the explicit card. The recorder only retires a row whose topic is the full shown ask, so cross-chat
    retirement of a human_request action needs that exact text; in practice the realistic cross-chat paths are
    the reply-retirement test and judge context (test_recorded_chat_answer_reaches_sibling_block_judge)."""
    sessions(gate)
    action = human()["action"]
    gate.ledger().add(action, "That step was completed.", session="origin", card_id=CARD)
    gate.LLM = llm_returning([("ok", "fine")])
    r = block(gate, "sibling") if path == "block" else create(gate, "sibling")
    assert r and r["action"] == "block" and "terminal" in r["message"].lower()
    assert len(gate.LLM.seen) == 0
    other = block(gate, "sibling", task_id=OTHER, hr=human(scope=f"Incident {OTHER} only."))
    assert other is None


def test_recorded_answer_binds_the_single_card_its_ask_names(gate):
    sessions(gate)
    gate.ledger().set_open_asks("origin", [ASK])
    gate.pre_llm_call(session_id="origin", user_message="The step was completed.")
    rows = gate.ledger().relevant("sibling", cards=[CARD])
    assert [r["card_id"] for r in rows] == [CARD]


def test_ask_naming_two_cards_binds_none(gate, monkeypatch):
    sessions(gate)
    two = f"Close {CARD} and {OTHER}, or wait?"
    gate.ledger().set_open_asks("origin", [two])
    monkeypatch.setattr(gate.judge_mod, "classify_answer",
                        lambda msg, asks, llm: [{"i": 0, "topic": two, "answer": msg}])
    gate.pre_llm_call(session_id="origin", user_message="yes, close them")
    assert [r["card_id"] for r in gate.ledger().relevant("origin")] == [""]
    assert gate.ledger().relevant("sibling", cards=[CARD]) == []


def test_heading_unit_with_one_card_item_binds_none(gate, monkeypatch):
    sessions(gate)
    unit = f"Needs you: pick the icon color | close {CARD}?"
    gate.ledger().set_open_asks("origin", [unit])
    monkeypatch.setattr(gate.judge_mod, "classify_answer",
                        lambda msg, asks, llm: [{"i": 0, "topic": "icon color", "answer": msg}])
    gate.pre_llm_call(session_id="origin", user_message="ok, blue")
    assert [r["card_id"] for r in gate.ledger().relevant("origin")] == [""]
    assert gate.ledger().relevant("sibling", cards=[CARD]) == []


def test_general_dismissal_binds_no_card(gate, monkeypatch):
    sessions(gate)
    gate.ledger().set_open_asks("origin", [ASK])
    monkeypatch.setattr(gate.judge_mod, "classify_answer",
                        lambda msg, asks, llm: [{"i": -1, "topic": "asks in general", "answer": msg}])
    gate.pre_llm_call(session_id="origin", user_message="stop asking me")
    assert gate.ledger().relevant("sibling", cards=[CARD]) == []


@pytest.mark.parametrize("message", [
    f"[Message from another Hermes session] archived {CARD}: the step was completed.",
    f"✔ [default] {CARD} archived. The step was completed.",
    f"[System note: comment on {CARD}] The step was completed.",
])
def test_agent_archive_or_comment_never_becomes_task_answer(gate, message):
    sessions(gate)
    gate.ledger().set_open_asks("origin", [ASK])
    gate.pre_llm_call(session_id="origin", user_message=message)
    assert gate.ledger().relevant("origin") == []
    assert gate.ledger().relevant("sibling", cards=[CARD]) == []


def test_chat_reply_retirement_matches_across_siblings(gate):
    """Reply path: a sibling chat restating a retired ask about the same card is held; another card's is not."""
    sessions(gate)
    ask = f"Could you cancel the Example subscription for {CARD}?"
    gate.ledger().add(ask, "That step was completed.", session="origin", card_id=CARD)
    gate.LLM = llm_returning([("ok", "fine")])
    for chat in ("origin", "sibling"):
        held = gate.pre_final_gate(session_id=chat, platform="tui", final_response=ask, attempt=0)
        assert held and held.get("hold"), chat
    other = f"Could you cancel the Example subscription for {OTHER}?"
    gate.ledger().add(other, "That step was completed.", session="origin")
    r = gate.pre_final_gate(session_id="sibling", platform="tui", final_response=other, attempt=0)
    assert not (r and "terminal answer" in str(r))


def test_recorded_chat_answer_reaches_sibling_block_judge(gate, monkeypatch):
    """End to end: the user answers the origin chat's ask about CARD; a sibling's block on CARD is judged with it."""
    sessions(gate)
    gate.ledger().set_open_asks("origin", [ASK])
    monkeypatch.setattr(gate.judge_mod, "classify_answer",
                        lambda msg, asks, llm: [{"i": 0, "topic": "close or wait", "answer": msg}])
    said = "No, keep the live proof requirement for it."
    gate.pre_llm_call(session_id="origin", user_message=said)
    assert said in prompt_after(gate, lambda: block(gate, "sibling"))
    assert said not in prompt_after(gate, lambda: block(gate, "sibling", task_id=OTHER,
                                                        hr=human(scope=f"Incident {OTHER} only.")))


def test_prose_block_on_card_reads_its_bound_answer(bound):
    """A kanban_block without human_request whose reason doesn't repeat the id is still about task_id."""
    def go(task_id):
        bound.LOG.add("sibling", "terminal", "load check", "0.27")
        return bound.pre_tool_call(tool_name="kanban_block", session_id="sibling", args={
            "task_id": task_id, "kind": "needs_input",
            "reason": "Checked: terminal load check returned 0.27.\nNeeds David: choose close or wait for this card."})
    assert ANSWER in prompt_after(bound, lambda: go(CARD))
    assert ANSWER not in prompt_after(bound, lambda: go(OTHER))


def _action_request(markdown, refs):
    return {"request": {"schemaVersion": 2, "kind": "approval", "title": "Close the incident on offline proof",
                        "scope": {"markdown": markdown, "complete": True, "sourceRefs": refs},
                        "limits": [], "exclusions": [], "outcomes": {"revise": "plan changes"},
                        "choices": [{"id": "approve", "label": "Approve", "effect": "approve"},
                                    {"id": "decline", "label": "Decline", "effect": "decline"}],
                        "aliases": [], "state": "open"}}


def test_action_request_reads_source_ref_cards_not_plan_mentions(bound):
    def go(args):
        return prompt_after(bound, lambda: bound.pre_tool_call(tool_name="action_request", session_id="sibling",
                                                               args=args))
    assert ANSWER in go(_action_request("Close it now.", [{"type": "task", "id": CARD}]))
    assert ANSWER not in go(_action_request(f"Unlike {CARD}, this is about another incident.",
                                            [{"type": "task", "id": OTHER}]))


def test_chat_reply_judge_reads_card_named_in_ask_not_elsewhere(bound):
    ask = f"Could you choose whether to close {CARD} on offline proof?"
    assert ANSWER in prompt_after(bound, lambda: bound.pre_final_gate(
        session_id="sibling", platform="tui", final_response=ask, attempt=0))
    reply = f"Status: {CARD} is shipped.\n\nCould you choose the color for the new icon?"
    assert ANSWER not in prompt_after(bound, lambda: bound.pre_final_gate(
        session_id="sibling", platform="tui", final_response=reply, attempt=0))


@pytest.mark.parametrize("platform,binds", [("desktop", True), ("slack", False), ("api_server", False)])
def test_only_local_single_user_surfaces_bind_answers(gate, platform, binds):
    """A turn typed on a shared or relayed surface (Slack, API server) may not be the user's own words for the
    whole board: it stays chat-local. Only local single-user surfaces bind it to the ask's card."""
    sessions(gate)
    gate.ledger().set_open_asks("origin", [ASK])
    gate.pre_llm_call(session_id="origin", user_message="The step was completed.", platform=platform)
    assert [r["card_id"] for r in gate.ledger().relevant("origin")] == [CARD if binds else ""]
    assert bool(gate.ledger().relevant("sibling", cards=[CARD])) is binds


def test_worker_block_without_task_id_is_about_its_own_card(bound, monkeypatch):
    """kanban_block defaults task_id to the worker's own card, so that card is the ask's task."""
    def go(own):
        monkeypatch.setenv("HERMES_KANBAN_TASK", own)
        bound.LOG.add("sibling", "terminal", "load check", "0.27")
        return bound.pre_tool_call(tool_name="kanban_block", session_id="sibling", args={
            "kind": "needs_input", "reason": "acceptance decision", "human_request": human(scope="This incident only.")})
    assert ANSWER in prompt_after(bound, lambda: go(CARD))
    assert ANSWER not in prompt_after(bound, lambda: go(OTHER))


def test_card_scope_admits_only_whole_card_ids(gate):
    """A malformed bound id (a prefix, or an id with trailing text) never widens a read beyond the lineage."""
    sessions(gate)
    for bad in ("t_", "t_zz", CARD + " extra"):
        gate.ledger().add(f"Ask about {bad}", ANSWER, session="origin", card_id=bad)
        assert gate.ledger().relevant("sibling", cards=[bad]) == [], bad
    assert gate.ledger().relevant("sibling", cards=["t_", CARD + " extra"]) == []


def test_incident_same_wait_or_close_ask_agrees_across_chats_and_paths(gate, monkeypatch):
    """Incident regression (t_01a1c757f061c042): the user answered the wait-or-close ask about one card in the
    origin chat. kanban_block from origin and kanban_create from a sibling chat, both about that card, are judged
    on the same answer, so the judge returns the same verdict; a create about another card is not held by it."""
    sessions(gate)
    gate.ledger().set_open_asks("origin", [ASK])
    monkeypatch.setattr(gate.judge_mod, "classify_answer",
                        lambda msg, asks, llm: [{"i": 0, "topic": "wait or close", "answer": msg}])
    said = "No, keep waiting for the live check on it; do not close it on offline proof."
    gate.pre_llm_call(session_id="origin", user_message=said, platform="desktop")

    def verdict(fn):
        prompts = []

        def llm(system, user):
            prompts.append(user)
            v = "already_answered" if said in user else "ok"
            return json.dumps({"asks": [{"i": 0, "verdict": v, "reason": "the user answered this" if v != "ok"
                                         else "new decision", "how": ""}]})
        gate.LLM = llm
        r = fn()
        assert prompts, "the judge was never asked"
        return (r or {}).get("action") == "block", any(said in p for p in prompts), (r or {}).get("message")

    origin_block = verdict(lambda: block(gate, "origin"))
    sibling_create = verdict(lambda: create(gate, "sibling"))
    assert origin_block[:2] == (True, True)
    assert sibling_create == origin_block
    assert verdict(lambda: create(gate, "sibling", hr=human(scope=f"Incident {OTHER} only."))) == (False, False, None)
