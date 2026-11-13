"""A session_outcome wait that linked nothing is not a wake source (self-heal agent-1b6c276b0a).

A chat declared `session_outcome(kind=wait)` on a card on another gateway. The call returned `ok: true, declared:
true` and put the failure in a side field (`not_linked`). The chat then ended its turn with "I'll watch it directly
until it's done"; nothing woke it and the user had to type "resume". The report_only judge resolved the nudge (p=0.2)
because "a session_outcome wait [was] registered on it", and the `unwatched` prefilter never matched the wording.
Now: the wait's result carries `wakes_chat`, the coach's tool log shows it on an `<- OK wait` line, the judges are
told a `wakes_chat=false` wait wakes nothing, and the prefilter matches "I'll watch ... until".
"""
import importlib.util
import json
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]


def _mod(name):
    spec = importlib.util.spec_from_file_location(f"t_unlinked_{name}", HERE / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


rc = _mod("reply_check")
ar = _mod("agent_rules")

REPLY = "The MacBook fix card t_01a1d139db545db9 is stalled. I'll watch it directly until it's done."
WAIT_ARGS = {"kind": "wait", "on": [{"kind": "card", "id": "t_01a1d139db545db9"}]}
UNLINKED = json.dumps({"ok": True, "declared": True, "linked": [], "wakes_chat": False,
                       "not_linked": ["t_01a1d139db545db9: not on this gateway's boards, so the chat does not show it"],
                       "warning": "Nothing will wake this chat for t_01a1d139db545db9."})
LINKED = json.dumps({"ok": True, "declared": True, "linked": ["t_01a1d139db545db9"], "wakes_chat": True})


def _log(result):
    coach = ar.AgentCoach()
    coach.observe("s", "session_outcome", WAIT_ARGS, result)
    return list(coach.sessions["s"]["turn_log"])


def test_watch_until_reply_reaches_the_unwatched_judge():
    assert "unwatched" in rc.prefilter(REPLY)
    assert "unwatched" not in rc.prefilter("I watched it until it finished; it is merged and deployed.")


def test_unlinked_wait_shows_wakes_chat_false_in_the_tool_log():
    log = _log(UNLINKED)
    assert any(line.startswith("   <- OK wait wakes_chat=false") and "t_01a1d139db545db9" in line for line in log), log


def test_linked_wait_shows_wakes_chat_true():
    log = _log(LINKED)
    assert any(line.startswith("   <- OK wait wakes_chat=true") for line in log), log


def test_wait_without_the_field_adds_no_line():
    # an older session-outcome without wakes_chat: no claim either way
    log = _log(json.dumps({"ok": True, "declared": True}))
    assert not any("<- OK wait" in line for line in log), log


def test_judges_treat_wakes_chat_false_as_no_wake_source():
    clause = rc.UNLINKED_WAIT
    assert "wakes_chat=false" in clause
    assert clause in rc.JUDGE_SYS and clause in rc.REPORT_ONLY_RULE and clause in rc.CONFIRM_SYS
    unwatched = rc.JUDGE_SYS[rc.JUDGE_SYS.index("- unwatched:"):rc.JUDGE_SYS.index("- report_only:")]
    assert clause in unwatched
