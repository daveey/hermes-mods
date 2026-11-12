"""merge_forecast (self-heal agent-84aa7db32f): a final reply that tells the user when a PR will merge ("merges once CI
is green") while this turn never checked that PR is nudged deterministically, before the judge.

Incident: a MacBook chat said at 18:47 PT that a PR "merges once CI is green", taking the forecast from the owner
card's plan. Other steps were still open (failures already on main, a fresh review after a rebase, the hub merge
queue); CI went green at 19:17 PT and the PR was still open 7 hours later. INCIDENT_REPLY paraphrases that reply (the
MacBook transcript was not readable from the hub); the forecast sentence keeps its shape.
"""
import importlib.util
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("t_reply_check_merge_forecast", HERE / "reply_check.py")
rc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rc)

INCIDENT_REPLY = (
    "PR1187's owner card restarted after the runner fix and pushed the rebased branch. "
    "It merges once CI is green, then PR1104 goes next.")

# The turn read the owner card, not the PR.
NO_CHECK_LOG = [
    "-> kanban_show({\"task_id\": \"t_01a1ce0000000001\"})",
    "-> terminal(tail -20 ~/.hermes/logs/agent.log)",
]


def _no_judge(system, prompt):
    raise AssertionError("merge_forecast must not need the LLM judge")


def _quiet_judge(system, prompt):
    return '{"handoff":0,"confirm":0,"unwatched":0,"report_only":0,"status_only":0,"evidence":""}'


def test_incident_reply_without_a_pr_check_is_flagged_before_the_judge():
    hit = rc.check(INCIDENT_REPLY, "when does 1187 merge?", NO_CHECK_LOG, _no_judge, only=["merge_forecast"])
    assert hit and hit["rule"] == "merge_forecast" and hit["p"] == 1.0
    assert "merges once CI is green" in hit["evidence"]
    assert "gh pr" in hit["nudge"] and "open step" in hit["nudge"]


def test_incident_reply_fires_in_the_default_rule_set():
    assert "merge_forecast" in rc.DEFAULT_RULES and "merge_forecast" in rc.settings()["rules"]
    hit = rc.check(INCIDENT_REPLY, "when does 1187 merge?", NO_CHECK_LOG, _quiet_judge)
    assert hit and hit["rule"] == "merge_forecast"


def test_same_reply_after_gh_pr_checks_is_not_flagged():
    log = NO_CHECK_LOG + ["-> terminal(token-broker exec --scope user/github -- gh pr checks 1187 -R daveey/hermes)"]
    assert rc.merge_forecast(INCIDENT_REPLY, log) is None


def test_other_pr_state_checks_count():
    for line in ("-> terminal(gh pr view 1187 --json mergeStateStatus,reviewDecision)",
                 "-> terminal(gh api repos/daveey/hermes/pulls/1187)",
                 "-> terminal(gh api repos/daveey/hermes/commits/abc123/check-runs)",
                 "-> skill_view(pr-blocker-check)",
                 "-> tool_call({\"calls\": [{\"name\": \"browser_exec\", \"arguments\": "
                 "{\"code\": \"new_tab('https://github.com/daveey/hermes/pull/1187')\"}}]})"):
        assert rc.merge_forecast(INCIDENT_REPLY, NO_CHECK_LOG + [line]) is None, line


def test_past_tense_merge_is_not_a_forecast():
    reply = "PR1187 merged at 03:17 after CI went green; PR1104 is still open."
    assert rc.merge_forecast(reply, NO_CHECK_LOG) is None


def test_other_forecast_shapes_are_flagged():
    for reply in ("The PR will merge after the hub queue drains.",
                  "PR #88 lands by 21:00 once review approves.",
                  "The fix PR should merge within the hour, then the deploy follows.",
                  "PR 42 will be merged as soon as checks pass."):
        assert rc.merge_forecast(reply, NO_CHECK_LOG), reply


def test_non_forecasts_pass():
    for reply in ("Want me to merge PR 42 once CI is green?",  # an offer, the user decides
                  "`gh pr merge` merges once the queue allows it.",  # code span
                  "The coach's merge_forecast rule flags \"merges once CI is green\" replies.",  # meta / quoted
                  "hermes-sync deploys it once Desktop is idle.",  # no PR named: a mechanism, not a PR forecast
                  # backtest false positives (worker-6, 14 days): mechanisms and git steps next to a PR mention
                  "PR 42: the installer only deploys when an installed client is older than the pin.",
                  "PR 42: event Z lands after our write but before we re-read the audit.",
                  "PR 42 now waits on a hub card that will deploy-check it.",
                  "For PR 42, I'll merge main into the branch and check again.",
                  "PR 42 is blocked: 3 checks fail on main too, and the rebase needs a fresh review."):
        assert rc.merge_forecast(reply, NO_CHECK_LOG) is None, reply


def test_rule_lists_stay_in_step():
    assert "merge_forecast" in rc.RULES and "merge_forecast" in rc.BACKTEST_RULES
    src = (HERE / "signals.py").read_text() + (HERE / "self_heal.py").read_text()
    assert src.count('"merge_forecast"') >= 2
