"""quota_claim (self-heal agent-f547b2a420): a reply that says model quota is out, accounts are capped or the fleet is
waiting for quota to come back, while this turn read no quota state, is held deterministically, before the judge.

Incident (hub session 20261110_184601_52504d, 2026-11-14): at 18:47Z the chat read the pool (provider walls,
`hermes-pool-llm check`) and correctly said OpenAI was out on all 3 accounts. By 22:34Z two more ChatGPT accounts had
been added and a third had been cleared, so 3 of 5 had headroom. Two turns at 22:34Z whose only calls were user-step
receipts then told David "That leaves only waiting for the weekly quota to come back." David: "there is chatgpt quota".
INCIDENT_REPLY_1/2 are those replies with account names scrubbed.
"""
import importlib.util
import json
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("t_reply_check_quota_claim", HERE / "reply_check.py")
rc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rc)
spec2 = importlib.util.spec_from_file_location("t_agent_rules_quota_claim", HERE / "agent_rules.py")
ar = importlib.util.module_from_spec(spec2)
spec2.loader.exec_module(ar)

INCIDENT_REPLY_1 = (
    "Got it: the personal account stays off for cards, and I haven't changed anything. Your \"No\" is on record.\n\n"
    "That leaves waiting for the weekly quota to come back, which costs nothing. Cards should start running again from "
    "about Nov 15, 14:00Z, when the work account resets. More accounts come back over Nov 17\u201320. The retention "
    "ladder and worker-first rollout cards pick up where they stopped at that point.")
INCIDENT_REPLY_2 = (
    "Got it: no paid pay-per-use access, so nothing that costs money is turned on and I haven't changed anything. Your "
    "\"No\" is on record.\n\nThat leaves only waiting for the weekly quota to come back. Cards should start running "
    "again from about Nov 15, 14:00Z, with more capacity returning Nov 17\u201320.")

# The incident turns' calls (msgs 1293060-1293174): user-step receipts only. No quota read.
INCIDENT_LOG = [
    "-> tool_describe({\"names\": [\"action_request\"]})",
    "-> tool_call({\"calls\": [{\"name\": \"action_request\", \"arguments\": {\"receipt\": {\"operationId\": \"9f74e9b1)",
]

# A pool read that starts past the 140-char `->` cut.
POOL_CMD = ("cd ~; date -u '+%H:%MZ'; sqlite3 -readonly ~/.hermes/kanban.db \"select count(*) from tasks where "
            "status='running'\"; echo; ~/.hermes/plugins/pool-llm/bin/hermes-pool-llm check 2>&1 | tail -4")


def _no_judge(system, prompt):
    raise AssertionError("quota_claim must not need the LLM judge")


def _quiet_judge(system, prompt):
    return '{"handoff":0,"confirm":0,"unwatched":0,"report_only":0,"status_only":0,"evidence":""}'


def test_incident_replies_are_held_before_the_judge():
    for reply in (INCIDENT_REPLY_1, INCIDENT_REPLY_2):
        hit = rc.check(reply, "[user-step answer] No", INCIDENT_LOG, _no_judge, only=["quota_claim"])
        assert hit and hit["rule"] == "quota_claim" and hit["p"] == 1.0, reply
        assert "waiting for the weekly quota to come back" in hit["evidence"]
        assert "hermes auth list" in hit["nudge"] and "hermes-pool-llm check" in hit["nudge"]


def test_incident_reply_fires_in_the_default_rule_set():
    assert "quota_claim" in rc.DEFAULT_RULES and "quota_claim" in rc.settings()["rules"]
    assert "quota_claim" in rc.RULES and "quota_claim" in rc.REPLY_ONLY_RULES
    assert "quota_claim" in rc.NEW_CLAIM_RULES and "quota_claim" in rc.BACKTEST_RULES
    hit = rc.check(INCIDENT_REPLY_2, "[user-step answer] No", INCIDENT_LOG, _quiet_judge)
    assert hit and hit["rule"] == "quota_claim"


def test_quota_reads_count():
    for line in ("-> terminal(cat ~/.hermes/provider_walls.json)",
                 "-> terminal(~/.hermes/plugins/pool-llm/bin/hermes-pool-llm check 2>&1 | tail -4)",
                 "-> terminal(hermes auth list 2>&1 | grep -iA12 codex)",
                 "-> terminal(sqlite3 -readonly ~/.hermes/kanban.db \"select payload from task_events where kind='llm_paced')",
                 "-> terminal(sqlite3 -readonly ~/.hermes/kanban.db \"select payload from task_events where kind='dispatch_skipped')",
                 "-> terminal(grep 'kanban pacing' ~/.hermes/logs/agent.log | tail -3)",
                 "-> terminal(curl -s localhost:9119/api/plugins/account-usage/accounts)",
                 "   <- QUOTA read: hermes-pool-llm check 2>&1 | tail -4"):
        assert rc.quota_claim(INCIDENT_REPLY_2, INCIDENT_LOG + [line]) is None, line


def test_quota_read_past_the_cut_counts_via_its_quota_read_line():
    lines = ar.quota_read_lines(POOL_CMD)
    assert lines and all(l.startswith("   <- QUOTA read: ") for l in lines)
    cut = f"-> terminal({POOL_CMD[:ar.TURN_LOG_ARG_CAP]})"
    assert rc.quota_claim(INCIDENT_REPLY_2, INCIDENT_LOG + [cut]) is not None  # the cut line alone shows no read
    assert rc.quota_claim(INCIDENT_REPLY_2, INCIDENT_LOG + [cut] + lines) is None


def test_quota_read_line_patterns_match_reply_check():
    assert ar.QUOTA_READ_RE.pattern == rc._QC_READ_RE.pattern


def test_other_quota_claims_are_held():
    for reply in ("Nothing is running right now: the system has run out of AI-model quota.",
                  "OpenAI (the default model) is out on all 3 accounts until Nov 20, 22:40Z.",
                  "All the ChatGPT accounts are capped.",
                  "Claude stays held until its weekly caps reset, starting Nov 15.",
                  "6 of the 7 enabled Claude accounts have hit their weekly cap.",
                  "There is no ChatGPT quota left.",
                  "We're waiting on the Codex quota to reset."):
        assert rc.quota_claim(reply, INCIDENT_LOG), reply


def test_non_claims_pass():
    for reply in ("There is ChatGPT quota: 3 of the 5 accounts are available.",
                  "Is the weekly quota back yet?",
                  "If the accounts are capped, cards wait.",
                  "Once the quota comes back, the card resumes.",
                  "The OpenAI accounts were capped until 21:30Z.",
                  "The accounts aren't capped anymore.",
                  "The card that waits for the quota to come back is queued.",
                  "The GitHub rate limit is exhausted until 15:56Z.",
                  "The fix caps each card's spend at $2.",
                  "Cards pinned to a model whose quota is out for 6 hours or more run on another provider.",
                  "What was never observed is a real lookup answered while Claude is exhausted.",
                  "Left unthrottled, Codex would run out in about 11 hours and stay out until the weekly reset.",
                  "quota_claim: that leaves only waiting for the weekly quota to come back."):
        assert rc.quota_claim(reply, INCIDENT_LOG) is None, reply


def test_revision_that_keeps_the_claim_fails_the_outcome():
    out = rc.nudge_outcome("deploy_claim", INCIDENT_REPLY_2, "x", INCIDENT_LOG, _no_judge,
                           extra_rules=("quota_claim",))
    assert out.get("new_claim", {}).get("rule") == "quota_claim"


# ---- self-heal agent-823488ec97: a quota read whose evidence is in the RESULT, not the command ------------------------
# A Mac chat queried a card's task_events (`select ... from task_events where task_id='$id'`): the command names no quota
# source, but the output carried the dispatcher's 04:26 `dispatch_skipped`/`llm_paced` rows with the per-account Codex
# percentages. The reply cited them ("three of the six Codex accounts are at 100% of their weekly quota (as of 04:26
# PST)") and quota_claim still held it as unread, because only tool arguments were scanned.
EVENTS_CMD = ("TZ=America/Los_Angeles date \"+now %H:%M:%S %Z\"; L=~/.hermes/kanban.db; for id in t_a t_b; do "
              "sqlite3 -readonly -separator ' | ' $L \"select datetime(created_at,'unixepoch','-8 hours'), kind, "
              "substr(payload,1,190) from task_events where task_id='$id' order by id desc limit 3\"; done")
EVENTS_OUT = json.dumps({"output": (
    "now 05:28:25 PST\n== [.hermes/kanban.db] t_a | ready | 1000\n"
    "2026-11-17 04:26:38 | dispatch_skipped | {\"reason\": \"llm_paced\", \"detail\": \"openai-codex paced\", "
    "\"providers\": [{\"provider\": \"openai-codex\", \"state\": \"paced\", \"limit\": 1, \"running\": 1}]}\n"
    "2026-11-17 04:26:38 | llm_paced | {\"providers\": [{\"provider\": \"openai-codex\", \"state\": \"paced\", "
    "\"factor\": 0.6976, \"until\": null, \"detail\": \"a@example.com 7d 4% +0.00%/min; b@example.com 7d 100% "
    "+0.00%/min; \n"), "exit_code": 0, "error": None})
EVENTS_REPLY = ("Only the Codex quota limit holds it: one run at a time, because three of the six Codex accounts are "
                "at 100% of their weekly quota (as of 04:26 PST).")


def _turn_log(tool, args, result):
    c = ar.AgentCoach()
    c.turn_start("s1", "[kanban] Task t_a is ready with no worker claiming it.")
    c.observe("s1", tool, args, result)
    return c.turn_context("s1")[1]


def test_quota_state_in_a_tool_result_counts_as_a_read():
    log = _turn_log("terminal", {"command": EVENTS_CMD}, EVENTS_OUT)
    assert any(l.startswith("   <- QUOTA read: ") for l in log), log
    assert rc.quota_claim(EVENTS_REPLY, log) is None
    out = rc.nudge_outcome("denied_ask", EVENTS_REPLY, "x", log, _no_judge, extra_rules=("quota_claim",))
    assert "new_claim" not in out, out


def test_kanban_show_pacing_events_count_as_a_read():
    shown = json.dumps({"task": {"id": "t_a", "body": "Fix the remote notices. " * 40}, "events": [
        {"kind": "llm_paced", "payload": {"providers": [{"provider": "openai-codex", "state": "paced", "detail": "a@example.com 7d 100% +0.00%/min"}]}}]})
    log = _turn_log("kanban_show", {"task_id": "t_a"}, shown)
    assert rc.quota_claim(EVENTS_REPLY, log) is None, log


def test_results_that_only_mention_quota_words_are_not_reads():
    for out in (json.dumps({"output": "plugins/coach/agent_rules.py:2657: QUOTA_READ_RE = ... llm_paced ..."}),
                json.dumps({"output": "Codex accounts at 100% of weekly quota"}),
                json.dumps({"output": "see provider_walls.json and auth.json"})):
        log = _turn_log("terminal", {"command": "grep -n quota plugins/coach/agent_rules.py"}, out)
        assert rc.quota_claim(EVENTS_REPLY, log) is not None, out
    failed = json.dumps({"output": EVENTS_OUT, "exit_code": 1, "error": "boom"})
    log = _turn_log("terminal", {"command": EVENTS_CMD}, failed)
    assert rc.quota_claim(EVENTS_REPLY, log) is not None
