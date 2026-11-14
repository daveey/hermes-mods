"""This turn's tool log must survive a plugin reload in the middle of the turn (self-heal agent-85e085f99e).

hermes-sync's mods-reload force-reloads every plugin in a running gateway/serve when any linked plugin changes
(16:06:52-16:07:01 UTC on 2026-11-14 it reloaded for a user-steps README edit). The manager purges ask-gate's
package and re-executes it, so a module-level TurnLog started empty, pre_llm_call (which would refill nothing
anyway) does not run again until the next turn, and every explicit human_request of the rest of the turn was
refused as "human_request.evidence matches no tool call made this turn" although the turn had made the calls.
"""
import json

from conftest import _load, llm_returning

EVIDENCE = ("Second and final U14e hosted run 43605081473 (head df76b2bd) stopped at P0 in the hosted Runner "
            "identity step before the executor: `identity-single-run`, inferred by deterministic replay.")


def _request(evidence=EVIDENCE):
    return {"title": "U14e: decide whether to allow a third run", "assignee": "default",
            "initial_status": "blocked",
            "human_request": {"action": "Reply A or B on this card. Unblock = allow a third run; archive = stop.",
                              "why_human": "David capped U14e at two hosted runs; only he can allow a third.",
                              "scope": "One more hosted macos-15 run, <=60 min, <=$5.",
                              "evidence": evidence, "interaction": "reply"}}


def test_tool_log_survives_a_mid_turn_plugin_reload(gate):
    gate.post_tool_call(tool_name="terminal", session_id="s1", args={"command": "grep -n identity NATIVE-RESULT.md"},
                        result=json.dumps({"output": "u14e-43605081473/NATIVE-RESULT-43605081473.md\n5:**Outcome: "
                                           "P0, stop `identity-single-run` (attributed by deterministic replay)"}))
    reloaded = _load()  # what mods-reload's discover_and_load(force=True) does to the running process
    reloaded.LLM = llm_returning([("ok", "real decision only David can make")])
    assert [t["tool"] for t in reloaded.LOG.get("s1")] == ["terminal"]
    assert reloaded.pre_tool_call(tool_name="kanban_create", args=_request(), session_id="s1") is None


def test_reload_keeps_this_turns_user_messages(gate):
    gate.LOG.set_users("s1", ["allow one more run"])
    assert _load().LOG.users("s1") == ["allow one more run"]


def test_next_turn_still_starts_with_an_empty_log(gate):
    gate.LOG.add("s1", "terminal", "ls", "x")
    reloaded = _load()
    reloaded.pre_llm_call(session_id="s1", user_message="next", conversation_history=[], platform="cli")
    assert reloaded.LOG.get("s1") == [] and gate.LOG.get("s1") == []


def test_evidence_quoted_from_the_middle_of_a_long_result_matches(gate):
    """The third retry quoted a sentence from the middle of a 4 kB grep result; the head+tail display clip
    (STORE_CHARS) had cut exactly that sentence out of what the evidence match read."""
    filler = "\n".join(f"{i}:" + "context line about something else entirely " * 9 for i in range(6))
    tail = "\n".join(f"{i}:" + "more unrelated table rows and numbers 0123 " * 9 for i in range(10, 18))
    out = filler + "\n7:The refusal is structural, not environmental. tools/u14e check.\n" + tail
    assert len(out) > 2 * gate.core.STORE_CHARS
    gate.post_tool_call(tool_name="terminal", session_id="s1", args={"command": "grep -n x f.md"},
                        result=json.dumps({"output": out}))
    gate.LLM = llm_returning([("ok", "real decision")])
    assert gate.pre_tool_call(tool_name="kanban_create", session_id="s1",
                              args=_request("The refusal is structural, not environmental.")) is None
    # the stored display copy stays bounded
    assert len(gate.LOG.get("s1")[0]["result"]) <= gate.core.STORE_CHARS
