"""An INCOMPLETE worker that needs David to start an owner-only terminal step (source card t_01a1c97761c390f1).

Observed (~/.hermes/logs/ask-gate.jsonl chat_delivery_ask_refused at 1794389072 and 1794390741): run_in_user_terminal
and cross_session were refused, the worker filed kanban_block(needs_input) whose action named the owner chat, and
ask-gate's refusal said "kanban_complete with FINAL SEND". The card could not complete (acceptance unmet), so the
worker looped. Core's closed owner-terminal template (hermes_cli.kanban_block_policy) is the supported ask; ask-gate
must accept it and its refusal must name it. No gate is relaxed: naming a chat in the action is still refused.
"""
import json

import pytest
from conftest import llm_returning

CHAT = "@session:default/20261111_032644_40d5be"
TASK = "t_01a1c97761c390f1"
SCOPE = ("Host hermes-hub, cwd /home/ubuntu/.hermes/kanban/workspaces/t_01a1c97761c390f1/owner-proof-v2: run "
         "`sha256sum -c FILES.sha256` then `tests/run_real_release_lifecycle.sh`; isolated fixtures, no activation. "
         f"David starts it from his own chat ({CHAT}).")


def ask(gate, **changes):
    chat = gate.chat_delivery
    return {"interaction": "user_terminal", "action": chat.owner_terminal_action(TASK),
            "why_human": chat.OWNER_TERMINAL_REASON, "evidence": chat.OWNER_TERMINAL_EVIDENCE, "scope": SCOPE, **changes}


def refuse_both(gate):
    gate.LOG.add("s1", "run_in_user_terminal", {"command": "x"},
                 "run_in_user_terminal refused: Kanban caller, owner chats only")
    gate.LOG.add("s1", "cross_session", {"action": "send", "target": CHAT},
                 "Refused: cross_session send refused: Kanban worker cannot send into a human chat.")


def block(gate, hr, kind="needs_input"):
    return gate.pre_tool_call(tool_name="kanban_block", session_id="s1",
                              args={"reason": "Checked: cross_session refused", "kind": kind, "human_request": hr})


@pytest.fixture
def worker(monkeypatch):
    monkeypatch.setenv("HERMES_KANBAN_TASK", TASK)


def test_reproduction_action_naming_the_owner_chat_is_refused_and_names_the_usable_path(gate, worker):
    refuse_both(gate)
    r = block(gate, ask(gate, action=f"Resume {CHAT} and run the native proof. Unblock = resume; archive = drop."))
    assert r and r["action"] == "block"
    msg = r["message"]
    # the old completion-only route stays, scoped to a finished card
    assert f"FINAL SEND for {CHAT}: <text>" in msg and "genuinely finished" in msg
    # corrected guidance: never complete an unfinished card; the template with this task id is the usable path
    assert "never kanban_complete just to send" in msg
    assert gate.chat_delivery.owner_terminal_action(TASK) in msg.replace("\\'", "'") or TASK in msg
    assert gate.chat_delivery.OWNER_TERMINAL_REASON in msg
    assert "interaction: 'user_terminal'" in msg and "kanban_block" in msg


@pytest.mark.parametrize("kind", ["needs_input", "capability"])
@pytest.mark.parametrize("is_worker", [True, False])
def test_canonical_template_passes_a_judge_that_sees_the_whole_request_and_both_refusals(
        gate, monkeypatch, kind, is_worker):
    if is_worker:
        monkeypatch.setenv("HERMES_KANBAN_TASK", TASK)
    refuse_both(gate)
    stub = gate.LLM = llm_returning([("ok", "owner-only initiation")])
    assert block(gate, ask(gate), kind=kind) is None
    prompt = stub.seen[0][1]
    for field in ("action", "why_human", "evidence", "scope", "interaction"):
        assert ask(gate)[field] in prompt
    assert "run_in_user_terminal refused" in prompt and "cross_session" in prompt


def test_a_working_judge_that_rejects_the_scope_still_blocks(gate, worker):
    refuse_both(gate)
    gate.LLM = llm_returning([("unverified", "no exact command", "name the command")])
    r = block(gate, ask(gate, scope="run the recipe"))
    assert r and r["action"] == "block" and "not verified" in r["message"]


def test_unavailable_judge_is_not_counted_as_acceptance_but_does_not_loop(gate, worker):
    refuse_both(gate)
    assert block(gate, ask(gate)) is None  # fixture LLM raises: deterministic checks passed, judge unavailable


def test_chat_ref_in_scope_is_not_judged_as_delivery_but_in_action_it_is(gate, worker):
    refuse_both(gate)
    gate.LLM = llm_returning([("ok", "fine")])
    assert block(gate, ask(gate, scope=f"owner chat {CHAT} runs it")) is None
    r = block(gate, ask(gate, action=ask(gate)["action"] + f" Resume the 20261111_032644_40d5be chat."))
    assert r and "FINAL SEND" in r["message"]


def test_repeated_identical_template_ask_gets_the_same_answer(gate, worker):
    refuse_both(gate)
    gate.LLM = llm_returning([("ok", "fine")])
    assert block(gate, ask(gate)) is None and block(gate, ask(gate)) is None


def test_evidence_must_match_a_tool_call_this_turn(gate, worker):
    """The attestation can't be filed without the refusals actually happening in this turn."""
    r = block(gate, ask(gate))
    assert r and r["action"] == "block" and "matches no tool call" in r["message"]


def test_guidance_outside_a_worker_does_not_offer_the_worker_block_route(gate):
    refuse_both(gate)
    r = block(gate, ask(gate, action=f"Resume {CHAT} and run it."))
    assert r and "cross_session action=send" in r["message"]
    assert "user_terminal" not in r["message"].split("(A Kanban worker")[0]


def test_completion_final_send_still_passes_unchanged(gate, monkeypatch):
    monkeypatch.setenv("HERMES_KANBAN_TASK", TASK)
    r = gate.pre_tool_call(tool_name="kanban_complete", session_id="s1", args={
        "summary": f"Merged the fix.\nFINAL SEND for {CHAT}: Please run it."})
    assert r is None


def test_unfinished_completion_leftover_asking_david_to_relay_is_still_refused(gate, monkeypatch):
    monkeypatch.setenv("HERMES_KANBAN_TASK", TASK)
    r = gate.pre_tool_call(tool_name="kanban_complete", session_id="s1", args={
        "summary": "Source incomplete. David needs to send one line in the canary repair chat asking it to run it."})
    assert r and r["action"] == "block" and "FINAL SEND" in r["message"]


def test_refusal_without_a_valid_task_id_uses_a_placeholder_not_junk(gate):
    chat = gate.chat_delivery
    assert "<task_id>" in chat.refusal(CHAT, worker=True, task_id="../x; rm")
    assert TASK in chat.refusal(CHAT, worker=True, task_id=TASK)


def test_constants_match_core_and_template_is_accepted_by_core_lint(gate):
    policy = pytest.importorskip("hermes_cli.kanban_block_policy")
    chat = gate.chat_delivery
    assert chat.OWNER_TERMINAL_REASON == policy._OWNER_TERMINAL_REASON
    assert chat.OWNER_TERMINAL_EVIDENCE == policy._OWNER_TERMINAL_EVIDENCE
    hr = ask(gate)
    assert policy.owner_only_action_evidenced(hr)
    assert policy.user_ask_lint_error(hr) is None
    assert not policy.owner_only_action_evidenced(ask(gate, action=chat.owner_terminal_action("<task_id>")))
    assert not policy.owner_only_action_evidenced(ask(gate, action=chat.owner_terminal_action("t_xyz")))
    # the true relay is still refused by core
    relay = dict(hr, interaction="reply", action="Ask your agent to run it from your own Hermes chat.")
    assert policy.user_ask_lint_error(relay) is not None
