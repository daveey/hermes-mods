"""A human ask whose action is to deliver text into a Hermes chat is agent work (self-heal agent-c25865e208).

Workers were refused a cross_session send into a Desktop chat, then blocked asking David to type the same line into
that chat. The refusal had already subscribed the chat to the worker's card, so a `FINAL SEND for @session:<ref>:`
line in kanban_complete delivers the text; outside a worker, cross_session sends it directly.
"""
import json

import pytest
from conftest import llm_returning

CANARY = "@session:default/24d53efacb8a4318ad03bc3752c19043"


def human(action, **changes):
    return {"action": action, "why_human": "Kanban workers are refused sending into Desktop chats",
            "scope": "Only the canary runner", "evidence": "cross_session refused Desktop session",
            "interaction": "reply", **changes}


def block(gate, hr=None, reason="Checked: cross_session refused the send", **extra):
    args = {"reason": reason, "kind": "needs_input", **extra}
    if hr is not None:
        args["human_request"] = hr
    return gate.pre_tool_call(tool_name="kanban_block", session_id="s1", args=args)


# The asks from the incident, verbatim or nearly (runners board t_01a185154afb4293, t_f15d182d; hub msg 297814).
DELIVERY_ACTIONS = [
    "In the canary repair chat @session:default/x, send one line asking it to check recorded approval e6c056f2 "
    "with runners_receipt, then diagnose and start the service once.",
    f"Resume the canary repair chat {CANARY} and ask it to verify recorded approval e6c056f2 via runners_receipt, "
    "then diagnose and service_start once.",
    "Send one authorization message in the Desktop repair chat 'Repair runner · hermes-hub-canary-1' (hub session "
    "24d53efacb8a4318ad03bc3752c19043) telling it to run runners_operate diagnose, then service_start once.",
    "In the canary repair chat, send one line asking it to check recorded approval `e6c056f2` with "
    "`runners_receipt`, then diagnose and start the service once.",
    "Type one line into the canary repair chat asking it to run the diagnosis.",
]


@pytest.mark.parametrize("action", DELIVERY_ACTIONS)
def test_structured_ask_to_deliver_text_into_a_chat_is_refused_with_final_send(gate, action):
    gate.LOG.add("s1", "cross_session", {"action": "send", "target": CANARY}, "Refused: a Kanban worker ...")
    r = block(gate, human(action))
    assert r and r["action"] == "block", r
    assert "FINAL SEND for @session:" in r["message"]
    assert "cross_session" in r["message"]


def test_refusal_names_the_exact_session_ref(gate):
    r = block(gate, human(f"Resume the canary repair chat {CANARY} and ask it to verify the receipt."))
    assert f"FINAL SEND for {CANARY}: <text>" in r["message"]


def test_prose_block_asking_david_to_send_into_a_chat_is_refused(gate):
    reason = ("One message from you finishes the canary repair test.\n1) In Hermes Desktop, open the hermes-hub chat "
              "\"Repair runner · hermes-hub-canary-1\" (session 24d53efacb8a4318ad03bc3752c19043).\n2) Send: "
              "\"Authorized: run runners_operate diagnose\".\n3) Unblock this card.\nChecked: cross_session refused")
    r = block(gate, reason=reason)
    assert r and r["action"] == "block" and "FINAL SEND" in r["message"]


def test_blocked_create_asking_to_send_into_a_chat_is_refused(gate):
    r = gate.pre_tool_call(tool_name="kanban_create", session_id="s1", args={
        "title": "Send the repair line", "initial_status": "blocked",
        "human_request": human(f"Open {CANARY} and type one line asking it to run diagnose.")})
    assert r and r["action"] == "block" and "FINAL SEND" in r["message"]


def test_ref_comes_from_the_block_reason_when_the_action_names_none(gate):
    """Real runners/t_01a185154afb4293 block: the action says "the canary repair chat", the reason has the ref."""
    hr = human("Resume the canary repair chat and trigger verification of existing receipt e6c056f2 followed by "
               "scoped diagnose/service_start once; unblock with the execution trace.")
    r = block(gate, hr, reason=f"1) Resume the canary repair chat {CANARY} and ask it to verify approval e6c056f2.")
    assert r and f"FINAL SEND for {CANARY}: <text>" in r["message"]


@pytest.mark.parametrize("reason", [
    # Real blocks the first draft of the detector matched wrongly (hub kanban.db t_f68e984c, t_b11f0591, t_cc5aeb02).
    "Don't paste the token in chat. You can copy it to the clipboard and ask an agent to run the token-broker flow.",
    "(5) Send the final result via cross_session to the origin chat and comment tracker t_0d7a7294.\n"
    "Needed from David:\n(a) Approve the live legacy reconciliation (1), or tell me to use the existing approval.",
    "1) Open Hermes Desktop on the MacBook and reconnect its macbook gateway so @session:default@macbook/x can "
    "receive the G1 handoff. 2) Unblock this card. Resume from published sec/g1=f380ffaa.",
    "Run this card from a Hermes chat on your MacBook (say 'work kanban task t_01a180908368db08').",
])
def test_real_non_delivery_blocks_are_not_matched(gate, reason):
    assert gate.chat_delivery.find_delivery(reason) is None


def test_ask_naming_a_chat_this_session_was_refused_a_send_to_is_refused(gate):
    gate.LOG.add("s1", "cross_session", {"action": "send", "target": "@session:default/abc123def456"},
                 "Refused: a Kanban worker (HERMES_KANBAN_TASK=t_x) cannot send into a human chat")
    r = block(gate, human("Get the abc123def456 conversation to run its diagnosis once, then unblock this card."))
    assert r and r["action"] == "block"
    assert "FINAL SEND for @session:default/abc123def456: <text>" in r["message"]


def test_worker_complete_leftover_asking_david_to_send_into_a_chat_is_refused(gate, monkeypatch):
    monkeypatch.setenv("HERMES_KANBAN_TASK", "t_x")
    r = gate.pre_tool_call(tool_name="kanban_complete", session_id="s1", args={
        "summary": "Merged the fix. David needs to send one line in the canary repair chat asking it to diagnose."})
    assert r and r["action"] == "block" and "FINAL SEND" in r["message"]


def test_worker_complete_that_already_uses_final_send_passes(gate, monkeypatch):
    monkeypatch.setenv("HERMES_KANBAN_TASK", "t_x")
    r = gate.pre_tool_call(tool_name="kanban_complete", session_id="s1", args={
        "summary": f"Merged the fix.\nFINAL SEND for {CANARY}: Please run runners_operate diagnose once."})
    assert r is None


@pytest.mark.parametrize("action", [
    "Open the MacBook lid with Hermes Desktop running.",
    "Reply A or B in this chat. Unblock = build the pick; archive = drop it.",
    "Approve deleting the old backup archive A",
    "Tap Allow on the iPhone prompt",
])
def test_non_delivery_asks_are_not_refused_by_this_check(gate, action):
    gate.LOG.add("s1", "terminal", "backup list", "archive A")
    gate.LLM = llm_returning([("ok", "fine")])
    hr = {"action": action, "why_human": "Only David can", "scope": "one thing",
          "evidence": "terminal backup list returned archive A", "interaction": "approval"}
    r = block(gate, hr)
    assert r is None or "FINAL SEND" not in r.get("message", ""), r


def test_physical_interaction_is_untouched(gate):
    gate.LOG.add("s1", "terminal", "backup list", "archive A")
    gate.LLM = llm_returning([("ok", "fine")])
    hr = human("Open the MacBook lid, then send the canary repair chat one line.", interaction="physical",
               evidence="terminal backup list returned archive A")
    r = block(gate, hr)
    assert r is None or "FINAL SEND" not in r.get("message", "")


def test_detector_fails_open(gate, monkeypatch):
    monkeypatch.setattr(gate.chat_delivery, "find_delivery", lambda *a, **k: 1 / 0)
    gate.LOG.add("s1", "terminal", "backup list", "archive A")
    gate.LLM = llm_returning([("ok", "fine")])
    hr = human(DELIVERY_ACTIONS[0], evidence="terminal backup list returned archive A")
    r = block(gate, hr)
    assert r is None or "FINAL SEND" not in r.get("message", "")


def test_refusal_is_logged(gate, tmp_path):
    block(gate, human(DELIVERY_ACTIONS[1]))
    logs = list(tmp_path.rglob("*.jsonl"))
    assert any("chat_delivery_ask_refused" in p.read_text() for p in logs), [str(p) for p in logs]
