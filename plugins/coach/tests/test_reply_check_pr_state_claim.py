"""pr_state_claim (self-heal agent-36ec41dc12): a final reply that states a PR's present state ("isn't merged yet",
"still open") while this turn never read that PR is nudged deterministically, before the judge.

Incident: at 16:45 PST a chat told David "PR #1750 ... isn't merged yet". It had merged at 16:35 and was live on the
hub from 16:37. The reply came from a `gh pr view 1750` taken at 16:24, before two Hermes-update pauses (the second
was this PR landing). INCIDENT_REPLY paraphrases that reply; the claim sentence keeps its shape.
"""
import importlib.util
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("t_reply_check_pr_state_claim", HERE / "reply_check.py")
rc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rc)

INCIDENT_REPLY = (
    "PR #1750 (the pause-note fix) isn't merged yet: it's waiting on the required checks. "
    "I'll tell you when it lands on the hub.")

# The resumed turn read the card and a log, not the PR.
NO_READ_LOG = [
    "-> kanban_show({\"task_id\": \"t_01a1ce0000000002\"})",
    "-> terminal(tail -20 ~/.hermes/logs/agent.log)",
]


def _no_judge(system, prompt):
    raise AssertionError("pr_state_claim must not need the LLM judge")


def _quiet_judge(system, prompt):
    return '{"handoff":0,"confirm":0,"unwatched":0,"report_only":0,"status_only":0,"evidence":""}'


def test_incident_reply_with_no_pr_read_is_flagged_before_the_judge():
    hit = rc.check(INCIDENT_REPLY, "is 1750 in?", NO_READ_LOG, _no_judge, only=["pr_state_claim"])
    assert hit and hit["rule"] == "pr_state_claim" and hit["p"] == 1.0
    assert "isn't merged yet" in hit["evidence"]
    assert "gh pr view" in hit["nudge"] and "state,mergedAt" in hit["nudge"]


def test_incident_reply_with_empty_tool_log_is_flagged():
    assert rc.pr_state_claim(INCIDENT_REPLY, [])
    assert rc.pr_state_claim(INCIDENT_REPLY, None)


def test_incident_reply_fires_in_the_default_rule_set():
    assert "pr_state_claim" in rc.DEFAULT_RULES and "pr_state_claim" in rc.settings()["rules"]
    hit = rc.check(INCIDENT_REPLY, "is 1750 in?", NO_READ_LOG, _quiet_judge)
    assert hit and hit["rule"] == "pr_state_claim"


def test_same_reply_after_gh_pr_view_is_not_flagged():
    log = NO_READ_LOG + ["-> terminal(token-broker exec --scope user/github -- gh pr view 1750 --json state,mergedAt)"]
    assert rc.pr_state_claim(INCIDENT_REPLY, log) is None


def test_other_pr_state_reads_count():
    for line in ("-> terminal(gh pr checks 1750)",
                 "-> terminal(gh pr list --state open -R daveey/hermes)",
                 "-> terminal(gh api repos/daveey/hermes/pulls/1750)",
                 "-> terminal(~/.hermes/bin/hermes-core-wt ship pause-note --no-ci-wait)",
                 "-> terminal(gh pr merge 1750 --rebase)",
                 "-> skill_view(pr-blocker-check)",
                 "-> tool_call({\"calls\": [{\"name\": \"browser_exec\", \"arguments\": "
                 "{\"code\": \"new_tab('https://github.com/daveey/hermes/pull/1750')\"}}]})"):
        assert rc.pr_state_claim(INCIDENT_REPLY, NO_READ_LOG + [line]) is None, line


def test_help_output_is_not_a_read():
    assert rc.pr_state_claim(INCIDENT_REPLY, NO_READ_LOG + ["-> terminal(gh pr view --help)"])


def test_other_present_state_claims_are_flagged():
    for reply in ("PR 42 is still open; review is pending.",
                  "The fix PR hasn't merged yet.",
                  "PR #88 is not merged yet, so the hub still runs the old code.",
                  "PR 42 is still unmerged.",
                  "The PR is awaiting merge behind the queue.",
                  "PR #88 has not been merged yet."):
        assert rc.pr_state_claim(reply, NO_READ_LOG), reply


def test_non_claims_pass():
    for reply in ("PR #1750 merged as bd8476 at 16:35.",  # past-tense fact
                  "Is PR 42 merged yet?",  # a question
                  "Want me to check whether PR 42 is still open?",  # an offer
                  "`gh pr view 42` said it isn't merged yet.",  # code span
                  "> PR 42 isn't merged yet",  # quote line
                  "You said \"PR 42 isn't merged yet\", but it merged at 16:35.",  # quoted text
                  "The coach's pr_state_claim rule flags replies like that.",  # meta
                  "The branch isn't merged yet.",  # no PR named
                  "PR 42 merged; the deploy isn't done yet.",
                  "PR #1750: my previous message said it wasn't merged yet. That was wrong."):  # the correction
        assert rc.pr_state_claim(reply, NO_READ_LOG) is None, reply


def test_rule_lists_stay_in_step():
    assert "pr_state_claim" in rc.RULES and "pr_state_claim" in rc.BACKTEST_RULES
    src = (HERE / "signals.py").read_text() + (HERE / "self_heal.py").read_text()
    assert src.count('"pr_state_claim"') >= 2
