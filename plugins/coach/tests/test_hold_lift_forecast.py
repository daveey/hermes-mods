"""hold_lift_forecast (self-heal agent-2f71681b0b): a reply that forecasts waiting cards start when a provider comes
back, resets or lifts its pacing hold is held deterministically, before the judge.

Incident (hub session 20261111_200009_edbe39, 2026-11-15): at 12:41Z the chat read anthropic `held` until 14:00Z
(6:00 AM PT), with 7 of 8 accounts at 7d 100% and only one 5-hour window due to reset. It then told David three
times that the waiting cards (9th-31st in line) "start when a Codex slot frees up or Claude comes back at 6:00 AM
PT". At 6:02-6:03 the reset window ran 4 cards from the head of the queue, none of them his, and anthropic re-held
until 7:00. The turn HAD read quota, so quota_claim's read gate could not catch it: a hold lifting is when the hold
ends, not when a given card starts.
"""
import importlib.util
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("t_reply_check_hold_lift", HERE / "reply_check.py")
rc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rc)

# Messages 1309850, 1309966 and 1310341 of the incident chat (the forecast sentences, verbatim).
INCIDENT = [
    "The scaler and Grok cards (24th and 25th in the queue) start when a Codex slot frees up or Claude comes back at "
    "6:00 AM PT, whichever is first.",
    "Both cards start when a Codex slot frees up or Claude comes back at 6:00 AM PT, whichever comes first.",
    "Nothing new needs you. Next up: the #3304 and #3301 review cards and the restart-failure card start as workers "
    "free up, and Claude comes back at 6:00 AM PT.",
    # Hub session 20261110_184601_52504d, 2026-11-14 (same forecast from one account's reset).
    "Cards should start running again from about Nov 15, 14:00Z, when the work account resets.",
]

# The incident turn read pacing state: the rule must not be exempted by a quota read.
QUOTA_LOG = ["-> terminal({\"command\": \"sqlite3 -readonly ~/.hermes/kanban.db \\\"select payload from task_events "
             "where kind='llm_paced' order by id desc limit 3\\\"\"})"]

# Replies that state the hold's end without promising a start, report the past, or hedge.
CLEAN = [
    "Claude's hold lifts at 6:00 AM PT, but that only resets one account's 5-hour window, and these cards are 9th "
    "and 10th in line, so they may not start then.",
    "It started at 06:40 UTC once the Codex quota reset, picked up the handoff, and parked itself on this CI run.",
    "The rollout card is running again: run 178169 started at 09:20 UTC, right after the Claude quota reset at 09:20.",
    "Kanban workers can't run them until Claude's weekly limit resets; the first account resets at 00:00Z on 11-15.",
    "Claude's pause ends at 6:00 AM PT. The card is 31st in line behind cards that use the same provider.",
    "Does the card start when Claude comes back at 6:00 AM PT?",
    "The hold_lift_forecast rule fired on: `cards start when Claude comes back at 6:00`.",
]


def _no_judge(system, prompt):
    raise AssertionError("hold_lift_forecast must not need the LLM judge")


def test_incident_forecasts_are_held_before_the_judge():
    for reply in INCIDENT:
        hit = rc.check(reply, "[kanban notice] card waits", QUOTA_LOG, _no_judge, only=["hold_lift_forecast"])
        assert hit and hit["rule"] == "hold_lift_forecast" and hit["p"] == 1.0, reply
        assert "until" in hit["nudge"] and "dispatch" in hit["nudge"]


def test_hold_lift_forecast_is_a_default_reply_only_rule():
    assert "hold_lift_forecast" in rc.DEFAULT_RULES and "hold_lift_forecast" in rc.RULES
    assert "hold_lift_forecast" in rc.REPLY_ONLY_RULES and "hold_lift_forecast" in rc.NEW_CLAIM_RULES
    assert "hold_lift_forecast" in rc.BACKTEST_RULES


def test_statements_of_the_hold_past_tense_and_hedges_pass():
    for reply in CLEAN:
        assert rc.hold_lift_forecast(reply, QUOTA_LOG) is None, reply


def test_revision_that_reintroduces_the_forecast_fails_another_nudges_outcome():
    res = rc.nudge_outcome("merge_forecast", INCIDENT[1] + " PR #3316 is approved.", "notice",
                           ["-> terminal(gh pr view 3316 --json state)"], _no_judge,
                           extra_rules=("hold_lift_forecast",))
    assert res.get("new_claim", {}).get("rule") == "hold_lift_forecast"
