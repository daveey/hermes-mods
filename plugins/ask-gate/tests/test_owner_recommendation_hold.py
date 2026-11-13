"""owner_recommendation_hold: an in-limits pick between options is applied by the agent, not handed to David
(red-sweep slug owner-recommendation-not-applied, prevention card t_01a1d6a03ce6f54b).

The four requests below are the exact human_request payloads of the 2026-11-13 red-sweep cards (each card's
`blocked` event, read from the Board replica; host names scrubbed for the public repo). The sweep applied every
pick under David's standing rule ("use your best judgement... i probly tell you to do it"). Real-money and
threshold-change asks must keep passing.
"""
import json

import pytest
from conftest import llm_returning

# worker-5 t_01a1d0866e63dd56: read-only permissions on two Apps David owns.
APP_READ_PERMS = {
    "action": "Reply yes or no in this card: should both hermes-ci-runners Apps get Pull requests: Read + Commit "
              "statuses: Read so that PR/status reads also leave the shared PAT? Unblock = an agent applies the "
              "permission change and extends gh-app-read.py. Archive = PR reads stay on the PAT.",
    "why_human": "This adds permissions to two GitHub Apps that also register CI runners. The card says App "
                 "install/permission changes are David's call.",
    "interaction": "reply",
    "scope": "Apps hermes-ci-runners-probe (5506216) and hermes-ci-runners-2 (5599258), softmax-universe "
             "installations 180786746 and 181007505, repo daveey-hermes only, read-only permissions",
    "evidence": "Probe on worker-5, 2026-11-12 ~16:05Z, with a read-only installation token: GET pulls -> 403, GET "
                "commits/<sha>/status -> 403, gh pr checks -> GraphQL 'Resource not accessible by integration'. "
                "actions/runs and check-runs -> 200.",
    "choices": [{"label": "Yes, grant read perms", "value": "yes"},
                {"label": "No, keep PR reads on PAT", "value": "no"}],
}
# worker-6 t_01a1d5b9382b24ad: which macOS CI lane (self-hosted runner capacity, no money).
MACOS_LANE = {
    "action": "Pick how macOS platforms-marked tests should get CI: A) PR lane on the mini, B) scheduled "
              "non-required lane, C) keep manual. Reply with the letter. Unblock = implement the pick; archive = "
              "keep it off (C).",
    "why_human": "It spends the single self-hosted Mac mini runner's capacity, which David tuned deliberately "
                 "(main-push lanes were dropped to keep required PR jobs from queueing behind it).",
    "interaction": "reply",
    "scope": "core repo CI only: .github/workflows/ci.yaml tests-os job and tests-os.yml macOS matrix row",
    "evidence": "ci.yaml tests-os has `if: false  # fork: no macOS/Windows runner` since 38ac1bd4c86 (#18). PR #2390 "
                "head fa0744925a: 'OS-specific tests' skipping, "
                "https://github.com/softmax-universe/daveey-hermes/actions/runs/43461239237/job/132009090104",
    "choices": [{"label": "A: PR lane on mini", "value": "A"}, {"label": "B: scheduled, non-required", "value": "B"},
                {"label": "C: keep manual", "value": "C"}],
}
# worker-7 t_01a1d4eee7cc5b15: route parity with the hub (subscription quota, not money).
FABLE_ROUTE = {
    "action": "Reply yes or no on this card: register claude-fable-5-2 as a deep-class subscription route on "
              "the MacBook and the mini. Unblock = an agent adds the route on both hosts and verifies a Fable turn; "
              "archive = leave Fable hub-only.",
    "why_human": "It changes which model unpinned deep-class turns use on those hosts and spends Anthropic "
                 "subscription quota there. That's a cost and preference choice, and nothing on record shows "
                 "David's preference.",
    "interaction": "reply",
    "scope": "model_routing.routes.deep on the MacBook and the mini (or shared-config.json for every host)",
    "evidence": "Self-heal defect agent-27ae0dbd84: on 2026-11-12 23:03 PT, Desktop chat 20261112_230228_26766d on "
                "the MacBook failed twice with ManagedPinRejectedError. The hub has Fable registered "
                "(t_01a1c68c9c1c515e); the Mac doesn't.",
    "choices": [{"label": "Register on Mac + mini", "value": "yes"},
                {"label": "Keep Fable hub-only", "value": "no"}],
}
# macbook t_01a1cf6f00320b7e: David's own second device under the same limits.
ROOM_IPHONE = {
    "action": "Reply A or B on this card. A = also count your iPhone in the 7-day room-accuracy run (same limits: "
              "per-room counts only, deleted after 7 days). B = watch only. Unblock = apply your pick in t_fb145498; "
              "archive = keep watch only.",
    "why_human": "It widens a privacy-scoped data collection to a second device. The watch-only scope came from a "
                 "delegated decision, so widening it is David's call.",
    "interaction": "reply",
    "scope": "Room evaluation run on the mini hub, David's devices only, <=7 days, counts only",
    "evidence": "Mini room_fp_latest: watch 62d4d604 last scan 2026-11-04 15:06 PST; watch/sessions.jsonl: 2 watch "
                "sessions since (Nov 4 15:07, Nov 10 17:56). iPhone fced2ba4: 7 non-learning + 6 learn results in "
                "room_fp_results (7 d).",
    "choices": [{"label": "A: add my iPhone", "value": "A"}, {"label": "B: watch only", "value": "B"}],
}
INCIDENTS = {"app_read_perms": APP_READ_PERMS, "macos_lane": MACOS_LANE, "fable_route": FABLE_ROUTE,
             "room_iphone": ROOM_IPHONE}

# Real money over a standing cap (t_01a1a44d9e230f03), given choices so only the limit releases it.
GPU_MONEY = {
    "action": "Approve up to $6 more GPU (one L40S/A100, ~2 h) to diagnose why the 5K LoRA SFT collapsed. That brings "
              "the Agent-World repro total to ~$29.15, over the $25 standing cap. Unblock = approve and run the "
              "diagnosis; archive = drop it and keep the reported FAIL.",
    "why_human": "The standing GPU budget is $25 total per job and $23.15 is already spent; going over needs David's "
                 "own number.",
    "interaction": "approval",
    "scope": "One single-GPU Vast/RunPod rental, ≤$6, auto-teardown; no retraining.",
    "evidence": "results.md: BFCL non-live base 88.92 vs SFT 18.87; SFT output p90 = 4096 tokens; spend ledger $23.15 "
                "(aw_repro_watchdog.log).",
    "choices": [{"label": "Approve $6", "value": "yes"}, {"label": "Keep the FAIL", "value": "no"}],
}
# Recurring cloud spend phrased without a $ sign.
RECURRING_SPEND = dict(
    MACOS_LANE,
    action="Pick where macOS tests run: A) Blacksmith macOS runners, B) the mini. Reply with the letter. Unblock = "
           "implement the pick; archive = B.",
    why_human="A adds recurring Blacksmith spend on a paid plan; B keeps the free mini.")
# Acceptance threshold change.
THRESHOLD = {
    "action": "Reply A or B: A) lower the cut-CI-spend target from 50% to 30% and close the card, B) keep the 50% "
              "target and continue. Unblock = apply the pick; archive = B.",
    "why_human": "A lowers the approved 50% acceptance target, which only David set.",
    "interaction": "reply",
    "scope": "CI cost card acceptance only",
    "evidence": "terminal: ci-cost ledger shows 31% reduction after the shipped caching and sharding changes",
    "choices": [{"label": "A: lower to 30%", "value": "A"}, {"label": "B: keep 50%", "value": "B"}],
}
# A write permission (not read-only) on the same Apps.
WRITE_PERMS = dict(
    APP_READ_PERMS,
    action="Reply yes or no: should both hermes-ci-runners Apps get Contents: Write so CI can push the cache branch? "
           "Unblock = an agent applies the permission change; archive = keep read-only.",
    scope="Apps 5506216 and 5599258, repo daveey-hermes, contents write access")
# Other people.
ROSE = dict(
    ROOM_IPHONE,
    action="Reply A or B. A = also count Rose's iPhone in the room-accuracy run. B = David's devices only. Unblock = "
           "apply the pick; archive = B.",
    why_human="It collects room data from Rose's phone; she has not been asked.",
    scope="Room evaluation run on the mini hub, <=7 days, counts only")
# Hard to reverse.
DESTRUCTIVE = dict(
    MACOS_LANE,
    action="Pick: A) delete the 2025 replay archive bucket now, B) keep it. Reply with the letter. Unblock = apply; "
           "archive = B.",
    why_human="A is irreversible: the bucket holds the only copy.")
# A rule David stated himself.
DAVID_RULE = dict(
    FABLE_ROUTE,
    why_human="David said in chat on 11-10: \"ask me before adding any model to the Mac's routes\".")
# Not a pick: one credential step only David can do.
CREDENTIAL = {
    "action": "Run `gh auth login` in Terminal on the MacBook and finish the device-code sign-in with your GitHub "
              "account. Unblock = an agent re-runs the release; archive = skip the release.",
    "why_human": "The GitHub device flow signs in to David's own account; no agent holds that login or a token for it.",
    "interaction": "user_terminal",
    "scope": "One gh auth login on the MacBook; nothing else.",
    "evidence": "terminal: gh auth status -> You are not logged into any GitHub hosts",
}
PASSING = {"gpu_money": GPU_MONEY, "recurring_spend": RECURRING_SPEND, "threshold": THRESHOLD,
           "write_perms": WRITE_PERMS, "rose": ROSE, "destructive": DESTRUCTIVE, "david_rule": DAVID_RULE,
           "credential": CREDENTIAL}


def _events(tmp_path, kind):
    p = tmp_path / "logs" / "ask-gate.jsonl"
    if not p.exists():
        return []
    return [r for r in map(json.loads, p.read_text().splitlines()) if r.get("kind") == kind]


def _block(gate, hr, session="s1", verdict=("ok", "a real human decision")):
    gate.LOG.add(session, "terminal", "evidence check", hr["evidence"])
    gate.LLM = llm_returning([verdict])
    return gate.pre_tool_call(tool_name="kanban_block", session_id=session,
                              args={"kind": "needs_input", "reason": "see human_request", "human_request": hr})


@pytest.mark.parametrize("name", sorted(INCIDENTS))
def test_incident_request_is_classified_in_limits(gate, name):
    assert gate.owner_recommendation.hit(INCIDENTS[name]) is not None


@pytest.mark.parametrize("name", sorted(INCIDENTS))
def test_incident_block_is_refused_with_apply_and_reverse(gate, tmp_path, name):
    r = _block(gate, INCIDENTS[name])
    assert r and r["action"] == "block"
    assert "owner_recommendation_hold" in r["message"]
    assert "apply your recommendation and comment how to reverse" in r["message"]
    assert gate.LLM.seen == []  # deterministic: held before the judge
    held = _events(tmp_path, "owner_recommendation_hold")
    assert held and held[0]["tool"] == "kanban_block"


@pytest.mark.parametrize("name", sorted(INCIDENTS))
def test_incident_blocked_card_creation_is_refused(gate, name):
    hr = INCIDENTS[name]
    gate.LOG.add("s1", "terminal", "evidence check", hr["evidence"])
    gate.LLM = llm_returning([("ok", "fine")])
    r = gate.pre_tool_call(tool_name="kanban_create", session_id="s1", args={
        "title": "Decide: " + name, "initial_status": "blocked", "human_request": hr})
    assert r and "owner_recommendation_hold" in r["message"]


def test_incident_is_refused_in_a_kanban_worker(gate, monkeypatch):
    monkeypatch.setenv("HERMES_KANBAN_TASK", "t_01a1d5b9382b24ad")
    r = _block(gate, MACOS_LANE)
    assert r and "owner_recommendation_hold" in r["message"]


@pytest.mark.parametrize("name", sorted(PASSING))
def test_david_only_limit_is_not_classified(gate, name):
    assert gate.owner_recommendation.hit(PASSING[name]) is None


@pytest.mark.parametrize("name", ["gpu_money", "recurring_spend", "threshold"])
def test_real_money_and_threshold_blocks_go_through(gate, tmp_path, name):
    hr = PASSING[name]
    r = _block(gate, hr)
    for _ in range(3):  # other bounded wording checks may hold once; none of them is this hold
        if r is None:
            break
        assert "owner_recommendation_hold" not in r.get("message", "")
        r = _block(gate, hr)
    assert r is None
    assert gate.LLM.seen  # reached the judge
    assert not _events(tmp_path, "owner_recommendation_hold")


def test_limit_names(gate):
    lim = gate.owner_recommendation.limit_named
    assert lim(GPU_MONEY) == "money"
    assert lim(RECURRING_SPEND) == "money"
    assert lim(THRESHOLD) == "threshold"
    assert lim(WRITE_PERMS) == "permission"
    assert lim(ROSE) == "other_people"
    assert lim(DESTRUCTIVE) == "safety"
    assert lim(DAVID_RULE) == "david_rule"
    assert all(lim(r) is None for r in INCIDENTS.values())


def test_evidence_does_not_release(gate):
    # APP_READ_PERMS evidence says "read-only installation token": evidence is tool output, not the ask's limit.
    assert "token" in APP_READ_PERMS["evidence"]
    assert gate.owner_recommendation.hit(APP_READ_PERMS) is not None


def test_pick_wording_without_choices_is_held(gate):
    hr = {k: v for k, v in FABLE_ROUTE.items() if k != "choices"}
    assert gate.owner_recommendation.hit(hr) is not None


def test_single_approval_without_a_pick_is_not_held(gate):
    hr = dict(FABLE_ROUTE, action="Approve registering claude-fable-5-2 on the Mac and mini. Unblock = add it; "
              "archive = leave it.", interaction="approval")
    hr.pop("choices")
    assert gate.owner_recommendation.hit(hr) is None


@pytest.mark.parametrize("interaction", ["physical", "user_terminal", "external"])
def test_non_decision_interactions_are_not_held(gate, interaction):
    assert gate.owner_recommendation.hit(dict(MACOS_LANE, interaction=interaction)) is None


def test_hold_is_bounded_then_released(gate, tmp_path):
    for _ in range(2):
        assert "owner_recommendation_hold" in _block(gate, ROOM_IPHONE)["message"]
    r = _block(gate, ROOM_IPHONE)
    assert r is None or "owner_recommendation_hold" not in r.get("message", "")
    assert _events(tmp_path, "owner_recommendation_released")


def test_gate_error_fails_open(gate, tmp_path, monkeypatch):
    def broken(_request):
        raise RuntimeError("regex exploded")
    monkeypatch.setattr(gate.owner_recommendation, "hit", broken)
    r = _block(gate, FABLE_ROUTE)
    for _ in range(3):
        if r is None:
            break
        assert "owner_recommendation_hold" not in r["message"]
        r = _block(gate, FABLE_ROUTE)
    assert r is None
    errs = _events(tmp_path, "owner_recommendation_error")
    assert errs and "regex exploded" in errs[0]["error"]


def test_judge_rule_covers_prose_the_regex_misses(gate):
    assert "apply your recommendation and comment how to" in gate.judge_mod.STRUCTURED_SYSTEM


def test_rewording_does_not_restart_the_bound(gate, tmp_path, monkeypatch):
    # The count is per blocked card, not per ask text: an agent that rewords a held ask must not be held forever.
    monkeypatch.setenv("HERMES_KANBAN_TASK", "t_01a1cf6f00320b7e")
    for i in range(2):
        hr = dict(ROOM_IPHONE, action=ROOM_IPHONE["action"] + " (rewording %d)" % i)
        assert "owner_recommendation_hold" in _block(gate, hr)["message"]
    r = _block(gate, dict(ROOM_IPHONE, action=ROOM_IPHONE["action"] + " (rewording 2)"))
    assert r is None or "owner_recommendation_hold" not in r.get("message", "")
    assert _events(tmp_path, "owner_recommendation_released")


def test_another_card_has_its_own_bound(gate, monkeypatch):
    monkeypatch.setenv("HERMES_KANBAN_TASK", "t_01a1cf6f00320b7e")
    for _ in range(2):
        _block(gate, ROOM_IPHONE)
    gate.LOG.add("s1", "terminal", "evidence check", MACOS_LANE["evidence"])
    gate.LLM = llm_returning([("ok", "fine")])
    r = gate.pre_tool_call(tool_name="kanban_block", session_id="s1", args={
        "task_id": "t_01a1d5b9382b24ad", "kind": "needs_input", "reason": "x", "human_request": MACOS_LANE})
    assert r and "owner_recommendation_hold" in r["message"]


# Review findings on PR #98: David-only limits the first regexes missed. Each must pass (not be held).
@pytest.mark.parametrize("action,why", [
    ("Reply yes or no: give the release bot repo admin so it can edit settings?", "It changes who controls the repo."),
    ("Reply yes or no: should the App get admin rights on the org?", "It changes the App."),
    ("Reply yes or no: grant the App read-only access and also write on Contents?", "It changes the App."),
    ("Pick one: A) upgrade to the Claude Max plan, B) stay on Pro. Reply with the letter.", "It is a plan choice."),
    ("Reply yes or no: renew the hermes.dev domain for another year?", "It expires next week."),
    ("Reply A or B: A) accept the new license terms for the SDK, B) pin the old version.", "Terms changed."),
    ("Reply A or B: A) read the personal Gmail account for receipts, B) the work inbox only.", "Mail access."),
    ("Reply A or B: A) restart the production hub now, B) wait for the nightly window.", "A interrupts chats."),
    ("Reply A or B: A) take BB offline for 5 min to migrate, B) migrate live.", "A is a short gap."),
    ("Reply yes or no: change branch protection on main to drop the review requirement?", "Repo policy."),
])
def test_review_found_limits_pass(gate, action, why):
    hr = dict(MACOS_LANE, action=action, why_human=why, scope="see action")
    assert gate.owner_recommendation.hit(hr) is None, gate.owner_recommendation.limit_named(hr)


def test_subscription_quota_alone_is_not_money(gate):
    # Spending existing subscription quota (the Fable incident) stays inside the limits; an upgrade does not.
    assert "subscription quota" in FABLE_ROUTE["why_human"]
    assert gate.owner_recommendation.limit_named(FABLE_ROUTE) is None
