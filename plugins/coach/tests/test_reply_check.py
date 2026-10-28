import importlib.util
import json
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]


def _mod(name):
    spec = importlib.util.spec_from_file_location(f"t_{name}", HERE / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


rc = _mod("reply_check")


def test_prefilter_catches_each_pattern_and_skips_plain_reports():
    assert "handoff" in rc.prefilter("Open System Settings and click Allow, then let me know when it's done.")
    assert "handoff" in rc.prefilter("1. Click **Install to Workspace**\n2. Copy the token")
    assert "confirm" in rc.prefilter("The fix is ready on a branch.\n\nWant me to run the tests and merge it?")
    assert "unverified" in rc.prefilter("Fixed. The sidebar now scrolls.")
    assert rc.prefilter("Here are the three options I found, with their trade-offs.") == []


def _judge(verdict):
    calls = []

    def llm(system, prompt):
        calls.append(prompt)
        return json.dumps(verdict)
    return llm, calls


REAL_MISSED_HANDOFFS = [  # verbatim from replies the old prefilter passed straight through (2026-10-21)
    "**What you need to do:** in #quakes, send:",
    "If I'm not in #quakes yet, `/invite @Alpha` first, since it's a private channel.",
    "**Cleanup for you:** revoke the dead clients `alpha-slack-bot` and `alpha-hermes`.",
    "That's yours to do, because my sandbox has no AWS access.",
    "**To unblock:** someone in #quakes runs `/invite @softmaxbot`.",
    "One thing may still need you: if Alpha isn't a member of the private #quakes channel, only a channel member can invite it.",
    "That's an owner command that needs AWS SSO, so it's one I can't run: `token_broker_client.py revoke`.",
    "Revoking is an owner command and needs your AWS SSO session.",
]


def test_prefilter_catches_statement_shaped_handoffs():
    for s in REAL_MISSED_HANDOFFS:
        assert "handoff" in rc.prefilter(s), s


def test_prefilter_statement_patterns_skip_plain_reports():
    for s in ["I revoked both clients and posted to #quakes (permalink below).",
              "Alpha is already in #quakes, so I posted directly.",
              "The worker needs your-org's token scope, which the broker grants."]:
        assert "handoff" not in rc.prefilter(s), s


def test_check_fires_only_over_threshold_and_on_prefiltered_rules():
    llm, calls = _judge({"handoff": 0.95, "confirm": 0.1, "unverified": 0.9, "evidence": "asks user to click Allow"})
    hit = rc.check("Click Allow, then let me know when you've done it.", "set up slack", ["-> terminal(ls)"], llm)
    assert hit["rule"] == "handoff" and "self-serve" in hit["nudge"] and "click Allow" in hit["nudge"]
    # unverified scored high but wasn't a prefilter candidate for this reply -> not chosen
    assert len(calls) == 1
    llm2, calls2 = _judge({"handoff": 0.5, "confirm": 0.1, "unverified": 0.2, "evidence": ""})
    assert rc.check("Click Allow, then let me know when you've done it.", "x", [], llm2) is None
    llm3, calls3 = _judge({"handoff": 1, "confirm": 1, "unverified": 1, "evidence": ""})
    assert rc.check("Here is the summary you asked for.", "x", [], llm3) is None and calls3 == []  # no LLM call


def test_check_respects_rule_allowlist_and_bad_judge_output():
    llm, _ = _judge({"handoff": 0.99, "confirm": 0.99, "unverified": 0.0, "evidence": "e"})
    reply = "Open Settings and click Allow, then tell me when it's done. Want me to restart it after?"
    assert rc.check(reply, "x", [], llm, only=["confirm"])["rule"] == "confirm"
    assert rc.check(reply, "x", [], lambda s, p: "not json") is None


def test_settings_env(monkeypatch):
    monkeypatch.setenv("COACH_REPLY_RULES", "handoff,bogus")
    monkeypatch.setenv("COACH_REPLY_THRESHOLD", "0.9")
    assert rc.settings()["rules"] == ["handoff"] and rc.settings()["threshold"] == 0.9
    monkeypatch.setenv("COACH_REPLY_RULES", "none")
    assert rc.settings()["rules"] == []


def test_pre_verify_hook_nudges_once_per_turn(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("coach_init_rc", HERE / "__init__.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    core = mod._load("core")
    rcl = mod._load("reply_check")
    monkeypatch.setattr(rcl, "aux_judge", lambda s, p: json.dumps(
        {"handoff": 0.0, "confirm": 0.92, "unverified": 0.0, "evidence": "asks to run tests"}))
    store = core.Store(root=tmp_path / "coach")
    coach, hooks = mod.build(store=store, checkout=None)
    hooks["pre_llm_call"](session_id="s1", user_message="fix the flaky test")
    hooks["post_tool_call"](tool_name="terminal", args={"command": "pytest -x"}, result="{}", session_id="s1")
    reply = "I found the race and patched it.\n\nWant me to run the full suite now?"
    out = hooks["pre_verify"](session_id="s1", attempt=0, final_response=reply, changed_paths=["a.py"])
    assert out["action"] == "continue" and "proceed on reversible work" in out["message"]
    assert hooks["pre_verify"](session_id="s1", attempt=1, final_response=reply) is None
    assert hooks["pre_verify"](session_id="s1", attempt=0, final_response=reply, platform="cron") is None
    logged = [json.loads(l) for l in (tmp_path / "coach" / "log.jsonl").read_text().splitlines()]
    assert any(r["event"] == "reply_nudge" and r["rule"] == "confirm" for r in logged)
    ask, tlog = coach.turn_context("s1")
    assert ask == "fix the flaky test" and tlog and "pytest -x" in tlog[0]


def test_settings_defaults(monkeypatch):
    monkeypatch.delenv("COACH_REPLY_RULES", raising=False)
    monkeypatch.delenv("COACH_REPLY_THRESHOLD", raising=False)
    monkeypatch.delenv("COACH_REPLY_THRESHOLDS", raising=False)
    cfg = rc.settings()
    assert cfg["rules"] == ["handoff", "confirm", "unwatched", "report_only"] and cfg["threshold"] == 0.7
    assert cfg["thresholds"] == {"report_only": 0.5, "report_only_screen": 0.85}


ORPHAN = ("Everything is running.\n\nOnce the last runs finish, around 03:30 UTC, I'll build the tables from the "
          "result files, write the report and merge the PR.")


def test_prefilter_unwatched_promises():
    assert "unwatched" in rc.prefilter(ORPHAN)
    assert "unwatched" in rc.prefilter("Smoke test is green. I'll launch the sweep as soon as CI passes.")
    assert "unwatched" in rc.prefilter("Nothing else to do here; waiting on the eval runs.")
    assert "unwatched" not in rc.prefilter("The runs finished and the tables are below.")


def test_unwatched_needs_no_wake_source_before_the_judge_runs():
    verdict = {"handoff": 0, "confirm": 0, "unverified": 0, "unwatched": 0.9, "evidence": "promises tables at 03:30"}
    llm, calls = _judge(verdict)
    assert rc.check(ORPHAN, "run it", [], llm, wake=lambda: ["process:proc_1"]) is None and calls == []
    assert rc.check(ORPHAN, "run it", [], llm) is None and calls == []  # can't tell -> skip
    assert rc.check(ORPHAN, "run it", [], llm, wake=lambda: 1 / 0) is None and calls == []  # lookup error -> skip
    hit = rc.check(ORPHAN, "run it", [], llm, wake=lambda: [])
    assert hit["rule"] == "unwatched" and "background=true, notify=true" in hit["nudge"] and len(calls) == 1
    low, _ = _judge({**verdict, "unwatched": 0.3})
    assert rc.check(ORPHAN, "run it", [], low, wake=lambda: []) is None


def test_wake_sources_tool_log_and_live_process(tmp_path, monkeypatch):
    import os
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(rc, "_hermes_home", lambda: tmp_path)
    assert rc.wake_sources("s1", ["-> terminal(ls)"]) == []
    assert rc.wake_sources("s1", ["-> delegate_task(train it)"]) == ["tool:delegate_task"]
    (tmp_path / "processes.json").write_text(json.dumps([
        {"session_id": "proc_live", "session_key": "s1", "pid": os.getpid()},
        {"session_id": "proc_other", "session_key": "s2", "pid": os.getpid()},
        {"session_id": "proc_dead", "session_key": "s1", "pid": 2 ** 22 + 12345}]))
    import sys
    monkeypatch.setitem(sys.modules, "tools.process_registry", None)  # force the processes.json fallback
    assert rc.wake_sources("s1", []) == ["process:proc_live"]
    assert rc.wake_sources("s3", []) == []


# --- report_only: the reply ends on a diagnosis of a problem the agent could fix, without fixing it -------------
# Endings from the labeled eval set (report_only + agent_fixable), trimmed to the phrase that carries the signal.
REPORT_ONLY_ENDINGS = [
    "Root cause: the watcher drops the event when the panel is hidden. Diagnosed, not fixed.",
    "You can ignore this prompt. I haven't fixed the panel itself.",
    "Relayed the 98% disk warning to the existing owner.",
    "The card ran, but the automation gap remains unfixed.",
    "No further action is needed from you; the safeguard is still not deployed.",
    "Open: the retry path still drops the last message.",
    "Still open: the sidebar badge is stale after a restart.",
    "**Remaining:** the hub copy is stale.",
    "The fix is merged but not yet live on the MacBook.",
    "The export job is still failing on the second batch.",
    "I haven't established yet whether those cooldowns are justified or stale.",
    "Both posts are still hidden: the blog index doesn't mention either of them.",
    "The outcome baseline remains unmeasured; no new analytics automation was deployed.",
    "Nothing needs you here; the existing card owns it.",
]
FINISHED_WORK = [
    "Fixed: the sidebar now scrolls. I reloaded Desktop and checked it on your session.",
    "Here are the three options I found, with their trade-offs.",
    "Merged and deployed. The tests pass and the live page shows the new label.",
]


def test_prefilter_report_only_catches_real_endings():
    for s in REPORT_ONLY_ENDINGS:
        assert "report_only" in rc.prefilter(s), s


def test_prefilter_report_only_skips_finished_work():
    for s in FINISHED_WORK:
        assert "report_only" not in rc.prefilter(s), s


def test_prefilter_report_only_reads_only_the_tail():
    old = "Earlier the build was not fixed. " + "Then I fixed it and verified the result. " * 60
    assert len(old) > 1600 and "report_only" not in rc.prefilter(old)


def test_parse_verdict_reads_report_only():
    v = rc.parse_verdict('ok {"handoff": 0.1, "confirm": 0, "unverified": 0, "unwatched": 0, "report_only": 0.91, '
                         '"evidence": "ends on not fixed"}')
    assert v["report_only"] == 0.91 and v["evidence"] == "ends on not fixed"
    assert rc.parse_verdict('{"handoff": 0.2, "evidence": ""}')["report_only"] == 0.0  # older judge output
    assert '"report_only"' in rc.JUDGE_SYS and "report_only" in rc.RULES and "report_only" in rc.DEFAULT_RULES


REPORT_ASK = "what am I supposed to do with this?"
REPORT_REPLY = "You can ignore this prompt. The panel is stale because its cache never refreshes. I haven't fixed the panel itself."


def test_check_fires_report_only_with_spec_nudge():
    llm, calls = _judge({"handoff": 0, "confirm": 0, "unverified": 0, "unwatched": 0, "report_only": 0.9,
                         "evidence": "ends on 'I haven't fixed the panel'"})
    hit = rc.check(REPORT_REPLY, REPORT_ASK, ["-> terminal(ls)"], llm, threshold=0.7,
                   thresholds=rc.settings()["thresholds"])
    assert hit["rule"] == "report_only" and len(calls) == 1
    assert hit["nudge"].startswith("[coach] Your message ends on a problem you could fix (ends on 'I haven't fixed")
    assert "kanban_create with acceptance criteria" in hit["nudge"] and "Don't end on the diagnosis." in hit["nudge"]


def test_check_report_only_respects_its_own_threshold():
    th = rc.settings()["thresholds"]["report_only_screen"]  # no second opinion: the screen's own threshold
    llm, _ = _judge({"report_only": max(0.0, th - 0.05), "evidence": "e"})
    assert rc.check(REPORT_REPLY, REPORT_ASK, [], llm, threshold=0.0, only=["report_only"],
                    thresholds=rc.settings()["thresholds"]) is None


def test_thresholds_env_override(monkeypatch):
    monkeypatch.setenv("COACH_REPLY_THRESHOLDS", "report_only=0.95,bogus=0.1,handoff=x")
    assert rc.settings()["thresholds"] == {"report_only": 0.95, "report_only_screen": 0.85}


SCOPE_LIMITED_ASKS = [  # explicit limits: a diagnosis is exactly what was asked for
    "Analysis only: why is the sidebar badge stale?",
    "just tell me why the export job fails, don't fix it",
    "Plan first. What is wrong with the retry path?",
    "Don't change anything, I only want to know why CI is red.",
    "Diagnose the flaky test but make no edits; report evidence only.",
    "Read-only: figure out why the hub disk is at 98%.",
]


def test_scope_limited_ask_never_fires_report_only_and_skips_the_judge():
    llm, calls = _judge({"report_only": 1.0, "evidence": "e"})
    for ask in SCOPE_LIMITED_ASKS:
        assert rc.scope_limited(ask), ask
        assert rc.check(REPORT_REPLY, ask, [], llm, only=["report_only"]) is None, ask
    assert calls == []
    for ask in [REPORT_ASK, "why is this red?", "fix the export job", "is it stuck?"]:
        assert not rc.scope_limited(ask), ask


def test_physical_step_reply_is_left_to_the_judge():
    reply = "The sensor is not fixed: its battery is dead. Swap the battery in the hall sensor; nothing else is open."
    llm, calls = _judge({"handoff": 0.1, "report_only": 0.2, "evidence": "needs a physical battery swap"})
    assert "report_only" in rc.prefilter(reply)
    assert rc.check(reply, "why is the hall sensor offline?", [], llm, thresholds=rc.settings()["thresholds"]) is None
    assert len(calls) == 1


def test_pre_verify_hook_holds_a_report_only_reply(tmp_path, monkeypatch):
    monkeypatch.delenv("COACH_REPLY_RULES", raising=False)
    spec = importlib.util.spec_from_file_location("coach_init_ro", HERE / "__init__.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    core = mod._load("core")
    rcl = mod._load("reply_check")
    monkeypatch.setattr(rcl, "aux_judge", lambda s, p: json.dumps(
        {"handoff": 0.0, "confirm": 0.0, "unverified": 0.0, "unwatched": 0.0, "report_only": 0.93,
         "evidence": "stops at 'haven't fixed the panel'"}))
    store = core.Store(root=tmp_path / "coach")
    coach, hooks = mod.build(store=store, checkout=None)
    hooks["pre_llm_call"](session_id="s9", user_message=REPORT_ASK)
    out = hooks["pre_verify"](session_id="s9", attempt=0, final_response=REPORT_REPLY)
    assert out and out["action"] == "continue" and "Don't end on the diagnosis." in out["message"]
    logged = [json.loads(l) for l in (tmp_path / "coach" / "log.jsonl").read_text().splitlines()]
    assert any(r["event"] == "reply_nudge" and r["rule"] == "report_only" for r in logged)


# --- report_only cascade: the screen's p >= CONFIRM_FLOOR gets a second opinion on the strong model ----------------
def _ro_verdict(p):
    return {"handoff": 0, "confirm": 0, "unverified": 0, "unwatched": 0, "report_only": p, "evidence": "screen"}


def test_confirm_replaces_the_screen_p_and_can_veto_it():
    llm, calls = _judge(_ro_verdict(0.9))
    asked = []
    ths = rc.settings()["thresholds"]
    hit = rc.check(REPORT_REPLY, REPORT_ASK, [], llm, only=["report_only"], thresholds=ths,
                   confirm=lambda: asked.append(1) or (0.9, "confirmed: no fix started"), confirm_floor=rc.CONFIRM_FLOOR)
    assert hit and hit["p"] == 0.9 and hit["verdict"]["report_only_src"] == "confirm" and "confirmed: no fix" in hit["nudge"]
    assert rc.check(REPORT_REPLY, REPORT_ASK, [], llm, only=["report_only"], thresholds=ths,
                    confirm=lambda: (0.1, "fix was sent to the owner"), confirm_floor=rc.CONFIRM_FLOOR) is None
    # a screen p below the floor never reaches the second opinion
    llm2, _ = _judge(_ro_verdict(0.6))
    assert rc.check(REPORT_REPLY, REPORT_ASK, [], llm2, only=["report_only"], thresholds=ths,
                    confirm=lambda: asked.append("below floor") or (0.9, "x"), confirm_floor=rc.CONFIRM_FLOOR) is None
    assert "below floor" not in asked


def test_confirm_unavailable_falls_back_to_the_stricter_screen_threshold():
    ths = rc.settings()["thresholds"]
    for p, fires in ((0.80, False), (0.90, True)):  # 0.80 clears the floor but not report_only_screen (0.85)
        llm, _ = _judge(_ro_verdict(p))
        hit = rc.check(REPORT_REPLY, REPORT_ASK, [], llm, only=["report_only"], thresholds=ths,
                       confirm=lambda: None, confirm_floor=rc.CONFIRM_FLOOR)
        assert bool(hit) is fires
        # without a confirm callable at all the screen is held to the same screen threshold
        assert bool(rc.check(REPORT_REPLY, REPORT_ASK, [], llm, only=["report_only"], thresholds=ths)) is fires


def test_parse_confirm_reads_the_strong_models_label():
    assert rc.parse_confirm('{"outcome": "report_only", "agent_fixable": true, "quote": "not fixed"}') == (0.9, "not fixed")
    for o in ('{"outcome": "started", "agent_fixable": true}', '{"outcome": "report_only", "agent_fixable": false}', "nope"):
        r = rc.parse_confirm(o)
        assert r is None or r[0] == 0.1
    assert "kanban_create" in rc.CONFIRM_SYS and "report_only" in rc.CONFIRM_SYS


def test_pre_verify_hook_runs_the_confirm_with_real_args_and_honours_the_daily_cap(tmp_path, monkeypatch):
    monkeypatch.delenv("COACH_REPLY_RULES", raising=False)
    monkeypatch.setenv("COACH_REPORT_ONLY_CONFIRM_CAP", "2")
    spec = importlib.util.spec_from_file_location("coach_init_ro2", HERE / "__init__.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    core = mod._load("core")
    rcl = mod._load("reply_check")
    monkeypatch.setattr(rcl, "aux_judge", lambda s, p: json.dumps(_ro_verdict(0.93)))
    prompts = []
    monkeypatch.setattr(rcl, "aux_confirm", lambda s, p: prompts.append(p) or json.dumps(
        {"outcome": "report_only", "agent_fixable": True, "quote": "stops at the diagnosis"}))
    store = core.Store(root=tmp_path / "coach")
    coach, hooks = mod.build(store=store, checkout=None)
    for i in range(3):
        sid = f"s{i}"
        hooks["pre_llm_call"](session_id=sid, user_message=REPORT_ASK)
        hooks["post_tool_call"](tool_name="kanban_create", args={"title": "Fix stale panel", "body": "cache never refreshes"},
                                result="ok", session_id=sid)
        out = hooks["pre_verify"](session_id=sid, attempt=0, final_response=REPORT_REPLY)
        assert out and out["action"] == "continue"  # the third falls back to the screen (0.93 >= 0.85)
    assert len(prompts) == 2 and "Fix stale panel" in prompts[0] and "cache never refreshes" in prompts[0]
    logged = [json.loads(l) for l in (tmp_path / "coach" / "log.jsonl").read_text().splitlines()]
    assert sum(r["event"] == "report_only_confirm" for r in logged) == 2
    assert sum(r["event"] == "report_only_confirm_capped" for r in logged) == 1
