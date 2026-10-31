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
    assert cfg["rules"] == ["handoff", "confirm", "unwatched", "report_only", "status_only", "denied_ask",
                            "credit_ask", "laptop_job"] and cfg["threshold"] == 0.7
    assert cfg["thresholds"] == {"report_only": 0.5, "report_only_screen": 0.85, "report_only_capped": 0.92}


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
    # bot-dc58243931 (2026-10-27 MacBook): a known leftover plus a "press this button" workaround, no fix.
    "O-7 through O-11 have the same old-approval problem. If the scheduled job holds one of them, pressing "
    "'Post now' on its missed-post card will work now.",
    "The same problem applies to the nightly export.",
    "The staging and canary hosts are also affected.",
    "If it stalls again, running hermes-sync run will work.",
    "You can click Retry on the card to post it.",
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


# Finished work that mentions the same problem: the prefilter may pass it on (recall-oriented), but the judge stage
# must score it low, so check() never nudges it.
SAME_PROBLEM_FIXED = "O-7 through O-11 had the same problem; I fixed them too and the check now returns post."


def test_same_problem_fixed_is_not_nudged_by_the_judge_stage():
    llm, calls = _judge({"handoff": 0, "confirm": 0, "unverified": 0, "unwatched": 0, "report_only": 0.05,
                         "evidence": ""})
    hit = rc.check(SAME_PROBLEM_FIXED, "so, can you fix it please", ["-> terminal(python3 fix.py)"], llm,
                   threshold=0.7, thresholds=rc.settings()["thresholds"])
    assert hit is None


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


# Replay of Desktop chat 20261027_210007_45baaa's 23:41Z turn: its kanban_show failed with the stale-view "corrupt"
# error (state.db message 191294), and the reply ended on a report. kanban_create from that process fails the same
# way, so the nudge must not ask for it.
KDB_ERR = ('{"error": "kanban_show: Refusing to open corrupt kanban DB at /home/ubuntu/.hermes/kanban.db: '
           'integrity_check returned \'*** in database main ***\\\\nTree 13 page 13 cell 45: Rowid 1997 out of order\'. '
           'Original preserved; backup at /home/ubuntu/.hermes/kanban.db.corrupt.a18094adddde6e53.bak."}')
KDB_LOG = ["-> process_manage(proc_38da1880ef4b)", "-> kanban_show({\"task_id\": \"t_01a17f92364bdd8e\"})",
           f"   <- ERROR {KDB_ERR[:120]}"]
KDB_REPLY = ("Separately, since about 4:28pm PT the hub's task board database has been failing its integrity checks, "
             "on both the main board and the runners board. Every failed read saves a 50 MB backup copy; that's about "
             "500 MB so far, with 95 GB free. Several other hub sessions are already on it. I haven't touched it.")


def test_report_only_nudge_when_kanban_db_is_down_asks_for_owner_or_restart():
    assert rc.kanban_db_down(KDB_LOG)
    llm, _ = _judge({"report_only": 0.9, "evidence": "reports corrupt kanban DB, I haven't touched it"})
    hit = rc.check(KDB_REPLY, "follow emmett's instructions", KDB_LOG, llm, threshold=0.7,
                   thresholds=rc.settings()["thresholds"])
    assert hit["rule"] == "report_only"
    assert "kanban_create with acceptance criteria" not in hit["nudge"]
    assert "name the owner card" in hit["nudge"] and "ask for the restart" in hit["nudge"]
    assert "pragma quick_check" in hit["nudge"] and "action_request" in hit["nudge"]


def test_report_only_nudge_unchanged_without_kanban_failure():
    assert not rc.kanban_db_down(["-> kanban_show(t_x)", "-> terminal(ls)", "   <- ERROR {\"error\": \"not found\"}"])
    assert rc.nudge_for("report_only", "e") == rc.NUDGES["report_only"].format(evidence="e")
    assert "kanban_create with acceptance criteria" in rc.nudge_for("report_only", "e", kanban_down=False)
    assert rc.nudge_for("handoff", "e", kanban_down=True) == rc.NUDGES["handoff"].format(evidence="e")


def test_owner_nudge_points_to_terminal_lookup_when_kanban_db_is_down():
    reply = "The board errors are another session's problem; the existing owner has it."
    log = ["-> terminal(ls)", f"   <- ERROR {KDB_ERR[:120]}"]
    llm, _ = _judge({"report_only": 0.0, "evidence": ""})
    hit = rc.check(reply, "why is it red?", log, llm, threshold=0.7)
    assert hit is not None and hit.get("reason") == "unverified_owner"
    assert "hermes kanban show" in hit["nudge"]
    plain = rc.check(reply, "why is it red?", ["-> terminal(ls)"], llm, threshold=0.7)
    assert plain is not None and plain.get("reason") == "unverified_owner"
    assert "hermes kanban show" not in plain["nudge"]


def test_check_report_only_respects_its_own_threshold():
    th = rc.settings()["thresholds"]["report_only_screen"]  # no second opinion: the screen's own threshold
    llm, _ = _judge({"report_only": max(0.0, th - 0.05), "evidence": "e"})
    assert rc.check(REPORT_REPLY, REPORT_ASK, [], llm, threshold=0.0, only=["report_only"],
                    thresholds=rc.settings()["thresholds"]) is None


def test_thresholds_env_override(monkeypatch):
    monkeypatch.setenv("COACH_REPLY_THRESHOLDS", "report_only=0.95,bogus=0.1,handoff=x,report_only_capped=0.97")
    assert rc.settings()["thresholds"] == {"report_only": 0.95, "report_only_screen": 0.85, "report_only_capped": 0.97}


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
    # the second opinion confirms (an unconfirmed screen score no longer nudges: agent-f668487724)
    monkeypatch.setattr(rcl, "aux_confirm", lambda s, p: json.dumps(
        {"outcome": "report_only", "agent_fixable": True, "quote": "haven't fixed the panel"}))
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
    # without a confirm callable at all the screen is held to report_only_screen (0.85)
    for p, fires in ((0.80, False), (0.88, True)):
        llm, _ = _judge(_ro_verdict(p))
        assert bool(rc.check(REPORT_REPLY, REPORT_ASK, [], llm, only=["report_only"], thresholds=ths)) is fires
    # a second opinion was due but unavailable (daily cap hit, error): the screen alone needs report_only_capped
    # (0.92). agent-fb58ec3a8f: a capped confirm let a 0.88 screen nudge, where confirms clear 43% at >= 0.85.
    for p, fires in ((0.80, False), (0.88, False), (0.93, True)):
        llm, _ = _judge(_ro_verdict(p))
        audit = []
        hit = rc.check(REPORT_REPLY, REPORT_ASK, [], llm, only=["report_only"], thresholds=ths,
                       confirm=lambda: None, confirm_floor=rc.CONFIRM_FLOOR, audit=audit.append,
                       report_only_fallback="nudge")  # the opt-in old behaviour (default skip: agent-f668487724)
        assert bool(hit) is fires
        assert audit and audit[0]["source"] == "screen_fallback" and audit[0]["threshold"] == 0.92


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
        if i < 2:
            assert out and out["action"] == "continue"  # confirmed by the second opinion
        else:
            # agent-f668487724: past the cap the screen alone (0.93) no longer nudges by default
            assert out is None
    assert len(prompts) == 2 and "Fix stale panel" in prompts[0] and "cache never refreshes" in prompts[0]
    logged = [json.loads(l) for l in (tmp_path / "coach" / "log.jsonl").read_text().splitlines()]
    assert sum(r["event"] == "report_only_confirm" for r in logged) == 2
    assert sum(r["event"] == "report_only_confirm_capped" for r in logged) == 1
    scored = [r for r in logged if r["event"] == "report_only_scored"]
    assert scored[-1]["source"] == "screen_fallback" and scored[-1]["suppressed"] == "unconfirmed"
    assert scored[-1]["passing"] is False
    # COACH_REPORT_ONLY_FALLBACK=nudge restores the old screen fallback (held to report_only_capped 0.92)
    monkeypatch.setenv("COACH_REPORT_ONLY_FALLBACK", "nudge")
    hooks["pre_llm_call"](session_id="s9", user_message=REPORT_ASK)
    out = hooks["pre_verify"](session_id="s9", attempt=0, final_response=REPORT_REPLY)
    assert out and out["action"] == "continue"


# --- report_only, unverified owner: the reply hands a problem to another session/card/worker that this turn never
# checked or messaged. A deterministic check fires before the judge (the judge scored the real case 0.60 / 0.75).
def owner_fixtures():
    rows = json.loads((HERE / "tests" / "fixtures" / "report_only_unverified_owner.json").read_text())
    by_id = {r["id"]: r for r in rows}
    for r in rows:
        r.setdefault("reply", by_id.get(r.get("reply_from"), {}).get("reply", ""))
        r.setdefault("log", by_id.get(r.get("log_from"), {}).get("log", []))
    return rows


def _owner(fid):
    return next(r for r in owner_fixtures() if r["id"] == fid)


def test_unverified_owner_handoff_fires_without_the_judge():
    """Replays the 2026-10-27 hub turn: a stub judge at 0.6 (below every bar) used to let it end."""
    f = _owner("owner-real-20261027")
    llm, calls = _judge(_ro_verdict(0.6))
    hit = rc.check(f["reply"], f["user"], f["log"], llm, threshold=0.7, thresholds=rc.settings()["thresholds"],
                   confirm=lambda: None, confirm_floor=rc.CONFIRM_FLOOR)
    assert hit and hit["rule"] == "report_only" and hit.get("reason") == "unverified_owner"
    assert calls == []  # no AI call
    assert "leaving them to that session" in hit["evidence"].lower() or "not from my change" in hit["evidence"].lower()
    assert "rerun" in hit["nudge"].lower() and "cross_session" in hit["nudge"]


def test_unverified_owner_fixtures_match_expectations():
    llm, _ = _judge(_ro_verdict(0.6))
    for f in owner_fixtures():
        hit = rc.check(f["reply"], f["user"], f["log"], llm, threshold=0.7, thresholds=rc.settings()["thresholds"])
        assert bool(hit and hit.get("reason") == "unverified_owner") is f["expect"], f["id"]


def test_unverified_owner_with_a_send_leaves_it_to_the_judge():
    f = _owner("owner-real-with-send")
    llm, calls = _judge(_ro_verdict(0.6))
    assert rc.check(f["reply"], f["user"], f["log"], llm, thresholds=rc.settings()["thresholds"]) is None
    assert len(calls) == 1  # fell through to the normal judge, which scored it below the bar


def test_unverified_owner_respects_analysis_only_and_the_rule_switch():
    f = _owner("owner-real-analysis-only")
    llm, calls = _judge(_ro_verdict(0.6))
    assert rc.check(f["reply"], f["user"], f["log"], llm, thresholds=rc.settings()["thresholds"]) is None
    real = _owner("owner-real-20261027")
    assert rc.check(real["reply"], real["user"], real["log"], llm, only=["handoff", "confirm"]) is None


def test_pre_verify_logs_unverified_owner_and_normal_endings_stay_quiet(tmp_path, monkeypatch):
    monkeypatch.delenv("COACH_REPLY_RULES", raising=False)
    spec = importlib.util.spec_from_file_location("coach_init_owner", HERE / "__init__.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    core = mod._load("core")
    rcl = mod._load("reply_check")
    judged = []
    monkeypatch.setattr(rcl, "aux_judge", lambda s, p: judged.append(p) or json.dumps(_ro_verdict(0.6)))
    monkeypatch.setattr(rcl, "aux_confirm", lambda s, p: json.dumps({"outcome": "na", "agent_fixable": None}))
    store = core.Store(root=tmp_path / "coach")
    coach, hooks = mod.build(store=store, checkout=None)
    f = _owner("owner-real-20261027")
    hooks["pre_llm_call"](session_id="so1", user_message=f["user"])
    for cmd in ("python3 -m unittest discover -s tests", "cat state.json"):
        hooks["post_tool_call"](session_id="so1", tool_name="terminal", args={"command": cmd}, result='{"exit_code": 0}')
    out = hooks["pre_verify"](session_id="so1", attempt=0, final_response=f["reply"])
    assert out and out["action"] == "continue" and "cross_session" in out["message"]
    hooks["pre_llm_call"](session_id="so2", user_message="rerun the tests")
    green = _owner("all-green")
    assert hooks["pre_verify"](session_id="so2", attempt=0, final_response=green["reply"]) is None
    logged = [json.loads(l) for l in (tmp_path / "coach" / "log.jsonl").read_text().splitlines()]
    nudges = [r for r in logged if r["event"] == "reply_nudge"]
    assert [(r["session_id"], r["rule"], r.get("reason")) for r in nudges] == [("so1", "report_only", "unverified_owner")]
    assert any(r["event"] == "reply_checked" and r["session_id"] == "so2" for r in logged)


def test_unverified_owner_skips_quoted_phrases_and_counts_cli_owner_checks():
    # a postmortem quoting the phrase is not a handoff (real hub reply 189017, the self-heal proposal for this rule)
    quoted = 'The reply said "not from my change ... I\'m leaving them to that session" and stopped.'
    assert rc.unverified_owner(quoted, []) is None
    # the 1500-char tail can start mid-quote: the closing quote mark still marks it quoted (real hub reply 189017)
    assert rc.unverified_owner('t session", "not from my change", "another session is landing"), and', []) is None
    # `hermes kanban --board runners comment t_x` in a terminal call is an owner message (real hub reply 133352)
    reply = "All three fixes belong to the t_26e528fa worker; it's that card's to fix."
    assert rc.unverified_owner(reply, []) is not None
    assert rc.unverified_owner(reply, ["-> terminal(hermes kanban --board runners comment t_26e528fa 'repro: ...')"]) is None
    # a plain terminal call that only mentions a tool name is not an owner check
    assert rc.unverified_owner(reply, ["-> terminal(grep -rn kanban_show plugins/)"]) is not None


def _hooks_with_judge(tmp_path, monkeypatch, verdicts):
    """build() with a scripted judge: each call pops the next verdict. Returns (hooks, calls, log_rows)."""
    spec = importlib.util.spec_from_file_location("coach_init_rc_outcome", HERE / "__init__.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    core = mod._load("core")
    rcl = mod._load("reply_check")
    calls = []

    def judge(s, p):
        calls.append(p)
        return json.dumps(verdicts[min(len(calls), len(verdicts)) - 1])
    monkeypatch.setattr(rcl, "aux_judge", judge)
    monkeypatch.setenv("COACH_REPLY_RULES", "handoff")
    monkeypatch.setenv("COACH_SYSTEM_ONE", "off")
    store = core.Store(root=tmp_path / "coach")
    _coach, hooks = mod.build(store=store, checkout=None)
    rows = lambda: [json.loads(l) for l in (tmp_path / "coach" / "log.jsonl").read_text().splitlines()]  # noqa
    return hooks, calls, rows


HANDOFF_DRAFT = "To finish, sign in to the Telnyx portal and paste the API key here, then let me know."
HANDOFF_FINAL = ("I checked directly: Telnyx redirects to login and the vault has no Telnyx credential, so that one "
                 "sign-in needs your hands: sign in to the Telnyx portal, then tell me when it's done.")


def test_pre_verify_logs_one_handoff_outcome_after_a_nudge_and_never_nudges_again(tmp_path, monkeypatch):
    hooks, calls, rows = _hooks_with_judge(tmp_path, monkeypatch, [
        {"handoff": 0.9, "confirm": 0, "unverified": 0, "unwatched": 0, "report_only": 0, "evidence": "asks sign-in"},
        {"handoff": 0.2, "confirm": 0, "unverified": 0, "unwatched": 0, "report_only": 0, "evidence": "legit"}])
    hooks["pre_llm_call"](session_id="s1", user_message="get me a telnyx number")
    out = hooks["pre_verify"](session_id="s1", attempt=0, final_response=HANDOFF_DRAFT)
    assert out["action"] == "continue" and len(calls) == 1
    assert hooks["pre_verify"](session_id="s1", attempt=1, final_response=HANDOFF_FINAL) is None
    assert len(calls) == 2  # exactly one outcome judgement
    outs = [r for r in rows() if r["event"] == "reply_nudge_outcome"]
    nudge = [r for r in rows() if r["event"] == "reply_nudge"][0]
    assert len(outs) == 1 and outs[0]["rule"] == "handoff" and outs[0]["session_id"] == "s1"
    assert outs[0]["resolved"] is True and outs[0]["p"] == 0.2
    assert outs[0].get("draft_id") == nudge.get("draft_id")
    # a further attempt in the same turn neither re-judges nor logs a second outcome
    assert hooks["pre_verify"](session_id="s1", attempt=2, final_response=HANDOFF_FINAL) is None
    assert len(calls) == 2 and len([r for r in rows() if r["event"] == "reply_nudge_outcome"]) == 1


def test_pre_verify_outcome_unresolved_when_the_revision_still_hands_off(tmp_path, monkeypatch):
    v = {"handoff": 0.95, "confirm": 0, "unverified": 0, "unwatched": 0, "report_only": 0, "evidence": "still asks"}
    hooks, calls, rows = _hooks_with_judge(tmp_path, monkeypatch, [v, v])
    hooks["pre_llm_call"](session_id="s2", user_message="get me a telnyx number")
    assert hooks["pre_verify"](session_id="s2", attempt=0, final_response=HANDOFF_DRAFT)["action"] == "continue"
    assert hooks["pre_verify"](session_id="s2", attempt=1, final_response=HANDOFF_DRAFT) is None
    outs = [r for r in rows() if r["event"] == "reply_nudge_outcome"]
    assert len(outs) == 1 and outs[0]["resolved"] is False and outs[0]["p"] == 0.95


def test_pre_verify_attempt_one_without_a_handoff_nudge_makes_no_judge_call(tmp_path, monkeypatch):
    hooks, calls, rows = _hooks_with_judge(tmp_path, monkeypatch, [
        {"handoff": 0.9, "confirm": 0, "unverified": 0, "unwatched": 0, "report_only": 0, "evidence": "x"}])
    hooks["pre_llm_call"](session_id="s3", user_message="get me a telnyx number")
    assert hooks["pre_verify"](session_id="s3", attempt=1, final_response=HANDOFF_DRAFT) is None
    assert calls == []
    # and a nudge from an earlier turn does not leak into the next turn's attempt 1
    hooks["pre_verify"](session_id="s3", attempt=0, final_response=HANDOFF_DRAFT)
    hooks["pre_llm_call"](session_id="s3", user_message="next question")
    n = len(calls)
    assert hooks["pre_verify"](session_id="s3", attempt=1, final_response=HANDOFF_DRAFT) is None
    assert len(calls) == n


# ---- denied_ask (self-heal agent-8cecfe01cb): a reply says no ask is pending while a linked card waits on the user
_ASK = {"action": "Confirm GitHub access for askevepodcast (passkey or emailed code)", "why_human": "2FA",
        "interaction": "external", "scope": "github login", "evidence": "sudo-mode prompt"}


def _zero_judge(*_):
    return json.dumps({r: 0.0 for r in rc.RULES} | {"evidence": ""})


def test_reply_check_denies_ask_while_card_human_blocked():
    cards = [{"id": "t_f68", "title": "askevepodcast GitHub", "status": "blocked", "block_kind": "needs_input",
              "human_request": json.dumps(_ASK)}]
    reply = "The worker is still running the Ask Eve setup; there's no new approval request for you."
    called = []
    hit = rc.check(reply, "is it stuck?", [], lambda s, p: called.append(1) or _zero_judge(),
                   human_asks=lambda: rc.linked_human_asks(cards))
    assert hit and hit["rule"] == "denied_ask" and hit["p"] == 1.0
    assert _ASK["action"] in hit["nudge"] and "askevepodcast GitHub" in hit["nudge"]
    assert not called  # deterministic: no judge call


def test_reply_check_denied_ask_negative_when_card_running():
    cards = [{"id": "t_f68", "title": "askevepodcast GitHub", "status": "running", "block_kind": None,
              "human_request": json.dumps(_ASK)}]
    reply = "The worker is still running the Ask Eve setup; there's no new approval request for you."
    assert rc.check(reply, "is it stuck?", [], _zero_judge, human_asks=lambda: rc.linked_human_asks(cards)) is None


def test_denied_ask_phrases():
    for text in ("No approval needed from you.", "Nothing needs you right now.", "There's no new approval request.",
                 "Nothing is blocked on you.", "No action required on your side.", "nothing for you to do"):
        assert rc.denies_ask(text), text
    for text in ("Card t_1 needs your approval: click Allow.", "No tests failed.", "The approval request is below."):
        assert not rc.denies_ask(text), text


def test_pre_verify_holds_denied_ask_once(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("coach_init_rc_da", HERE / "__init__.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    core = mod._load("core")
    rcl = mod._load("reply_check")
    monkeypatch.setattr(rcl, "aux_judge", _zero_judge)
    monkeypatch.setattr(rcl, "session_human_asks", lambda sid: [
        {"id": "t_f68", "title": "askevepodcast GitHub", "action": _ASK["action"]}] if sid == "s1" else [])
    store = core.Store(root=tmp_path / "coach")
    coach, hooks = mod.build(store=store, checkout=None)
    hooks["pre_llm_call"](session_id="s1", user_message="what is this blocked on")
    reply = "It's running; no approval needed from you."
    out = hooks["pre_verify"](session_id="s1", attempt=0, final_response=reply)
    assert out and out["action"] == "continue" and _ASK["action"] in out["message"]
    assert hooks["pre_verify"](session_id="s1", attempt=1, final_response=reply) is None


def test_session_human_asks_reads_local_human_request_from_the_board(tmp_path, monkeypatch):
    """Local session_tasks rows carry no human_request: it is read from the card's board; a running card is not an ask."""
    import sqlite3
    import types
    db = tmp_path / "kanban.db"
    c = sqlite3.connect(db)
    c.execute("create table tasks (id text, status text, block_kind text, human_request text)")
    c.execute("insert into tasks values ('t_f68', 'blocked', 'needs_input', ?)", (json.dumps(_ASK),))
    c.execute("insert into tasks values ('t_run', 'running', NULL, ?)", (json.dumps(_ASK),))
    c.commit()
    c.close()
    fake = types.SimpleNamespace(
        BLOCKED_STATUSES=("blocked", "triage"), _board_db_paths=lambda: [db],
        session_tasks=lambda ids, statuses: [{"id": "t_f68", "title": "askevepodcast GitHub", "status": "blocked",
                                              "board": "default"},
                                             {"id": "t_run", "title": "other", "status": "blocked", "board": "default"}])
    import sys
    pkg = sys.modules.get("hermes_cli") or types.ModuleType("hermes_cli")
    monkeypatch.setitem(sys.modules, "hermes_cli", pkg)
    monkeypatch.setattr(pkg, "kanban_session_activity", fake, raising=False)
    monkeypatch.setitem(sys.modules, "hermes_cli.kanban_session_activity", fake)
    monkeypatch.setattr(rc, "_lineage", lambda sid: [sid])
    got = rc.session_human_asks("chat")
    assert got == [{"id": "t_f68", "title": "askevepodcast GitHub", "action": _ASK["action"]}]


def test_denied_ask_skips_a_reply_that_already_tells_the_ask():
    """Live false positive: the reply presented the ask (card id and all), then said other jobs don't need the user."""
    card = {"id": "t_f68", "title": "askevepodcast GitHub", "status": "blocked", "block_kind": "needs_input",
            "human_request": json.dumps(_ASK)}
    asks = lambda: rc.linked_human_asks([card])
    for reply in ("One decision waits on you: unblock card t_f68 once you approve. The other two jobs don't need you.",
                  "You need to: " + _ASK["action"] + ". The rest of the work doesn't need you."):
        assert rc.check(reply, "status?", [], _zero_judge, human_asks=asks) is None, reply
    hit = rc.check("The PR merges on its own; nothing needs you.", "status?", [], _zero_judge, human_asks=asks)
    assert hit and hit["rule"] == "denied_ask"


# ---- agent-a806f203ab: a worker that closed its own card earlier in the turn, then named a done card as owner ----
CLOSED_REPLY = (  # shape of the 2026-10-30 revised reply (msg 444875), product details removed
    "I fixed both gaps by pushing commit 849d3ff2 to PR #248. Exact-head CI on 849d3ff2 hasn't finished. The earlier "
    "green run was on a715eac4, and I'm not watching the new run.\n\nI did not run the deletion check for the new "
    "assertion, so I haven't shown it fails when a label loses its timestamp.\n\nI left a comment on #248 with the "
    "commit and the CI state. My original card is already completed. The follow-up lives on PR #248, owned by card "
    "t_01a18de0c2168ff2.")
CLOSED_LOG = ['-> kanban_complete({"task_id": "t_01a18e0f85fa5317", "summary": "duplicate of #248"})',
              "-> terminal(git push origin HEAD:fix)", "-> terminal(gh pr comment 248 --body ...)"]


def test_card_closed_reads_a_successful_kanban_complete_only():
    assert rc.card_closed(CLOSED_LOG)
    assert rc.card_closed(['-> tool_call({"calls": [{"name": "kanban_complete", "arguments": {}}]})'])
    assert rc.card_closed(["-> terminal(hermes kanban complete t_1 --summary done)"])
    assert not rc.card_closed(['-> kanban_complete({"task_id": "t_1"})', '   <- ERROR {"error": "refused"}'])
    assert not rc.card_closed(["-> kanban_show(t_1)", "-> terminal(grep -n kanban_complete x.py)"])


def test_report_only_after_own_card_closed_gets_card_closed_nudge():
    llm, _ = _judge({"report_only": 0.95, "evidence": "not watching the new run; deletion check not run"})
    hit = rc.check(CLOSED_REPLY, "work kanban task", CLOSED_LOG, llm, threshold=0.7,
                   thresholds=rc.settings()["thresholds"], card_status=lambda cid: None)
    assert hit and hit["rule"] == "report_only"
    assert "Your card is closed" in hit["nudge"] and "nothing wakes you" in hit["nudge"]
    assert "wait_for_ci.py" in hit["nudge"] and "A done card owns nothing" in hit["nudge"]
    open_card = rc.check(CLOSED_REPLY, "work kanban task", CLOSED_LOG[1:], llm, threshold=0.7,
                         thresholds=rc.settings()["thresholds"], card_status=lambda cid: None)
    assert open_card and "Your card is closed" not in open_card["nudge"]


def test_owner_phrase_owned_by_card_is_a_handoff():
    assert rc.unverified_owner(CLOSED_REPLY, CLOSED_LOG) == "owned by card t_01a18de0c2168ff2"
    assert rc.unverified_owner("That follow-up belongs to task t_abc123 now.", []) is not None
    assert rc.unverified_owner("The fix lives with card t_abc123.", []) is not None
    assert rc.unverified_owner("The fix lives on main; card t_abc123 shipped it.", []) is None


def test_closed_owner_fires_on_a_done_owner_even_after_kanban_show():
    log = CLOSED_LOG + ["-> kanban_show(t_01a18de0c2168ff2)"]
    status = {"t_01a18de0c2168ff2": "done"}.get
    assert rc.closed_owner(CLOSED_REPLY, card_status=status) == ("t_01a18de0c2168ff2", "done")
    llm, calls = _judge({"report_only": 0.0, "evidence": ""})
    hit = rc.check(CLOSED_REPLY, "work kanban task", log, llm, threshold=0.7, card_status=status)
    assert hit and hit["rule"] == "report_only" and hit.get("reason") == "closed_owner"
    assert "t_01a18de0c2168ff2" in hit["nudge"] and "done" in hit["nudge"]
    assert "Your card is closed" in hit["nudge"] and not calls  # deterministic: no judge call


def test_closed_owner_quiet_for_a_running_owner_that_was_checked():
    log = CLOSED_LOG[1:] + ["-> kanban_show(t_01a18de0c2168ff2)"]
    status = {"t_01a18de0c2168ff2": "running"}.get
    assert rc.closed_owner(CLOSED_REPLY, card_status=status) is None
    llm, _ = _judge({"report_only": 0.0, "evidence": ""})
    assert rc.check(CLOSED_REPLY, "work kanban task", log, llm, threshold=0.7, card_status=status) is None
    assert rc.closed_owner(CLOSED_REPLY, card_status=lambda cid: None) is None  # unknown card: fail-open


def test_closed_owner_reads_the_board_read_only(tmp_path, monkeypatch):
    import sqlite3
    db = tmp_path / "kanban.db"
    c = sqlite3.connect(db)
    c.execute("create table tasks (id text primary key, status text)")
    c.executemany("insert into tasks values (?, ?)", [("t_aa11", "done"), ("t_bb22", "running")])
    c.commit()
    c.close()
    monkeypatch.setattr(rc, "_board_dbs", lambda: [db])
    assert rc.board_card_status("t_aa11") == "done"
    assert rc.board_card_status("t_bb22") == "running"
    assert rc.board_card_status("t_cc33") is None
    assert rc.closed_owner("The rest is owned by card t_aa11.") == ("t_aa11", "done")
    assert rc.closed_owner("CI is owned by card `t_aa11` / PR 172.") == ("t_aa11", "done")
    assert rc.closed_owner("Fix card `t_aa11` owns it.") == ("t_aa11", "done")
    assert rc.closed_owner("Fix card `t_bb22` owns it.") is None
    assert rc.closed_owner('He wrote "owned by card t_aa11" in the log.') is None  # quoted, not a claim


def test_judged_report_only_after_own_card_closed_uses_the_closed_card_template():
    reply = CLOSED_REPLY.split("\n\nI left")[0]  # no owner phrase: the judge path picks the nudge
    llm, _ = _judge({"report_only": 0.95, "evidence": "not watching the new run"})
    hit = rc.check(reply, "work kanban task", CLOSED_LOG, llm, threshold=0.7, card_status=lambda cid: None)
    assert hit["rule"] == "report_only" and hit.get("variant") == "card_closed"
    assert hit["nudge"] == rc.REPORT_ONLY_CARD_CLOSED.format(evidence="not watching the new run")
    plain = rc.check(reply, "work kanban task", CLOSED_LOG[1:], llm, threshold=0.7, card_status=lambda cid: None)
    assert "variant" not in plain and plain["nudge"] == rc.NUDGES["report_only"].format(evidence="not watching the new run")
    assert rc.nudge_for("report_only", "e", kanban_down=True, card_closed=True) == rc.REPORT_ONLY_KANBAN_DOWN.format(evidence="e")


# ── status_only on Hermes-written prompts (self-heal agent-21185525f4, agent-73e6b1a688, agent-f573a1f951) ──────
# The [Kanban close-out] and [chat-closeout] prompts are automation, not the user asking "why are we still waiting":
# status_only held correct close-out summaries in 4 hub chats and 2 MacBook chats (10-28..10-30).
import os  # noqa: E402

CORE = Path(os.environ.get("HERMES_AGENT_DIR") or Path.home() / ".hermes" / "hermes-agent")
CLOSEOUT_PROMPT = (  # state.db row 459940, @session:default/20261030_184904_51cda4 (hub)
    "[Kanban close-out] The Kanban notice above reached this chat and no turn followed it. Act on it now: check "
    "what the card delivered or why it stopped, fix or follow up on anything an agent can, then end with a short "
    "summary for the user, the decision you need from them, or what you are still waiting on.")
CLOSEOUT_REPLY = (
    "The \"Active workers\" panel is fixed on your MacBook and pinned so it won't revert.\n\n- **Pin:** the plugin is "
    "installed from your fork at commit `96b44c7`.\n- The pin card is scheduled: it switches back when upstream PR #3 "
    "releases.")
CLOSEOUT_LOG = ['-> cross_task({"action": "show", "task": "@task:default@macbook/t_01a18ecf672c9235"})',
                '-> cross_task({"action": "show", "task": "@task:default@macbook/t_01a18ec57362f026"})']


def _closeout_core():
    spec = importlib.util.spec_from_file_location("t_chat_closeout_core", HERE.parent / "chat-closeout" / "core.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _ask_relay_prompt():
    """The real producer's [chat-closeout] ask-relay prompt for t_82f0fe15 (@session:default/20261023_144828_86fa15),
    whose verbatim ask line carries "unblock = complete it"."""
    cc = _closeout_core()
    card = {"id": "t_82f0fe15", "status": "blocked", "block_kind": "needs_input",
            "title": "Model balancing 5: merge, deploy and prove routing on every gateway and Babybear",
            "human_request": json.dumps({
                "action": "Reply 'accept scoped' to close t_82f0fe15 as scoped-verified (unblock = complete it, "
                          "remaining gaps stay on their owner cards), or provide Gemini/Grok grants/funding to "
                          "continue. Archive = drop the card.",
                "why_human": "Four-provider completion needs Gemini/Grok grants and funding that only David can "
                             "supply, and the goal judge refuses scoped completion.",
                "interaction": "reply", "scope": "t_82f0fe15 model-balancing fleet rollout",
                "evidence": "PR #2139 landed as 4f2b1efd6; Gemini/Grok grants absent."})}
    return cc.ask_relay_prompt({"batch": ["t_82f0fe15"]}, {"t_82f0fe15": card}, ["t_82f0fe15"])


def test_status_only_skips_the_kanban_closeout_prompt():
    assert rc.PROGRESS_ASK_RE.search(CLOSEOUT_PROMPT)  # the template itself says "still waiting on"
    assert rc.status_only(CLOSEOUT_PROMPT, CLOSEOUT_REPLY, CLOSEOUT_LOG) is None
    llm = lambda s, p: json.dumps({k: 0 for k in rc.RULES})
    hit = rc.check(CLOSEOUT_REPLY, CLOSEOUT_PROMPT, CLOSEOUT_LOG, llm, only=["status_only"])
    assert not hit or hit["rule"] != "status_only"


def test_status_only_skips_the_chat_closeout_ask_relay_quoting_unblock_complete_it():
    prompt = _ask_relay_prompt()
    assert prompt.startswith("[chat-closeout]") and "unblock = complete it" in prompt
    reply = ("The specific wall is **permission to narrow the completion criteria**: close the rollout card with "
             "Gemini/Grok coverage and outstanding live checks explicitly incomplete, rather than satisfy the "
             "original full-fleet acceptance.\n\nI can’t treat silence as that permission or mark those checks "
             "passed.")
    assert rc.status_only(prompt, reply, ['-> cross_task({"action": "show", "task": "t_82f0fe15"})']) is None


def test_status_only_accepts_a_named_authorization_wall_after_a_human_ask():
    reply = ("The specific wall is **authorization to narrow the completion criteria**: the card requires "
             "four-provider verification, but only the scoped rollout is verified. I cannot waive that requirement "
             "or authorize additional provider spending myself.")
    assert rc.status_only("why are we still waiting on t_82f0fe15?", reply, ["-> kanban_show(t_82f0fe15)"]) is None
    assert rc.status_only("any update?", "I need your permission to drop the Grok route.",
                          ["-> kanban_show(t_x)"]) is None


def test_status_only_still_fires_on_the_human_ask_about_a_card():
    ask = "why are we still waiting on kanban card t_82f0fe15?"  # 10-28 ask, @session:default/20261028_080753_ba1741
    reply = "Card t_82f0fe15 is ready and its reviewer is alive; CI is still running."
    hit = rc.status_only(ask, reply, ["-> kanban_show(t_82f0fe15)"])
    assert hit and "kanban_show" in hit
    # a human typing the close-out tag mid-sentence is still a human ask (only anchored envelopes are notices)
    assert rc.status_only("so why is the [Kanban close-out] still waiting?", reply, ["-> kanban_show(t_x)"])


def test_notice_turn_recognizes_every_prompt_producer_text():
    """Drift guard: the producers' real text, not a copy. A new prompt envelope must be added to NOTICE_RE."""
    path = CORE / "tui_gateway" / "notice_guarantee.py"
    assert path.is_file(), f"core checkout missing: set HERMES_AGENT_DIR (looked at {path})"
    import ast
    tree = ast.parse(path.read_text())
    texts = [ast.literal_eval(n.value) for n in ast.walk(tree) if isinstance(n, ast.Assign)
             and any(getattr(t, "id", "") == "CLOSEOUT_TEXT" for t in n.targets)]
    assert texts, "notice_guarantee.CLOSEOUT_TEXT not found"
    cc = _closeout_core()
    for text in texts + [cc.TAG + " anything", _ask_relay_prompt(),
                         cc.closeout_prompt({"batch": ["t_x"]}, {"t_x": {"status": "done", "title": "x"}})]:
        assert rc.notice_turn(text), text[:60]
    sh = _mod("self_heal")  # the complaint lane must not read them as user complaints either
    for text in texts + [_ask_relay_prompt()]:
        assert sh.SYNTHETIC_RE.match(text), text[:60]


# --- agent-f668487724: past the confirm cap the screen alone (57% agreement with the second opinion) nudged a worker
# that had already routed the MacBook step to a capability owner. An unconfirmed report_only never nudges by default.
def test_capped_confirm_does_not_nudge_on_the_screen_alone(monkeypatch):
    monkeypatch.delenv("COACH_REPORT_ONLY_FALLBACK", raising=False)
    llm, _ = _judge(_ro_verdict(0.94))
    audit = []
    hit = rc.check(REPORT_REPLY, REPORT_ASK, [], llm, only=["report_only"], thresholds=rc.settings()["thresholds"],
                   confirm=lambda: None, confirm_floor=rc.CONFIRM_FLOOR, audit=audit.append)
    assert hit is None
    e = next(e for e in audit if e["event"] == "report_only_scored")
    assert e["source"] == "screen_fallback" and e["passing"] is False and e.get("suppressed") == "unconfirmed"


def test_report_only_fallback_nudge_restores_the_screen_fallback(monkeypatch):
    monkeypatch.setenv("COACH_REPORT_ONLY_FALLBACK", "nudge")
    assert rc.settings()["report_only_fallback"] == "nudge"
    llm, _ = _judge(_ro_verdict(0.94))
    audit = []
    hit = rc.check(REPORT_REPLY, REPORT_ASK, [], llm, only=["report_only"], thresholds=rc.settings()["thresholds"],
                   confirm=lambda: None, confirm_floor=rc.CONFIRM_FLOOR, audit=audit.append,
                   report_only_fallback=rc.settings()["report_only_fallback"])
    assert hit and hit["rule"] == "report_only"
    assert audit[0]["passing"] is True and "suppressed" not in audit[0]


def test_confirm_daily_cap_covers_the_busiest_day():
    assert rc.CONFIRM_DAILY_CAP >= 250  # 10-30 had 189 screen scores at or above the 0.75 confirm floor


def test_capability_block_with_needs_is_not_a_human_ask():
    # kanban_block(kind=capability, needs=[...]) with no human_request opens an agent owner card; it asks David nothing
    args = {"kind": "capability", "reason": "MacBook rollout needs the MacBook gateway", "needs": ["host:macbook"]}
    assert rc.human_block("kanban_block", args, "t_x") is None
    assert rc.human_block("tool_call", {"calls": [{"name": "kanban_block", "arguments": args}]}, "t_x") is None
    # a capability block with no needs, or one that also carries a human_request, still asks a human
    assert rc.human_block("kanban_block", {"kind": "capability", "reason": "needs the YubiKey"}, "t_x")
    hr = {"action": "Tap the key. Unblock = go; archive = drop.", "why_human": "physical", "interaction": "physical",
          "scope": "x", "evidence": "y"}
    assert rc.human_block("kanban_block", {**args, "human_request": hr}, "t_x")
