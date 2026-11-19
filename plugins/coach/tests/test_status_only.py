"""status_only: the user asks about waiting/progress (or to finish the work) and the turn only reopened, unblocked,
commented on or looked at cards. Defect agent-38a817308d: four replies in one chat reported "reopened the card /
verified ready" while the rollout card sat unstarted for 4 days, until the user wrote "can you just finish it please?".

ASKS and REPLIES are the session's real text (coach digest + the chat read by the investigation card). The tool logs
are not in the digest; they are rebuilt from what each reply says it did (reopen/unblock, show, comment, cross-gateway
reads), in the coach's own tool-log line format."""
import hashlib
import importlib.util
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent


def _rc():
    spec = importlib.util.spec_from_file_location("t_reply_check_so", HERE / "reply_check.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


rc = _rc()

REAL = [
    ("make all the corrections",
     "I reopened the original implementation card and verified it is **ready**, with its existing code, history, "
     "dependencies, and Codex subscription intact. The recovery and review tasks now have that confirmation.\n\n"
     "The required correction is unchanged: **no OpenRouter request may escape through a stale model pin, saved "
     "session, or fallback.** Deployment remains gated on the fix and outbound-request regression tests.",
     ["-> terminal(hermes kanban unblock t_ae11c0bc --reason 'reopen for corrections')",
      "-> kanban_show(t_ae11c0bc)",
      "-> kanban_comment(t_e3e216d4)",
      "-> kanban_comment(t_5ac0e9ee)"]),
    ("whya re we waiting for 8h?",
     "Reopened the original card and verified **ready** for both compression fixes.\n\nI also expanded the correction "
     "instructions to audit the whole temporary-switch state restoration together, rather than fixing one missing "
     "field per review cycle. That repeated piecemeal repair is contributing to the delay. Deployment remains gated; "
     "nothing further is needed from you.",
     ["-> kanban_show(t_82f0fe15)",
      "-> tool_call({\"calls\": [{\"name\": \"cross_task\", \"arguments\": {\"action\": \"read\", \"task\": \"t_ae11c0bc\"}}]})",
      "-> terminal(hermes-hub request hub tasks default/default/t_ae11c0bc unblock --arg reason=reopen)",
      "-> kanban_comment(t_ae11c0bc)"]),
    ("whats going on here, why are you blocked",
     "Reactivated the same reviewer and verified **ready** for commit `ae2ea0cce48c`. The earlier dependency deadlock "
     "remains removed, so review can run alongside CI.\n\nThe deployment owner has the new commit and CI handle. Fresh "
     "review and passing exact-commit CI are still required; no duplicate run or deployment was started.",
     ["-> kanban_list({\"status\": \"blocked\"})",
      "-> kanban_show(t_5ac0e9ee)",
      "-> terminal(hermes kanban unblock t_5ac0e9ee)",
      "-> kanban_comment(t_7e2ee45c)"]),
    ("what are you doing?",
     # the digest clips the middle of this reply: head and tail verbatim, joined at the clip mark
     "I’ve been **coordinating the subscription-balancer implementation, review, and release tasks**—mostly "
     "reopening the same reviewer when the release bra …[clipped]… te them and get the resulting commit through CI.\n"
     "- Repeated updates to `main` have also forced fresh integration and review, prolonging this cycle.",
     ["-> kanban_show(t_82f0fe15)",
      "-> kanban_show(t_7e2ee45c)",
      "-> cross_session({\"action\": \"read\", \"target\": \"@session:default@hub/20261023_054025_d312e6\"})"]),
]


def test_fires_on_the_four_real_status_replies():
    for ask, reply, log in REAL:
        hit = rc.status_only(ask, reply, log)
        assert hit, (ask, reply[:60])


def test_check_returns_the_deterministic_status_only_nudge_without_a_judge_call():
    calls = []
    llm = lambda s, p: calls.append(1) or json.dumps({k: 0 for k in rc.RULES})
    for ask, reply, log in REAL:
        hit = rc.check(reply, ask, log, llm, only=["status_only", "report_only"])
        assert hit and hit["rule"] == "status_only" and hit["p"] == 1.0, ask
        assert "next real step" in hit["nudge"] and "wall" in hit["nudge"]
    assert calls == []


def test_quiet_when_the_turn_made_a_commit():
    for ask, reply, log in REAL:
        did = log + ["-> terminal(cd ~/code/hermes && git commit -m 'route alpha through the balancer' && git push)"]
        assert rc.status_only(ask, reply, did) is None, ask


def test_quiet_when_the_turn_edited_code_or_ran_tests():
    ask, reply, log = REAL[1]
    assert rc.status_only(ask, reply, log + ["-> patch(path=src/router.py, 'pin = None')"]) is None
    assert rc.status_only(ask, reply, log + ["-> terminal(scripts/run_tests.sh tests/test_router.py)"]) is None
    assert rc.status_only(ask, reply, log + ["-> write_file(path=docs/x.md, 'x')"]) is None


def test_quiet_when_the_reply_names_a_wall_only_the_user_can_clear():
    ask, _, log = REAL[1]
    wall = ("The deploy is waiting on one thing only you can do: GitHub Actions billing failed (payment declined), so "
            "no CI job can start. Raise the spending limit at github.com/settings/billing.")
    assert rc.status_only(ask, wall, log) is None


def test_quiet_without_a_progress_or_finish_ask():
    _, reply, log = REAL[1]
    assert rc.status_only("what does the reviewer card check?", reply, log) is None
    assert rc.status_only("thanks", reply, log) is None


def test_quiet_when_no_card_was_touched():
    ask, reply, _ = REAL[1]
    assert rc.status_only(ask, reply, []) is None
    assert rc.status_only(ask, reply, ["-> read_file(README.md)"]) is None


def test_pre_verify_hook_nudges_the_real_turn_once(tmp_path, monkeypatch):
    """Entry path: the live pre_verify hook, fed the turn through the same hooks the agent loop calls."""
    spec = importlib.util.spec_from_file_location("coach_init_so", HERE / "__init__.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    core = mod._load("core")
    rcm = mod._load("reply_check")
    judged = []
    monkeypatch.setattr(rcm, "aux_judge", lambda s, p: judged.append(1) or json.dumps({k: 0 for k in rcm.RULES}))
    monkeypatch.setenv("COACH_SYSTEM_ONE", "off")
    monkeypatch.setenv("COACH_STALLED_GOAL", "0")
    monkeypatch.delenv("COACH_REPLY_RULES", raising=False)
    root = tmp_path / "coach"
    _, hooks = mod.build(store=core.Store(root=root), checkout=None)
    sid = "20261101_000000_bbbbbb"
    hooks["pre_llm_call"](session_id=sid, user_message="whya re we waiting for 8h?", platform="desktop")
    hooks["post_tool_call"](tool_name="kanban_show", args={"task_id": "t_82f0fe15"}, result="{}", session_id=sid)
    hooks["post_tool_call"](tool_name="terminal", args={"command": "hermes kanban unblock t_ae11c0bc"},
                            result="{}", session_id=sid)
    hooks["post_tool_call"](tool_name="kanban_comment", args={"task_id": "t_ae11c0bc", "body": "x"}, result="{}",
                            session_id=sid)
    out = hooks["pre_verify"](session_id=sid, platform="desktop", attempt=0, final_response=REAL[1][1])
    assert out and out["action"] == "continue" and "status_only" in out["message"]
    assert judged == []
    logged = [json.loads(l) for l in (root / "log.jsonl").read_text().splitlines()]
    assert any(r.get("event") == "reply_nudge" and r.get("rule") == "status_only" for r in logged)
    # the row carries the ask's opening text, which the repeated-template detector (signals.py) groups on
    assert [r.get("ask_head") for r in logged if r.get("event") == "reply_nudge"] == ["whya re we waiting for 8h?"]
    # ...and a hash of the whole ask, so one notice fanned out to N chats counts as one prompt (agent-efb7407bb5)
    assert [r.get("ask_fp") for r in logged if r.get("event") == "reply_nudge"] == [
        hashlib.sha1("whya re we waiting for 8h?".encode()).hexdigest()[:10]]
    # the retry (attempt 1) is never nudged again
    assert hooks["pre_verify"](session_id=sid, platform="desktop", attempt=1, final_response=REAL[1][1]) is None


def test_pre_verify_logs_the_request_behind_a_resume_preamble(tmp_path, monkeypatch):
    """agent-6b76c58161: a resumed turn's ask is core's recovery note + the user's words. The row's ask_head is the
    user's words (the template detector groups on it) and ask_wrapped marks the strip; ask_fp still hashes it all."""
    spec = importlib.util.spec_from_file_location("coach_init_so_wrap", HERE / "__init__.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    core = mod._load("core")
    rcm = mod._load("reply_check")
    monkeypatch.setattr(rcm, "aux_judge", lambda s, p: json.dumps({k: 0 for k in rcm.RULES}))
    monkeypatch.setenv("COACH_SYSTEM_ONE", "off")
    monkeypatch.setenv("COACH_STALLED_GOAL", "0")
    monkeypatch.delenv("COACH_REPLY_RULES", raising=False)
    root = tmp_path / "coach"
    _, hooks = mod.build(store=core.Store(root=root), checkout=None)
    sid = "20261101_000000_cccccc"
    ask = ("[System note: Your previous turn was interrupted mid-run — the app or its backend process stopped before "
           "the turn could finish. Some of the work may already be complete; check the current state before redoing "
           "anything, then finish the task. The interrupted request was:]\n\nwhya re we waiting for 8h?")
    hooks["pre_llm_call"](session_id=sid, user_message=ask, platform="desktop")
    hooks["post_tool_call"](tool_name="kanban_show", args={"task_id": "t_82f0fe15"}, result="{}", session_id=sid)
    hooks["post_tool_call"](tool_name="terminal", args={"command": "hermes kanban unblock t_ae11c0bc"},
                            result="{}", session_id=sid)
    hooks["post_tool_call"](tool_name="kanban_comment", args={"task_id": "t_ae11c0bc", "body": "x"}, result="{}",
                            session_id=sid)
    out = hooks["pre_verify"](session_id=sid, platform="desktop", attempt=0, final_response=REAL[1][1])
    assert out and out["action"] == "continue"
    rows = [json.loads(l) for l in (root / "log.jsonl").read_text().splitlines()]
    nudges = [r for r in rows if r.get("event") == "reply_nudge"]
    assert [(r.get("ask_head"), r.get("ask_wrapped")) for r in nudges] == [("whya re we waiting for 8h?", True)]
    assert nudges[0]["ask_fp"] == hashlib.sha1(ask.encode()).hexdigest()[:10]


def test_status_only_is_a_default_rule_and_can_be_turned_off(monkeypatch):
    monkeypatch.delenv("COACH_REPLY_RULES", raising=False)
    assert "status_only" in rc.settings()["rules"]
    ask, reply, log = REAL[1]
    llm = lambda s, p: json.dumps({k: 0 for k in rc.RULES})
    hit = rc.check(reply, ask, log, llm, only=["report_only"])  # rule list without it -> never fires
    assert not hit or hit["rule"] != "status_only"


# agent-fafbddd67a: a "⏸ … blocked" Kanban notice (the gateway's needs_input ping) was scored as David asking about
# progress, because reply_check.NOTICE_RE only knew the ✔ done notice. Its card ask ("land and deploy it") matched the
# progress pattern, and the reply named the wall in its first sentence ("a choice only you can make"), outside the
# 1500-char tail WALL_RE read. Real ask and reply from session 20261110_185031_4f5f4b (hermes-hub, 2026-11-13).
BLOCKED_ASK = (
    "⏸ [worker-6:default] @default Kanban t_01a1d59e543673b0 blocked — actor: agent (task t_01a1d59e543673b0)\n"
    "Next action for the user: Reply A, B or C on this card (A = task liveness hold 300 s, sessions 30 s, 52.7%; "
    "B = task hold 180 s, sessions 30 s, 51.1%; C = keep 30/30 and accept ~42%). Unblock = I apply the pick to PR "
    "#126, get an independent review, land and deploy it; archive = drop card N and close the PR.\n"
    "Why only the user: Each option changes an approved design limit: the ≥50% acceptance target (C), or C7's 30 s "
    "hold bound for task liveness (A/B). At 30 s the target is not reachable: task liveness changes arrive 60–120 s "
    "apart.\nScope: hermes-hub PR https://github.com/daveey/hermes-hub/pull/126 (LIVENESS_HOLD_S); design "
    "t_01a1d563324d7e8e P4/C7. Session latency stays ≤30 s under A, B and C.\n"
    "Open task: @task:default@worker-6/t_01a1d59e543673b0")
BLOCKED_REPLY = (
    "One piece of the hub CPU work is blocked on a choice only you can make, because each option changes a limit you "
    "approved in the design. I checked the card on worker-6 just now and it's still waiting. Your earlier \"a\" was "
    "for the worker-first rollout's update pause and went to that card, before this question was asked.\n\n"
    "This piece cuts how many rows the hub sends to the Desktop backends when the only thing that changed is a \"last "
    "active\" time. The change is built and passes CI (hermes-hub PR #126). But with the 30-second hold the design "
    "approved, it removes only 42% of those rows, short of the 50% target. A card's \"last active\" time changes "
    "every 60–120 seconds, so a 30-second hold rarely gets to merge two changes together.\n\n"
    "It tested other settings by replaying 10 minutes of real hub traffic. In every option, chat activity on your "
    "other machines still shows up within 30 seconds, and status and content changes still show up immediately.\n\n"
    "| | Cards' \"last active\" time | Rows removed | What changes |\n|---|---|---|---|\n"
    "| **A (recommended)** | can lag up to 5 min | 52.7% | Meets the target. The design review found that time is "
    "only displayed, so nothing acts on it. |\n"
    "| **B** | can lag up to 3 min | 51.1% | Meets the target with little margin. |\n"
    "| **C** | stays at 30 s | about 42% | Lowers the target. |\n\n"
    "Here's the card's ask word for word: \"Reply A, B or C on this card (A = task liveness hold 300 s, sessions 30 "
    "s, 52.7%; B = task hold 180 s, sessions 30 s, 51.1%; C = keep 30/30 and accept ~42%). Unblock = I apply the "
    "pick to PR #126, get an independent review, land and deploy it; archive = drop card N and close the PR.\"\n\n"
    "You can reply here and I'll pass it on. If you don't reply, the PR stays as a draft and the hub keeps sending "
    "every change. The rest of the hub CPU work goes on without it.")
BLOCKED_LOG = ['-> cross_task({"action": "read", "task_id": "t_01a1d59e543673b0", "gateway": "worker-6"})']


def test_blocked_kanban_notice_is_not_a_progress_ask():
    assert rc.notice_turn(BLOCKED_ASK) and rc.automation_prompt(BLOCKED_ASK)
    assert rc.status_only(BLOCKED_ASK, BLOCKED_REPLY, BLOCKED_LOG) is None


# Every gateway.kanban.ping notice opens "<glyph> {head}" (head = "[board] @assignee Kanban t_x", board and assignee
# optional; changes_requested writes "{board_tag}Kanban {task_id}" with no assignee). Listing glyphs one at a time is
# how ⏸ was missed here (and ⏸/⏳ in self_heal before it), so every one is pinned, with and without U+FE0F.
KANBAN_PING_GLYPHS = ("✔", "✓", "👀", "🛑", "⛔", "⏱", "⏸", "✖", "🔄", "⏳", "⚠", "ℹ")


def test_every_kanban_ping_glyph_and_head_shape_is_a_notice():
    heads = ("[hub:default] @default Kanban t_01a1d59e543673b0", "@default Kanban t_01a1d59e543673b0",
             "[default] Kanban t_01a1d59e543673b0", "Kanban t_01a1d59e543673b0")
    for glyph in KANBAN_PING_GLYPHS:
        for sel in ("", "\ufe0f"):
            for head in heads:
                ask = f"{glyph}{sel} {head} blocked — can we land and deploy it? why is it still waiting?"
                assert rc.notice_turn(ask), ask
                assert rc.status_only(ask, "Checked it; still waiting.", BLOCKED_LOG) is None, ask


def test_every_core_kanban_ping_template_is_a_notice():
    """Render core's own gateway.kanban.ping templates when core is installed, so a new notice glyph fails here."""
    import os
    import pytest
    root = Path(os.environ.get("HERMES_TEST_CORE") or Path.home() / ".hermes/hermes-agent")
    locale = root / "locales" / "en.yaml"
    if not (root / "agent/context_compressor.py").exists():
        if os.environ.get("HERMES_TEST_CORE"):
            raise AssertionError(f"HERMES_TEST_CORE set but {root} has no agent/context_compressor.py")
        pytest.skip("Hermes core checkout not found")
    import re
    text = locale.read_text(encoding="utf-8")
    block = text.split("\n    ping:\n", 1)[1]
    templates = []
    for line in block.splitlines():
        if line.strip() and not line.startswith("      "):
            break
        m = re.match(r'\s+\w+: "(.*)"$', line)
        if m and ("{head}" in m.group(1) or "Kanban {task_id}" in m.group(1)) and not m.group(1).startswith("{"):
            templates.append(m.group(1))
    assert len(templates) >= 8, templates
    for tpl in templates:
        ask = re.sub(r"\{(\w+)\}", lambda m: {"head": "[hub:default] @default Kanban t_01a1d59e543673b0",
                                               "board_tag": "[hub:default] ", "task_id": "t_01a1d59e543673b0"}
                     .get(m.group(1), "x"), tpl)
        assert rc.notice_turn(ask), tpl


def test_human_question_quoting_a_notice_stays_a_human_ask():
    ask = "why is ⏸ [hub:default] @default Kanban t_01a1d59e543673b0 still blocked?"
    assert not rc.notice_turn(ask)
    assert rc.status_only(ask, "Checked it; it's still waiting on review.", BLOCKED_LOG)


def test_symbol_prefixed_human_kanban_question_requires_a_card_id_for_a_notice():
    ask = "📌 Kanban looks stuck — why are we still waiting?"
    assert not rc.notice_turn(ask)
    assert rc.status_only(ask, "Checked it; it's still waiting on review.", BLOCKED_LOG)


def test_wall_after_first_150_chars_but_inside_opening_window_is_exempt():
    wall = "A choice only you can make is needed. "
    reply = ("Checked the card. " * 30) + wall + ("Still waiting on review. " * 150)
    assert len(reply) > 3000
    assert 200 < reply.index(wall) < 1400
    assert not rc.WALL_RE.search(reply[:150])
    assert not rc.WALL_RE.search(reply[-1500:])
    assert rc.status_only("why are we still waiting?", reply, BLOCKED_LOG) is None


def test_wall_just_past_opening_window_does_not_exempt_status_only():
    wall = "A choice only you can make is needed. "
    reply = "Checked the card. ".ljust(1501) + wall + ("Still waiting on review. " * 150)
    assert len(reply) > 3000
    assert reply.index(wall) == 1501
    assert not rc.WALL_RE.search(reply[:1500])
    assert not rc.WALL_RE.search(reply[-1500:])
    assert rc.status_only("why are we still waiting?", reply, BLOCKED_LOG)


def test_quiet_when_a_long_reply_names_the_wall_in_its_opening():
    """status_plain asks for the one thing stopping it in the first paragraph; status_only must read that opening."""
    ask = "why are we still waiting on the hub CPU work?"
    assert len(BLOCKED_REPLY) > 1500 and not rc.WALL_RE.search(BLOCKED_REPLY[-1500:])
    assert rc.status_only(ask, BLOCKED_REPLY, BLOCKED_LOG) is None
