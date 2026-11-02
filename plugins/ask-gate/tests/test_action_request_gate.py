"""action_request approval/choice asks get the same "already answered / agent can do it" check as other asks.

Defect agent-ebedadaefd: an agent used action_request to ask David to approve sending BB repairs to Astra/Codex,
which he had ordered days earlier in the same chat. ask-gate only judged clarify/kanban_create/kanban_block, so the
approval card was published unjudged and the repairs froze waiting on it.
"""
import json
import sqlite3

from conftest import llm_returning

ORDER = ("why are we only using claude, we also have chatgpt subscriptions we can be using. get bb issue repair "
         "working again. do what's needed, dont give up or wait")


def _state_db(tmp_path, session, users):
    """A state.db holding this chat's user rows (oldest first); far more than the judge's 30-message window."""
    c = sqlite3.connect(tmp_path / "state.db")
    c.execute("create table messages (id integer primary key, session_id text, role text, content text, "
              "display_kind text, active integer default 1, compacted integer default 0, message_uid text)")
    for i, (text, kind) in enumerate(users):
        c.execute("insert into messages (session_id, role, content, display_kind, message_uid) values (?,?,?,?,?)",
                  (session, "user", text, kind, f"u{i}"))
        c.execute("insert into messages (session_id, role, content) values (?,?,?)", (session, "assistant", "ok"))
    c.commit()
    c.close()


def _request(title, markdown, kind="approval"):
    choices = ([{"id": "approve", "label": "Approve", "effect": "approve"},
                {"id": "revise", "label": "Change", "effect": "revise"},
                {"id": "decline", "label": "Decline", "effect": "decline"}] if kind == "approval"
               else [{"id": "a", "label": "Option A", "effect": "answer"}])
    return {"request": {"schemaVersion": 2, "kind": kind, "title": title,
                        "scope": {"markdown": markdown, "complete": True, "sourceRefs": []},
                        "limits": [], "exclusions": [], "outcomes": {"revise": "plan changes"},
                        "choices": choices, "aliases": [], "state": "open"}}


def _eq(request_quote):
    return {"source": "messages", "source_quote": "we also have chatgpt subscriptions we can be using",
            "request_quote": request_quote, "explanation": "David ordered BB repair onto the ChatGPT subscriptions"}


ASTRA = _request("Make BB repairs use Astra",
                 "Route Baby Bear issue-repair workers to openai-codex/gpt-6-astra when Claude usage is over the "
                 "75% repair limit. Max 2 workers, subscription only.")


def test_action_request_already_ordered_in_this_chat_is_blocked(gate, tmp_path):
    rows = [(ORDER, None)] + [(f"unrelated question number {i} about the garden lights", None) for i in range(60)]
    rows += [("[kanban] card t_0123 finished", "kanban_notice"), ("i switched to astra, does it work now", None)]
    _state_db(tmp_path, "s1", rows)
    stub = llm_returning([("already_answered", "David ordered ChatGPT subscriptions be used for BB repair on 10-25", "",
                          _eq("Make BB repairs use Astra"))])
    gate.LLM = stub
    r = gate.pre_tool_call(tool_name="action_request", session_id="s1", args=ASTRA)
    assert r and r["action"] == "block", r
    assert "already_answered" in r["message"] and "do the work" in r["message"].lower()
    prompt = stub.seen[0][1]
    # David's order is 63 user rows back: the judge must still see it (whole session, not the last 30).
    assert "we also have chatgpt subscriptions" in prompt
    assert "Make BB repairs use Astra" in prompt
    assert "card t_0123 finished" not in prompt  # runtime notices are not David's words
    log = (tmp_path / "logs" / "ask-gate.jsonl").read_text()
    assert any(json.loads(line).get("kind") == "action_request_held" and "already_answered" in line
               for line in log.splitlines())


def test_action_request_agent_can_do_is_blocked(gate, tmp_path):
    _state_db(tmp_path, "s1", [("fix the dns record", None)])
    gate.LLM = llm_returning([("agent_can_do", "token-broker cf-infra edits DNS", "use the cf-infra scope")])
    req = _request("Approve editing the Cloudflare DNS record", "Edit the A record for example.com")
    r = gate.pre_tool_call(tool_name="action_request", session_id="s1", args=req)
    assert r and r["action"] == "block"
    assert "agent_can_do" in r["message"]


def test_new_spend_ask_with_no_prior_words_passes(gate, tmp_path):
    _state_db(tmp_path, "s1", [("how is the training run doing?", None)])
    stub = llm_returning([("ok", "a new spend decision David has not made")])
    gate.LLM = stub
    req = _request("Approve renting an 8xH100 node for $400", "Multi-GPU rental above the $25 envelope for 6 hours.")
    assert gate.pre_tool_call(tool_name="action_request", session_id="s1", args=req) is None
    assert len(stub.seen) == 1


def test_withdraw_is_never_judged(gate, tmp_path):
    _state_db(tmp_path, "s1", [(ORDER, None)])
    args = {"withdraw": {"requestId": "r1", "reason": "already directed by David"}}
    assert gate.pre_tool_call(tool_name="action_request", session_id="s1", args=args) is None  # boom LLM not called


def test_choice_kind_is_judged(gate, tmp_path):
    _state_db(tmp_path, "s1", [(ORDER, None)])
    gate.LLM = llm_returning([("already_answered", "he said use chatgpt", "", _eq("Which provider should BB repairs use?"))])
    req = _request("Which provider should BB repairs use?", "Claude or ChatGPT/Codex", kind="choice")
    assert gate.pre_tool_call(tool_name="action_request", session_id="s1", args=req)["action"] == "block"


def test_hold_is_capped_at_two_nudges(gate, tmp_path):
    _state_db(tmp_path, "s1", [(ORDER, None)])
    gate.LLM = llm_returning([("already_answered", "he said use chatgpt", "", _eq("Make BB repairs use Astra"))])
    results = [gate.pre_tool_call(tool_name="action_request", session_id="s1", args=ASTRA) for _ in range(3)]
    assert [bool(r) for r in results] == [True, True, False]


def test_judge_unreachable_fails_open(gate, tmp_path, monkeypatch):
    _state_db(tmp_path, "s1", [(ORDER, None)])

    def down(system, user):
        raise gate.judge_mod.JudgeUnavailable("no route")
    gate.LLM = down
    monkeypatch.setattr(gate, "FALLBACKS", lambda: [])
    assert gate.pre_tool_call(tool_name="action_request", session_id="s1", args=ASTRA) is None
