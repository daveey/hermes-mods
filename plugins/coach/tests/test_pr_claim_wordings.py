"""Self-heal agent-aece75acd4 (+ agent-fd934117b4): stale PR-state claims the PR guards let through.

Incident 1 (agent-aece75acd4): at 08:35 PT on Nov 13 a chat, after a report_only nudge, told David a fix "merges right
after the other Hermes change to the same file, PR #1806. That one is approved, its checks pass, and its worker is
landing it." The fix had merged at 06:03 PT and the turn read neither the card nor the PR. Three gaps:
- merge_forecast needed "merges" directly before "after/when": "merges right after" slipped through;
- pr_state_claim only knew negative claims ("not merged", "still open"): "is approved, its checks pass" passed;
- the nudge's outcome check re-ran only the nudged rule, so a stale claim the revision added reached David unchecked.

Incident 2 (agent-fd934117b4): on Nov 12 08:54 PT a chat said "#1187 is approved and waiting for its macOS check";
#1187 had merged at 16:47:04Z and was never re-read that turn.
"""
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


rc = load("coach_reply_check_pr_claim_wordings", ROOT / "reply_check.py")

REVISED_REPLY = (
    "The slow-session-start fix (t_01a1d4a2514c0224) merges right after the other Hermes change to the same file, "
    "PR #1806. That one is approved, its checks pass, and its worker is landing it.")
# The nudged draft carried the same plan (paraphrase: the report_only nudge hit its "worker is landing it" line).
DRAFT_REPLY = (
    "Still on it. The slow-session-start fix merges right after PR #1806, which touches the same file; "
    "its worker is landing it.")
FD_REPLY = ("Two fixes are already moving: #1187 is approved and waiting for its macOS check, and "
            "t_01a1d09a4b2dce85 is running.")
# The resumed turn read two other cards, never the PR or its card.
NO_READ_LOG = ["-> kanban_show({\"task_id\": \"t_01a1d4a0000000001\"})",
               "-> kanban_show({\"task_id\": \"t_01a1d4a0000000002\"})"]
PR_READ = "-> terminal(gh pr view 1806 --json state,mergedAt)"


def _no_judge(system, prompt):
    raise AssertionError("deterministic PR rules must not need the judge")


# ── merge_forecast: an adverb between "merges/lands" and "after/when/once" ───────────────────────────────────────

def test_incident_revised_reply_is_a_merge_forecast():
    assert "merges right after" in (rc.merge_forecast(REVISED_REPLY, NO_READ_LOG) or "")
    assert rc.merge_forecast(DRAFT_REPLY, NO_READ_LOG)


def test_adverb_variants_forecast():
    for adv in ("right", "just", "soon", "immediately", "straight", "directly"):
        # "lands after" stays a mechanism ("Z lands after our write"); "lands" needs a time/condition word
        for verb, cond in (("merges", "after"), ("merges", "when"), ("merges", "once"), ("lands", "once")):
            reply = f"PR #42 {verb} {adv} {cond} CI is green."
            assert rc.merge_forecast(reply, NO_READ_LOG), reply


def test_merge_forecast_negative_controls():
    for reply in ("PR #42: I'll merge it into main myself.",
                  "PR #42 merges into main through the queue; that is how the queue works.",
                  "PR #42 and #43 were two merges in a row.",
                  # backtest: gate mechanism, not a PR's merge time
                  "For PR #42, ship merges only after CI is green on that exact head.",
                  "> PR #42 merges right after #41",
                  "Does PR #42 merge right after #41?"):
        assert rc.merge_forecast(reply, NO_READ_LOG) is None, reply
    assert rc.merge_forecast(REVISED_REPLY, NO_READ_LOG + [PR_READ]) is None


# ── pr_state_claim: positive present-state claims ───────────────────────────────────────────────────────────────

def test_incident_revised_reply_is_a_pr_state_claim():
    hit = rc.pr_state_claim(REVISED_REPLY, NO_READ_LOG)
    assert hit and "approved" in hit


def test_fd934117b4_reply_is_a_pr_state_claim():
    assert rc.pr_state_claim(FD_REPLY, [])
    assert rc.pr_state_claim("PR #42 is waiting on the macOS CI job.", [])
    assert rc.pr_state_claim("PR #42 is approved and waiting.", [])


def test_positive_state_wordings_fire():
    for reply in ("PR #42 is approved.",
                  "PR #42's checks pass.",
                  "PR #42: all checks are green, review done.",
                  "PR #42 has been approved and CI is green.",
                  "PR #42 is queued to merge.",
                  "PR #42 is in the merge queue.",
                  "PR #42 is waiting for its required checks."):
        assert rc.pr_state_claim(reply, NO_READ_LOG), reply


def test_positive_state_negative_controls():
    for reply in ("If PR #42 is approved, I'll land it.",
                  "if #1187 is approved and waiting, the queue picks it up.",
                  "> #1187 is waiting for its macOS check to be added\n\nI'll look.",
                  "t_01a1d09a4b2dce85 is waiting for its parent card to finish.",
                  "You said \"PR 42 is approved\", but it merged.",
                  "Is PR #42 approved?",
                  "PR #42 merged as bd8476 at 16:35.",
                  "The coach's pr_state_claim rule flags \"is approved\".",
                  # backtest mechanism/hypothetical wordings
                  "Suppose a PR is approved while its card is parked.",
                  "PR #42 is deployed on the hub; the ancestry check passes.",
                  "I approved PR #1504, but the ship gate still has to see the required checks pass.",
                  "Next, once the review approves PR #42, confirm every check is green on that head.",
                  "The landing gate opens a PR and requires required checks pass on that exact head."):
        assert rc.pr_state_claim(reply, NO_READ_LOG) is None, reply
    assert rc.pr_state_claim(REVISED_REPLY, NO_READ_LOG + [PR_READ]) is None
    assert rc.pr_state_claim(FD_REPLY, ["-> terminal(gh pr view 1187 --json state,mergedAt)"]) is None


# ── the nudge's outcome also runs the deterministic PR rules on the revision ──────────────────────────────────────

def _quiet(system, prompt):
    return json.dumps({k: 0 for k in rc.RULES} | {"evidence": ""})


def test_outcome_fails_when_the_revision_adds_a_stale_pr_claim():
    res = rc.nudge_outcome("report_only", REVISED_REPLY, "is the slow start fixed?", NO_READ_LOG, _quiet,
                           threshold=0.7)
    assert res["resolved"] is False
    assert res["new_claim"]["rule"] in ("merge_forecast", "pr_state_claim")
    assert "1806" in res["new_claim"]["evidence"] or "merges right after" in res["new_claim"]["evidence"]


def test_outcome_stays_resolved_when_the_revision_read_the_pr():
    res = rc.nudge_outcome("report_only", REVISED_REPLY, "is the slow start fixed?", NO_READ_LOG + [PR_READ],
                           _quiet, threshold=0.7)
    assert res["resolved"] is True and "new_claim" not in res


def test_outcome_extra_rules_can_be_limited():
    res = rc.nudge_outcome("report_only", REVISED_REPLY, "x", NO_READ_LOG, _quiet, threshold=0.7, extra_rules=())
    assert res["resolved"] is True and "new_claim" not in res


def test_live_outcome_logs_new_claim(tmp_path, monkeypatch):
    mod = load("coach_init_pr_claim_wordings", ROOT / "__init__.py")
    c = mod._load("core")
    rcm = mod._load("reply_check")
    z = {k: 0 for k in rcm.RULES}
    verdicts = [{**z, "confirm": 0.9, "evidence": "asks permission"}, {**z, "confirm": 0.1, "evidence": "ok"}]
    calls = []

    def judge(s, p):
        calls.append(p)
        return json.dumps(verdicts[min(len(calls), len(verdicts)) - 1])
    monkeypatch.setattr(rcm, "aux_judge", judge)
    monkeypatch.setenv("COACH_SYSTEM_ONE", "off")
    monkeypatch.setenv("COACH_STALLED_GOAL", "0")
    monkeypatch.setenv("COACH_REPLY_RULES", "confirm,merge_forecast,pr_state_claim")
    root = tmp_path / "coach"
    _, hooks = mod.build(store=c.Store(root=root), checkout=None)
    sid = "20261111_091916_d2d36a"
    hooks["pre_llm_call"](session_id=sid, user_message="is the slow start fixed?", platform="desktop")
    out = hooks["pre_verify"](session_id=sid, platform="desktop", attempt=0,
                              final_response="I found the slow path. Want me to fix it?")
    assert out and out["action"] == "continue"
    assert hooks["pre_verify"](session_id=sid, platform="desktop", attempt=1, final_response=REVISED_REPLY) is None
    rows = [json.loads(l) for l in (root / "log.jsonl").read_text().splitlines()]
    outs = [r for r in rows if r["event"] == "reply_nudge_outcome"]
    assert len(outs) == 1 and outs[0]["rule"] == "confirm" and outs[0]["resolved"] is False
    new = [r for r in rows if r["event"] == "reply_nudge_outcome_new_claim"]
    assert len(new) == 1 and new[0]["rule"] == "confirm" and new[0]["claim_rule"] in ("merge_forecast",
                                                                                     "pr_state_claim")
    # no second nudge: the revision was let through
    assert len([r for r in rows if r["event"] == "reply_nudge"]) == 1
