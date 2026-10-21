import json
import time

import pytest

from conftest import FIX, llm_returning

GOOD = FIX["good"]


def tools_of(entry):
    return [{"tool": t["tool"], "args": t["args"], "result": t["result"], "t": 0} for t in entry["tools"]]


def seed_log(gate, sid, entry):
    gate.LOG.reset(sid)
    for t in entry["tools"]:
        gate.LOG.add(sid, t["tool"], t["args"], t["result"])


# ---- detector -------------------------------------------------------------------------------------------
@pytest.mark.parametrize("key", ["I0", "I1_I2", "I3", "I4"])
def test_incident_replies_contain_asks(gate, key):
    assert gate.core.find_asks(FIX[key]["reply"]), key


@pytest.mark.parametrize("key", ["g2", "g3", "g4", "g5", "g6", "g7", "g9"])
def test_good_replies_have_no_asks(gate, key):
    assert gate.core.find_asks(GOOD[key]["reply"]) == []


@pytest.mark.parametrize("text", [
    "Still waiting on you: t_b0192d39.", "Needs you: sign in.", "That is your call.", "Want me to deploy it?",
    "If you'd like me to handle it, I'll cancel it.", "Can you approve the token?", "Cancelling it is up to you.",
    "Add the permission, then unblock the card."])
def test_detects_asks(gate, text):
    assert gate.core.find_asks(text)


@pytest.mark.parametrize("text", [
    "Nothing from this work is waiting on you.", "No action needed from you.", "The filter runs every 5 minutes.",
    "I stopped it and nothing else needs you.", "```\nWant me to run this?\n```", 'I never say "needs David" without checking.'])
def test_ignores_non_asks(gate, text):
    assert gate.core.find_asks(text) == []


def test_heading_absorbs_its_list(gate):
    asks = gate.core.find_asks("Done.\n\n**Needs you:**\n1. Revoke Foo's access.\n2. Cancel the plan.\n\nBye.")
    assert len(asks) == 1 and "Revoke Foo" in asks[0] and "Cancel the plan" in asks[0]


# ---- L2: the four incidents -----------------------------------------------------------------------------
def test_I3_done_card_is_already_done_by_live_status_alone(board, gate):
    board("t_b0192d39", "done", completed=2000.0)
    # the model only sees the follow-on sentence that names no card; the sentence naming the card never reaches it
    stub = llm_returning([("already_done", "same card")])
    gate.LLM = stub
    seed_log(gate, "s1", FIX["I3"])
    r = gate.pre_final_gate(session_id="s1", platform="tui", final_response=FIX["I3"]["reply"],
                            turn_messages=[], attempt=0)
    assert r["hold"] is True
    assert "already_done" in r["nudge"] and "t_b0192d39" in r["nudge"] and "David has NOT seen" in r["nudge"]
    assert "Still waiting on you" not in stub.seen[0][1].split("ASK SENTENCES:")[1].split("FULL DRAFT")[0]
    assert "t_b0192d39: done" in stub.seen[0][1]


def test_I1_I2_unverified_and_agent_can_do_are_held(board, gate):
    board("t_b0192d39", "blocked")
    asks = gate.core.find_asks(FIX["I1_I2"]["reply"])
    stub = llm_returning([("unverified", "nothing checked whether Superhuman still has access", "open connections page"),
                          ("unverified", "subscription state unchecked"),
                          ("agent_can_do", "cf-token-minter can mint Email Routing", "token-broker exec --scope cf-token-minter")][: len(asks)])
    gate.LLM = stub
    seed_log(gate, "s1", FIX["I1_I2"])
    r = gate.pre_final_gate(session_id="s1", platform="tui", final_response=FIX["I1_I2"]["reply"], attempt=0)
    assert r["hold"] and "agent_can_do" in r["nudge"] and "cf-token-minter" in r["nudge"]
    prompt = stub.seen[0][1]
    assert "cf-token-minter" in prompt          # capability sheet reaches the judge
    assert "browser_exec" in prompt             # this turn's tool calls reach the judge
    assert "t_b0192d39: blocked" in prompt      # live card status reaches the judge


def test_I4_repeat_of_answered_ask_sees_ledger_and_user_message(gate):
    gate.ledger().add("Superhuman subscription and access", FIX["I4"]["ledger"][0]["answer"], session="s1")
    gate.LOG.set_users("s1", FIX["I4"]["users"])
    asks = gate.core.find_asks(FIX["I4"]["reply"])
    stub = llm_returning([("already_answered", "he said it was handled")] * len(asks))
    gate.LLM = stub
    r = gate.pre_final_gate(session_id="s1", platform="tui", final_response=FIX["I4"]["reply"], attempt=0)
    assert r["hold"] and "already_answered" in r["nudge"]
    prompt = stub.seen[0][1]
    assert "Superhuman subscription and access" in prompt and "i keep completing it" in prompt


def test_I0_first_summary_is_held(gate):
    asks = gate.core.find_asks(FIX["I0"]["reply"])
    gate.LLM = llm_returning([("unverified", "x")] * len(asks))
    assert gate.pre_final_gate(session_id="s1", platform="tui", final_response=FIX["I0"]["reply"], attempt=0)["hold"]


def test_good_final_replies_pass_untouched(gate):
    for k, e in GOOD.items():
        gate.LLM = llm_returning([("ok", "")] * 5)
        assert gate.pre_final_gate(session_id="s1", platform="tui", final_response=e["reply"], attempt=0) is None, k


def test_ok_verdict_delivers_and_records_for_open_asks(gate):
    gate.LLM = llm_returning([("ok", "real decision")])
    r = gate.pre_final_gate(session_id="s1", platform="tui", final_response="Should I delete the 40 GB of backups?", attempt=0)
    assert r is None and gate.last_verdict("s1") == "ok"
    assert gate.ledger().open_asks("s1") == ["Should I delete the 40 GB of backups?"]


def test_nudge_budget_then_append_note(gate):
    gate.LLM = llm_returning([("unverified", "unchecked premise")])
    text = "Can you confirm the token was revoked?"
    assert gate.pre_final_gate(session_id="s1", platform="tui", final_response=text, attempt=0)["hold"]
    assert gate.pre_final_gate(session_id="s1", platform="tui", final_response=text, attempt=1)["hold"]
    r = gate.pre_final_gate(session_id="s1", platform="tui", final_response=text, attempt=2)
    assert "append" in r and "could not verify" in r["append"]
    assert gate.last_verdict("s1") == "unverified"


def test_judge_down_delivers_and_logs(gate, tmp_path):
    def down(system, user):
        raise RuntimeError("provider down")
    gate.LLM = down
    assert gate.pre_final_gate(session_id="s1", platform="tui", final_response="Should I go ahead?", attempt=0) is None
    log = (tmp_path / "logs" / "ask-gate.jsonl").read_text()
    assert "gate_error" in log and "provider down" in log


def test_garbage_model_output_fails_open(gate):
    gate.LLM = lambda s, u: "not json at all"
    assert gate.pre_final_gate(session_id="s1", platform="tui", final_response="Should I go ahead?", attempt=0) is None


@pytest.mark.parametrize("kw", [dict(platform="cron"), dict(platform="subagent")])
def test_non_chat_platforms_skipped(gate, kw):
    assert gate.pre_final_gate(session_id="s1", final_response="Should I go ahead?", attempt=0, **kw) is None


def test_worker_and_off_switch_skipped(gate, monkeypatch):
    monkeypatch.setenv("HERMES_KANBAN_TASK", "t_12345678")
    assert gate.pre_final_gate(session_id="s1", platform="tui", final_response="Should I go ahead?", attempt=0) is None
    monkeypatch.delenv("HERMES_KANBAN_TASK")
    monkeypatch.setenv("ASK_GATE_OFF", "1")
    assert gate.pre_final_gate(session_id="s1", platform="tui", final_response="Should I go ahead?", attempt=0) is None


def test_tools_come_from_turn_messages_too(gate):
    msgs = [{"role": "assistant", "tool_calls": [{"id": "c1", "function": {"name": "terminal", "arguments": '{"command":"kanban show t_1"}'}}]},
            {"role": "tool", "tool_call_id": "c1", "content": "status: done"}]
    stub = llm_returning([("ok", "")])
    gate.LLM = stub
    gate.pre_final_gate(session_id="s1", platform="tui", final_response="Should I go ahead?", turn_messages=msgs, attempt=0)
    assert "status: done" in stub.seen[0][1]


# ---- L1 -------------------------------------------------------------------------------------------------
def test_l1_block_lists_cards_and_ledger(board, gate):
    board("t_b0192d39", "done", title="Remove Superhuman", completed=1_792_620_000.0)
    board("t_aaaaaaaa", "blocked", title="Other", created=1100.0)
    board("t_zzzzzzzz", "done", session="other-chat")
    gate.ledger().add("Superhuman", "already revoked", session="s0")
    out = gate.pre_llm_call(session_id="s1", user_message="hi", conversation_history=[], platform="tui")
    ctx = out["context"]
    assert "t_b0192d39 [done" in ctx and "t_aaaaaaaa [blocked" in ctx and "t_zzzzzzzz" not in ctx
    assert "Superhuman -> already revoked" in ctx


def test_l1_silent_when_nothing_to_say(gate):
    assert gate.pre_llm_call(session_id="s1", user_message="hi", conversation_history=[], platform="tui") is None


def test_l1_skips_workers(board, gate, monkeypatch):
    board("t_b0192d39", "done")
    monkeypatch.setenv("HERMES_KANBAN_TASK", "t_12345678")
    assert gate.pre_llm_call(session_id="s1", user_message="hi", platform="tui") is None


# ---- L4 -------------------------------------------------------------------------------------------------
def test_l4_user_answer_is_recorded_and_shown_next_turn(gate):
    gate.ledger().set_open_asks("s1", ["The only open item is cancelling the Superhuman subscription."])
    gate.LLM = lambda s, u: json.dumps({"answered": [{"i": 0, "topic": "cancel Superhuman subscription",
                                                      "answer": "already handled, he keeps completing it"}]})
    out = gate.pre_llm_call(session_id="s1", user_message="superhuman has been revoked for a while, i keep completing it",
                            conversation_history=[], platform="tui")
    assert "cancel Superhuman subscription" in out["context"]
    assert gate.ledger().open_asks("s1") == []
    # cross-session: another chat sees it too
    out2 = gate.pre_llm_call(session_id="s9", user_message="hello", conversation_history=[], platform="tui")
    assert "cancel Superhuman subscription" in out2["context"]


def test_l4_strong_dismissal_recorded_even_if_model_down(gate):
    def down(s, u):
        raise RuntimeError("x")
    gate.LLM = down
    hist = [{"role": "assistant", "content": "Want me to rotate the key?"}]
    gate.pre_llm_call(session_id="s1", user_message="YOU JUST SAID IT WAS CANCELED, WHY DO YOU KEEP ASKING ME",
                      conversation_history=hist, platform="tui")
    assert gate.ledger().recent(60)


def test_l4_no_model_call_for_unrelated_message(gate):
    gate.ledger().set_open_asks("s1", ["Want me to rotate the key?"])
    # boom LLM would raise AssertionError if called
    gate.pre_llm_call(session_id="s1", user_message="build the release", conversation_history=[], platform="tui")
    assert gate.ledger().open_asks("s1")


def test_l4_ignores_kanban_completion_notices(gate):
    gate.ledger().set_open_asks("s1", ["Want me to rotate the key?"])
    gate.pre_llm_call(session_id="s1", user_message="✔ [default] @default Kanban t_1 done — fine, already handled",
                      conversation_history=[], platform="tui")
    assert gate.ledger().open_asks("s1")


# ---- L3 -------------------------------------------------------------------------------------------------
BLOCKED = {"title": "Remove Superhuman", "initial_status": "blocked", "assignee": "default",
           "body": "Needs David to revoke access."}


def test_l3_blocked_card_without_checked_is_refused(gate):
    r = gate.pre_tool_call(tool_name="kanban_create", args=BLOCKED, session_id="s1")
    assert r["action"] == "block" and "Checked:" in r["message"] and "No `Checked:` line" in r["message"]


def test_l3_checked_line_must_match_a_tool_call_this_turn(gate):
    args = {**BLOCKED, "body": BLOCKED["body"] + "\nChecked: terminal gws connections list — Superhuman still present"}
    r = gate.pre_tool_call(tool_name="kanban_create", args=args, session_id="s1")
    assert r["action"] == "block" and "matches no tool call" in r["message"]
    gate.LOG.add("s1", "terminal", {"command": "gws connections list"}, "apps: Superhuman, Zoom")
    gate.LLM = llm_returning([("ok", "verified")] * 4)
    assert gate.pre_tool_call(tool_name="kanban_create", args=args, session_id="s1") is None


def test_l3_the_card_tools_themselves_are_not_evidence(gate):
    gate.LOG.add("s1", "kanban_create", {"title": "x"}, "ok")
    args = {**BLOCKED, "body": "Needs David.\nChecked: kanban_create made a card — it worked fine"}
    assert gate.pre_tool_call(tool_name="kanban_create", args=args, session_id="s1")["action"] == "block"


def test_l3_judge_can_still_refuse_a_matching_checked(gate):
    gate.LOG.add("s1", "terminal", {"command": "token-broker scopes"}, "cf-token-minter auto")
    args = {**BLOCKED, "body": "Needs David to add the Email Routing permission.\nChecked: terminal token-broker scopes — cf-token-minter auto"}
    gate.LLM = llm_returning([("agent_can_do", "mint it with cf-token-minter", "token-broker exec --scope cf-token-minter")] * 4)
    r = gate.pre_tool_call(tool_name="kanban_create", args=args, session_id="s1")
    assert r["action"] == "block" and "agent_can_do" in r["message"] and "cf-token-minter" in r["message"]


def test_l3_kanban_block_needs_checked_unless_waiting_on_a_clock(gate):
    assert gate.pre_tool_call(tool_name="kanban_block", args={"reason": "need David's call"}, session_id="s1")["action"] == "block"
    assert gate.pre_tool_call(tool_name="kanban_block", args={"reason": "waiting on parent", "kind": "dependency"}, session_id="s1") is None
    assert gate.pre_tool_call(tool_name="kanban_block", args={"reason": "soak window", "kind": "time"}, session_id="s1") is None


def test_l3_plain_card_not_about_david_untouched(gate):
    args = {"title": "Build the thing", "body": "Implement X and test it.", "assignee": "default"}
    assert gate.pre_tool_call(tool_name="kanban_create", args=args, session_id="s1") is None


def test_l3_non_blocked_card_saying_needs_david_is_gated(gate):
    args = {"title": "Review (needs David)", "body": "Build it.", "assignee": "default"}
    assert gate.pre_tool_call(tool_name="kanban_create", args=args, session_id="s1")["action"] == "block"


def test_l3_open_asks_plugin_cards_exempt(gate):
    args = {**BLOCKED, "body": "Filed automatically by the `open-asks` plugin: Open question for David."}
    assert gate.pre_tool_call(tool_name="kanban_create", args=args, session_id="s1") is None


def test_l3_other_tools_ignored(gate):
    assert gate.pre_tool_call(tool_name="terminal", args={"command": "ls"}, session_id="s1") is None


def test_l3_judge_down_lets_a_verified_card_through(gate):
    gate.LOG.add("s1", "terminal", {"command": "kanban show t_1"}, "status blocked on credentials")
    def down(s, u):
        raise RuntimeError("x")
    gate.LLM = down
    args = {**BLOCKED, "body": "Needs David.\nChecked: terminal kanban show t_1 — status blocked on credentials"}
    assert gate.pre_tool_call(tool_name="kanban_create", args=args, session_id="s1") is None


# ---- workers ----------------------------------------------------------------------------------------------
@pytest.fixture
def worker(gate, monkeypatch):
    monkeypatch.setenv("HERMES_KANBAN_TASK", "t_12345678")
    return gate


def test_worker_unverified_davids_call_becomes_fyi(worker):
    worker.LLM = llm_returning([("unverified", "never checked")])
    summ = "Done: alias works. Cancelling the Superhuman subscription is David's call. Tests pass."
    r = worker.pre_tool_call(tool_name="kanban_complete", args={"summary": summ}, session_id="w1")
    assert r["action"] == "modify"
    out = r["args"]["summary"]
    assert "FYI (unverified, not an ask for David): Cancelling the Superhuman subscription is David's call." in out
    assert out.startswith("Done: alias works.") and out.endswith("Tests pass.")


def test_worker_verified_ask_survives(worker):
    worker.LOG.add("w1", "terminal", {"command": "stripe subscriptions list"}, "sub_123 active, renews 11-02")
    worker.LLM = llm_returning([("ok", "checked")])
    summ = "Subscription is active and renews Nov 2, so cancelling it is David's call.\nChecked: terminal stripe subscriptions list — sub_123 active"
    assert worker.pre_tool_call(tool_name="kanban_complete", args={"summary": summ}, session_id="w1") is None


def test_worker_judge_ok_without_checked_line_still_fyi(worker):
    worker.LLM = llm_returning([("ok", "fine")])
    r = worker.pre_tool_call(tool_name="kanban_complete", args={"summary": "It is David's call whether to ship."}, session_id="w1")
    assert r["action"] == "modify" and "FYI" in r["args"]["summary"]


def test_worker_handoff_without_leftovers_untouched(worker):
    assert worker.pre_tool_call(tool_name="kanban_complete", args={"summary": "All done, tests pass."}, session_id="w1") is None


def test_worker_kanban_block_needs_checked(worker):
    r = worker.pre_tool_call(tool_name="kanban_block", args={"reason": "needs David to sign in", "kind": "needs_input"}, session_id="w1")
    assert r["action"] == "block"


# ---- helpers ------------------------------------------------------------------------------------------------
def test_checked_matches_rules(gate):
    tools = [{"tool": "terminal", "args": "gh pr list --repo a/b", "result": "no open PRs", "t": 0},
             {"tool": "kanban_create", "args": "x", "result": "y", "t": 0}]
    m = gate.core.checked_matches
    assert m("terminal gh pr list — no open PRs", tools)
    assert m("`gh pr list --repo a/b` returned nothing", tools)
    assert not m("kanban_create made it", tools)
    assert not m("i looked at it", tools)


def test_capability_sheet_sources(tmp_path, gate):
    cfg = tmp_path / "ssh_config"
    cfg.write_text("Host mini-host\n  User x\nHost *\n  ForwardAgent no\nHost a b\n")
    assert gate.core.ssh_hosts(cfg) == ["mini-host", "a", "b"]


def test_capability_sheet_is_generated_from_runtime_sources():
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location("core_fresh", Path(__file__).resolve().parents[1] / "core.py")
    c = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(c)
    c.broker_scopes = lambda: ["cf-token-minter (auto)", "aws.write (approval)"]
    c.ssh_hosts = lambda *a: ["mini-host"]
    c.chrome_accounts = lambda: ["a@example.com"]
    c.vault_entries = lambda: []
    sheet = c.capability_sheet(force=True)
    assert "cf-token-minter (auto)" in sheet and "mini-host" in sheet and "a@example.com" in sheet
    assert "vault entries: none" in sheet
