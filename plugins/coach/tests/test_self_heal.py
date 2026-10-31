"""self_heal: complaint (pre_llm_call) and self-admission (post_llm_call) detectors, fingerprint dedupe, modes."""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


sh = load("coach_self_heal_test", ROOT / "self_heal.py")
core = load("coach_core_test_sh", ROOT / "core.py")
plugin = load("coach_plugin_test_sh", ROOT / "__init__.py")


def judge(p=0.9, component="desktop", symptom="sidebar sessions reshuffle", kind="admission"):
    """Fake judge. kind 'admission' counts on both paths (the audit only files self-admissions)."""
    calls = []

    def llm(system, prompt):
        calls.append((system, prompt))
        return json.dumps({"p_defect": p, "kind": kind, "component": component, "symptom": symptom})
    return llm, calls


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setenv("COACH_SELFHEAL", "shadow")
    monkeypatch.delenv("HERMES_KANBAN_TASK", raising=False)


def events(tmp_path, name="selfheal_detect"):
    p = tmp_path / "coach" / "log.jsonl"
    if not p.exists():
        return []
    return [r for r in (json.loads(x) for x in p.read_text().splitlines()) if r.get("event") == name]


# ── prefilters ──
@pytest.mark.parametrize("text", [
    "i think the sort is broken",
    "why are you asking me to open a terminal?",
    "i fixed this once, but now it's broken again",
    "you said that, but i dont see any cards scheduled",
    "fix this [screenshot]",
    "[OUT-OF-BAND USER MESSAGE — x]\nYOU JUST SAID IT WAS CANCELED, WHY DO YOU KEEP ASKING ME\n[/OUT-OF-BAND USER MESSAGE]",
    "it's speaking spanish to me for some reason",
    "you buy the credits",          # a correction with no complaint words (missed by the v1 regex)
    "resume", "whats going on",     # nudges: the judge decides from the previous turn
])
def test_complaint_prefilter_hits(text):
    assert sh.prefilter(text)


@pytest.mark.parametrize("text", [
    "do it", "go", "yes, do it", "thanks!", "/goal ship it",
    "[IMPORTANT: Background process proc_1 exited (exit code 0).]",
    "✔ [default] Kanban t_1 done — shipped",
])
def test_complaint_prefilter_skips(text):
    assert not sh.prefilter(text)


@pytest.mark.parametrize("text", [
    '[Message from another Hermes session "Review" (@session:default/other)]\nThe card is blocked; fix it.',
    '⏸ [default] @default Kanban t_12345678 blocked: Approve the maintenance operation.',
    '⏸️ [default] @worker Kanban t_12345678 blocked: needs authorization',
    '[chat-closeout] Every kanban card this chat handed work to has settled.\nblocked: needs approval',
])
def test_synthetic_coordination_never_calls_complaint_judge(text):
    llm, calls = judge(1.0, kind="hermes_bug")
    assert not sh.prefilter(text)
    assert sh.detect_complaint("The operation needs approval.", text, llm) is None
    assert calls == []


@pytest.mark.parametrize("text", [
    'Why is this blocked? [chat-closeout] Every kanban card has settled.',
    'The message from another Hermes session is wrong; fix the routing.',
    '[OUT-OF-BAND USER MESSAGE — a direct message]\nWhy is this blocked?\n[/OUT-OF-BAND USER MESSAGE]',
])
def test_real_complaints_survive_coordination_filter(text):
    assert sh.prefilter(text)


def test_multimodal_message_and_trailers_are_cleaned():
    msg = [{"type": "text", "text": "fix this"}, {"type": "image_url", "image_url": {"url": "data:..."}}]
    assert sh.clean_user(msg) == "fix this [screenshot]" and sh.prefilter(msg)
    assert sh.clean_user("didnt work [screenshot] [user-steps] Steps still waiting on the user") == "didnt work [screenshot]"


def test_admission_prefilter():
    assert sh.admission_prefilter("You're right, and that was my mistake. I skipped the check.")
    assert sh.admission_prefilter("The root cause is a bug in the fallback plugin's import lock.")
    assert sh.admission_prefilter("Run this in your terminal: sudo foo")  # handoff
    assert sh.admission_prefilter("Shipped.", ["-> terminal(make)", "   <- ERROR exit 2"])  # errors in the log
    assert not sh.admission_prefilter("Shipped. Tests pass and the page is live.", ["-> terminal(make)"])


def test_machine_signals_need_no_judge():
    api = sh.machine_signal("Sorry — this message was not processed (API call failed).")
    assert api["source"] == "signal" and api["kind"] == "api_error" and api["component"] == "provider"
    # Real logger shape (agent_rules.observe): "-> tool(args)" lines and "   <- ERROR <result>" lines. A turn that was
    # cut short with no reply ends on the cancelled tool's error line.
    cut = ["-> terminal(sleep 900)", "   <- ERROR Tool execution cancelled. The user interrupted the turn."]
    assert sh.machine_signal("", cut)["kind"] == "interrupted"
    rep = ["-> terminal(npm test)", "   <- ERROR exit 1: ENOENT"] * 3
    assert sh.machine_signal("done", rep)["kind"] == "repeat_failure"
    assert sh.machine_signal("done", rep[:4]) is None  # two identical errors are not a loop
    llm, calls = judge(0.0)
    assert sh.detect_admission("Sorry, not processed.", llm)["source"] == "signal" and not calls


# gateway-a8b8d01aa4: finished turns were filed as "turn interrupted" because the tool log merely mentioned it.
FINISHED = "Done. The hub's own background retry switches on once the hub Desktop backend restarts."


def test_finished_reply_whose_command_args_mention_interrupt_is_not_interrupted():
    log = ["-> terminal(grep -rn \"Turn stopped\" ~/.hermes/hermes-agent/tui_gateway)",
           "-> search_files(Tool execution cancelled|Operation interrupted)",
           "-> terminal(git commit -m 'detect turn lease lost')"]
    assert sh.machine_signal(FINISHED, log) is None
    assert sh.detect_admission(FINISHED, judge(0.0)[0], tool_log=log) is None


def test_cancelled_tool_then_recovery_and_reply_is_not_interrupted():
    log = ["-> terminal(sleep 900)", "   <- ERROR Tool execution cancelled. The user interrupted the turn.",
           "-> terminal(ls)", "-> read_file(/tmp/x)"]
    assert sh.machine_signal(FINISHED, log) is None
    # even as the very last line: the agent replied afterwards, so the turn finished
    assert sh.machine_signal(FINISHED, log[:2]) is None


def test_core_interrupt_placeholder_reply_is_still_interrupted():
    for reply in ("Operation interrupted: waiting for model response (42s).",
                  "[Turn stopped by the user before it finished.]",
                  "Session turn lease lost; stopping to protect the transcript.",
                  "⚠️ No reply: the turn stopped while a tool result was still pending and the model produced no "
                  "follow-up text. Send `continue` to let it summarize."):
        hit = sh.machine_signal(reply, ["-> terminal(make)"])
        assert hit and hit["kind"] == "interrupted" and hit["fingerprint"] == "gateway-a8b8d01aa4", reply


def test_finished_reply_quoting_a_placeholder_is_not_interrupted():
    # live check 20261028_082017_634f4b: the agent grepped core and quoted the match in its finished reply
    for reply in ('It found 1 match, on line 64: `content = f"[Turn stopped by {label} before it finished.]"`.',
                  "Not a real incident: the detector misread 'Operation interrupted: waiting for model response'.",
                  "**Self-heal flags finished turns** when a log mentions turn lease lost."):
        assert sh.machine_signal(reply, ["-> terminal(grep -rn x)"]) is None, reply


# agent-6eb3a51e94: every repeated tool error got the one fixed symptom "same tool error repeated" (one fingerprint
# for unrelated failures) and the error text was dropped, so the investigation card saw only the reply tail.
DISK_IO = ["-> terminal(sqlite3 /home/u/.hermes/kanban.db 'select 1')",
           "   <- ERROR exit 1: Error: disk I/O error (10)"] * 3
OVERLAP = ["-> kanban_create(title='fix t_01a18e0f85fa5317', assignee=default)",
           "   <- ERROR create_overlap: refused, open card t_01a18f450bc2683c covers it"] * 3


def test_repeat_failure_fingerprint_is_per_tool_and_error():
    a, b = sh.machine_signal("done", DISK_IO), sh.machine_signal("done", OVERLAP)
    assert a["kind"] == b["kind"] == "repeat_failure"
    assert a["fingerprint"] != b["fingerprint"]
    assert "terminal" in a["symptom"] and "disk" in a["symptom"]
    assert "kanban_create" in b["symptom"] and "create_overlap" in b["symptom"]
    # the same error with different card ids, paths and numbers is one defect
    other = ["-> kanban_create(title='other', assignee=default)",
             "   <- ERROR create_overlap: refused, open card t_01a1999999999999 covers it"] * 3
    assert sh.machine_signal("done", other)["fingerprint"] == b["fingerprint"]
    io2 = ["-> terminal(sqlite3 /tmp/x/kanban.db 'select 2')", "   <- ERROR exit 1: Error: disk I/O error (522)"] * 3
    assert sh.machine_signal("done", io2)["fingerprint"] == a["fingerprint"]
    # the same error text from two different tools is two defects
    io3 = [x.replace("-> terminal(", "-> execute_code(") for x in DISK_IO]
    assert sh.machine_signal("done", io3)["fingerprint"] != a["fingerprint"]


def test_repeat_failure_hit_carries_the_error_line_and_command():
    hit = sh.machine_signal("done", OVERLAP)
    ev = hit["evidence"]
    assert "create_overlap: refused" in ev and "kanban_create(" in ev and "3x" in ev
    assert "create_overlap: refused" in hit["details"]


def test_resume_note_user_turn_files_backend_stopped_signal():
    note = ("[System note: Your previous turn was interrupted mid-run — the app or its backend process stopped "
            "before the turn could finish. Some of the work may already be complete; check the current state "
            "before redoing anything, then finish the task. The interrupted request was:]\n\nfix the panel")
    llm, calls = judge(0.0)
    hit = sh.detect_complaint("working on it", note, llm, gap_s=1200)
    assert hit and hit["source"] == "signal" and hit["kind"] == "interrupted" and not calls
    assert hit["symptom"] == "turn interrupted (backend stopped)" and hit["gap_s"] == 1200
    assert hit["fingerprint"] != "gateway-a8b8d01aa4"
    assert sh.resume_signal("please fix the panel") is None


# ── detect_complaint / detect_admission ──
def test_detect_complaint_returns_schema_and_passes_prev_reply_and_tool_log():
    llm, calls = judge(0.92)
    hit = sh.detect_complaint("Done: sessions are sorted by creation time.", "i think the sort is broken", llm,
                              tool_log=["-> terminal(npm run build)", "   <- ERROR tsc failed"])
    assert set(hit) >= {"p_defect", "component", "symptom", "fingerprint", "source", "kind"}
    assert hit["source"] == "complaint" and hit["component"] == "desktop" and hit["p_defect"] == 0.92
    assert "sorted by creation time" in calls[0][1] and "sort is broken" in calls[0][1]
    assert "ERROR tsc failed" in calls[0][1]
    assert calls[0][0].startswith(sh.COMPLAINT_SYS)


def test_product_bug_kind_is_not_a_self_heal_defect():
    def llm(system, prompt):
        return json.dumps({"p_defect": 0.9, "kind": "external", "component": "other", "symptom": "vendor site down"})
    assert sh.detect_complaint("", "the vendor login page is down", llm) is None


def test_complaint_stage_without_an_answer_is_retried_then_left_out_not_a_zero_vote():
    """Week of 2026-10-12 replay: 'why did you think you were done? create a task to investigate and fix' scored
    [1.0, 0.0] because the strict stage's answer did not parse; counted as 0 it halved a sure catch to 0.5."""
    answers = iter([json.dumps({"p_defect": 1.0, "kind": "agent_fault", "component": "agent",
                                "symptom": "claimed done prematurely"}), "", ""])
    calls = []

    def llm(system, prompt):
        calls.append(system)
        return next(answers)
    hit = sh.detect_complaint("", "why did you think you were done? create a task to investigate and fix", llm)
    assert hit and hit["stages"] == [1.0] and hit["p_defect"] == 1.0
    assert len(calls) == 3 and calls[1].startswith(sh.COMPLAINT_STRICT_SYS)  # strict stage asked twice
    # a strict stage that ANSWERS 0 still votes
    answers2 = iter([json.dumps({"p_defect": 0.9, "kind": "hermes_bug", "component": "bb", "symptom": "x"}),
                     json.dumps({"p_defect": 0.05, "kind": "product_bug", "component": "bb", "symptom": "x"})])
    assert sh.detect_complaint("", "the camera stream is broken", lambda s, p: next(answers2)) is None


def test_complaint_prompt_carries_backstop_context():
    p = sh.complaint_prompt("", "why didnt this work [screenshot]", context="Session title: Fix sidebar drag")
    assert p.startswith("SESSION CONTEXT") and "Fix sidebar drag" in p and "(not available)" in p
    assert "SESSION CONTEXT" not in sh.complaint_prompt("", "x") and "(none: first turn)" in sh.complaint_prompt("", "x")


def test_audit_files_only_self_admissions():
    llm, _ = judge(0.95, kind="needless_handoff")
    assert sh.detect_admission("Please run this in your terminal: make", llm) is None
    llm2, _ = judge(0.95, kind="admission")
    assert sh.detect_admission("That was my mistake, I skipped the test.", llm2)["kind"] == "admission"


def test_complaint_cascade_gate_second_opinion_and_confirm():
    first, c1 = judge(0.8, kind="agent_fault")
    no, c2 = judge(0.2, kind="none")
    # >= GATE: a second flash opinion (strict prompt) and the confirm model; p = mean(0.8, 0.8, 0) < 0.6
    assert sh.detect_complaint("Done.", "that's not what i asked", first, confirm=no) is None
    assert len(c1) == 2 and c1[1][0].startswith(sh.COMPLAINT_STRICT_SYS) and len(c2) == 1
    assert c2[0] == c1[0]  # the confirm model sees the identical prompt
    yes, c3 = judge(0.85, kind="agent_fault", symptom="ignored instruction")
    hit = sh.detect_complaint("Done.", "that's not what i asked", first, confirm=yes)
    assert hit["p_defect"] == round((0.8 + 0.8 + 0.85) / 3, 3) and hit["symptom"] == "ignored instruction"
    assert hit["stages"] == [0.8, 0.8, 0.85]
    low, c4 = judge(0.1, kind="none")
    assert sh.detect_complaint("Done.", "add a toggle", low, confirm=yes) is None
    assert len(c4) == 1 and len(c3) == 1  # below GATE: one cheap call, no escalation
    adm, _ = judge(0.95, kind="admission")
    assert sh.detect_admission("my mistake, I skipped it", adm, confirm=no) is None  # confirm overrules the audit
    assert sh.detect_admission("my mistake, I skipped it", adm, confirm=yes) is None  # confirm kind not admission


def test_confirm_judge_from_env(monkeypatch):
    monkeypatch.delenv("COACH_DEFECT_CONFIRM_MODEL", raising=False)
    monkeypatch.setitem(sys.modules, "hermes_cli.config", None)  # no Hermes config importable -> off
    assert sh.confirm_judge() is None
    monkeypatch.setenv("COACH_DEFECT_CONFIRM_MODEL", "m")
    assert callable(sh.confirm_judge())


def test_profile_overlay_is_appended(tmp_path, monkeypatch):
    prof = tmp_path / "reply_profile.md"
    prof.write_text("- Never ask me to run a shell command myself.")
    monkeypatch.setenv("COACH_REPLY_PROFILE", str(prof))
    llm, calls = judge(0.9)
    sh.detect_complaint("Run: sudo x", "you have ssh, do it", llm)
    assert "Never ask me to run a shell command" in calls[0][0]
    monkeypatch.setenv("COACH_REPLY_PROFILE", str(tmp_path / "missing.md"))
    assert sh._with_profile("SYS") == "SYS"


def test_detect_complaint_below_threshold_prefilter_miss_and_judge_error():
    llm, calls = judge(0.4)
    assert sh.detect_complaint("prev", "why did you stop?", llm) is None
    llm2, calls2 = judge(0.99)
    assert sh.detect_complaint("prev", "do it", llm2) is None and not calls2

    def boom(system, prompt):
        raise RuntimeError("provider down")
    assert sh.detect_complaint("prev", "still broken", boom) is None  # fail-open
    assert sh.detect_complaint("prev", "still broken", lambda s, p: "not json") is None


def test_detect_admission():
    llm, calls = judge(0.95, "agent", "asked user for a finished step")
    hit = sh.detect_admission("That was my mistake: I asked you to do something already done.", llm,
                              tool_log=["-> terminal(ls)"], ask="clean up the cards")
    assert hit["source"] == "admission" and hit["component"] == "agent"
    assert calls[0][0].startswith(sh.AUDIT_SYS) and "clean up the cards" in calls[0][1]
    llm2, calls2 = judge(0.99)
    assert sh.detect_admission("All green, deployed.", llm2) is None and not calls2


def test_unknown_component_maps_to_other():
    llm, _ = judge(0.9, component="spaceship")
    assert sh.detect_complaint("", "it crashed again", llm)["component"] == "other"


# ── fingerprint + dedupe ──
def test_fingerprint_stable_across_rewording_and_distinct_otherwise():
    a = sh.fingerprint("desktop", "Sidebar sessions reshuffle")
    b = sh.fingerprint("desktop", "session sidebar reshuffles")
    c = sh.fingerprint("desktop", "draft text lost on new session")
    d = sh.fingerprint("plugin", "sidebar sessions reshuffle")
    assert a == b and a != c and a != d and a.startswith("desktop-")


def test_fingerprints_dedupe_per_utc_day(tmp_path):
    t = [86400 * 100 + 10.0]
    fps = sh.Fingerprints(tmp_path, clock=lambda: t[0])
    hit = {"fingerprint": "desktop-abc", "component": "desktop", "symptom": "x"}
    assert fps.record(hit, "s1") is True
    assert fps.record(hit, "s2") is False  # same day, another session
    t[0] += 86400
    assert fps.record(hit, "s3") is True  # next UTC day files again
    d = json.loads((tmp_path / "selfheal.json").read_text())["desktop-abc"]
    assert d["count"] == 3 and d["sessions"] == ["s1", "s2", "s3"]


# ── through the real hooks (both entry paths) ──
def test_pre_llm_call_complaint_path_logs_shadow_and_dedupes(tmp_path, on):
    llm, calls = judge(0.9)
    store = core.Store(tmp_path / "coach")
    _, hooks = plugin.build(store=store, checkout=None, selfheal_sync=True, selfheal_llm=llm)
    hist = [{"role": "user", "content": "sort by creation"}, {"role": "assistant", "content": "Sorted by creation time."}]
    hooks["pre_llm_call"](session_id="s1", user_message="i think the sort is broken", conversation_history=hist)
    hooks["pre_llm_call"](session_id="s2", user_message="the sidebar keeps reshuffling, still broken")
    ev = events(tmp_path)
    assert [e["source"] for e in ev] == ["complaint", "complaint"]
    assert ev[0]["mode"] == "shadow" and ev[0]["new"] is True and ev[1]["new"] is False
    assert ev[0]["fingerprint"] == ev[1]["fingerprint"]
    assert "Sorted by creation time." in calls[0][1]  # prev reply from conversation_history
    assert not (tmp_path / "coach" / "selfheal_queue.jsonl").exists()  # shadow never enqueues


def test_pre_llm_call_resume_note_files_backend_stop_with_gap(tmp_path, on):
    llm, calls = judge(0.0)
    now = [1000.0]
    store = core.Store(tmp_path / "coach", clock=lambda: now[0])
    _, hooks = plugin.build(store=store, checkout=None, selfheal_sync=True, selfheal_llm=llm)
    hooks["post_llm_call"](session_id="s1", assistant_response="Working on the panel fix now.")
    now[0] += 1200  # backend down 20 min
    note = ("[System note: Your previous turn was interrupted mid-run — the app or its backend process stopped "
            "before the turn could finish. The interrupted request was:]\n\nfix the panel")
    hooks["pre_llm_call"](session_id="s1", user_message=note)
    ev = events(tmp_path)
    assert len(ev) == 1 and ev[0]["source"] == "signal" and ev[0]["symptom"] == "turn interrupted (backend stopped)"
    assert ev[0]["evidence"].startswith("backend stopped mid-turn; chat resumed 20 min later.")
    assert not calls


def test_post_llm_call_admission_path(tmp_path, on):
    llm, calls = judge(0.95, "agent", "claimed cards scheduled when none were")
    store = core.Store(tmp_path / "coach")
    _, hooks = plugin.build(store=store, checkout=None, selfheal_sync=True, selfheal_llm=llm)
    hooks["post_llm_call"](session_id="s1", assistant_response="You're right, that was my mistake: no cards were scheduled.")
    hooks["post_llm_call"](session_id="s1", assistant_response="Done, deployed and verified.")
    ev = events(tmp_path)
    assert len(ev) == 1 and ev[0]["source"] == "admission" and ev[0]["component"] == "agent"


def test_live_mode_enqueues_once_per_fingerprint(tmp_path, on, monkeypatch):
    monkeypatch.setenv("COACH_SELFHEAL", "live")
    llm, _ = judge(0.9)
    _, hooks = plugin.build(store=core.Store(tmp_path / "coach"), checkout=None, selfheal_sync=True, selfheal_llm=llm)
    hooks["pre_llm_call"](session_id="s1", user_message="still broken")
    hooks["pre_llm_call"](session_id="s1", user_message="still broken!!")
    q = (tmp_path / "coach" / "selfheal_queue.jsonl").read_text().splitlines()
    assert len(q) == 1 and json.loads(q[0])["mode"] == "live"


def test_off_mode_subagents_cron_and_kanban_workers_are_skipped(tmp_path, on, monkeypatch):
    llm, calls = judge(0.9)
    _, hooks = plugin.build(store=core.Store(tmp_path / "coach"), checkout=None, selfheal_sync=True, selfheal_llm=llm)
    hooks["pre_llm_call"](session_id="s1", user_message="still broken", platform="cron")
    hooks["pre_llm_call"](session_id="s1", user_message="still broken", parent_session_id="p1")
    monkeypatch.setenv("HERMES_KANBAN_TASK", "t_1")
    hooks["pre_llm_call"](session_id="s1", user_message="Fix the dispatcher crash")
    monkeypatch.delenv("HERMES_KANBAN_TASK")
    monkeypatch.setenv("COACH_SELFHEAL", "off")
    hooks["pre_llm_call"](session_id="s1", user_message="still broken")
    hooks["post_llm_call"](session_id="s1", assistant_response="that was my mistake")
    assert not calls and events(tmp_path) == []


def test_tool_log_flows_through_hooks_to_both_paths(tmp_path, on):
    llm, calls = judge(0.9)
    store = core.Store(tmp_path / "coach")
    _, hooks = plugin.build(store=store, checkout=None, selfheal_sync=True, selfheal_llm=llm)
    hooks["pre_llm_call"](session_id="s1", user_message="deploy the site")
    hooks["post_tool_call"](tool_name="terminal", args={"command": "wrangler deploy"}, result='{"exit_code": 1}',
                            session_id="s1", status="error", error_message="auth failed")
    n = len(calls)
    hooks["post_llm_call"](session_id="s1", assistant_response="Deployed, the site is live.")
    assert len(calls) == n + 1 and "wrangler deploy" in calls[-1][1] and "deploy the site" in calls[-1][1]
    hooks["pre_llm_call"](session_id="s1", user_message="its not live, you said it was")
    assert "wrangler deploy" in calls[-1][1] and "Deployed, the site is live." in calls[-1][1]
    assert {e["source"] for e in events(tmp_path)} == {"admission", "complaint"}


def test_hook_judge_crash_is_fail_open(tmp_path, on):
    def boom(system, prompt):
        raise RuntimeError("x")
    _, hooks = plugin.build(store=core.Store(tmp_path / "coach"), checkout=None, selfheal_sync=True, selfheal_llm=boom)
    assert hooks["pre_llm_call"](session_id="s1", user_message="still broken") is None
    hooks["post_llm_call"](session_id="s1", assistant_response="my mistake")
    assert events(tmp_path) == []


# ── replay eval (synthetic fixture; the real labeled set is private) ──
def test_incident_replay_scores_like_score3(tmp_path):
    """Episode E1: flagged turns 1-2, explicit fix ask at turn 2 -> only a complaint at turn 1 or an audit before it
    counts unasked. E2: no explicit ask. E3: not a real incident (its turns are negatives). Turn x/0 is a normal turn."""
    ir = load("selfheal_incident_replay_test", ROOT / "eval" / "selfheal_incident_replay.py")
    d = tmp_path / "data"
    d.mkdir()
    turns = [("a", 0, "build it", ""), ("a", 1, "that's not what i asked", "Built X."), ("a", 2, "fix your process", "ok"),
             ("b", 0, "resume", "(error)"), ("c", 0, "why did you do that", "Did Y."), ("x", 0, "add a toggle", "Hi")]
    (d / "turns.jsonl").write_text("".join(json.dumps({"sid": s, "idx": i, "text": t, "prev_reply": p}) + "\n"
                                           for s, i, t, p in turns))
    (d / "turns2.jsonl").write_text("".join(json.dumps({"sid": s, "idx": i, "prev_log": []}) + "\n"
                                            for s, i, _, _ in turns))
    eps = [{"eid": "E1", "sid": "a", "first": 1, "last": 2, "turns": [{"sid": "a", "idx": 1}, {"sid": "a", "idx": 2}]},
           {"eid": "E2", "sid": "b", "first": 0, "last": 0, "turns": [{"sid": "b", "idx": 0}]},
           {"eid": "E3", "sid": "c", "first": 0, "last": 0, "turns": [{"sid": "c", "idx": 0}]}]
    (d / "episodes.json").write_text(json.dumps(eps))
    (d / "fivewhys.jsonl").write_text(json.dumps({"eid": "E1", "real_incident": True, "explicit_fix_ask_turn": 2}) + "\n"
                                      + json.dumps({"eid": "E2", "real_incident": True, "explicit_fix_ask_turn": None})
                                      + "\n" + json.dumps({"eid": "E3", "real_incident": False}) + "\n")
    (d / "fpcheck.jsonl").write_text(json.dumps({"sid": "c", "idx": 0, "real_incident": True}) + "\n")
    hot = {"that's not what i asked": 0.9, "fix your process": 0.95, "why did you do that": 0.8, "add a toggle": 0.7}

    def llm(system, prompt):
        msg = prompt.split("USER'S NEW MESSAGE:\n", 1)[-1]
        p = next((v for k, v in hot.items() if k in msg), 0.1)
        return json.dumps({"p_defect": p, "kind": "agent_fault", "component": "agent", "symptom": msg[:20]})
    out = tmp_path / "out"
    ir.run(type("A", (), {"hermes_root": "", "data": str(d), "out": str(out), "limit": 0, "workers": 2,
                          "tag": "t"})(), llm=llm)
    v = ir.jl(out / "verdicts.jsonl")
    res = ir.score(d, v, 0.65, 0.65, False, relabel=True)
    by = {r["eid"]: r for r in res["episodes"]}
    assert by["E1"]["mode"] == "reactive" and by["E1"]["cut"] == 1  # caught before the fix ask
    assert by["E2"]["mode"] is None and by["E2"]["mode_with_ask"] is None  # "resume" judged 0.1
    assert res["n"] == 2 and res["caught"] == 1 and res["neg_turns"] == 3
    fps = {(f["sid"], f["idx"]): f for f in res["fps"]}
    assert set(fps) == {("c", 0), ("x", 0)} and res["fp_relabeled_real"] == 1 and res["fp_true"] == 1
    # with the fix ask counted, E1 still counts once; an ask-only episode counts only in with_ask
    hot.pop("that's not what i asked")
    ir.run(type("A", (), {"hermes_root": "", "data": str(d), "out": str(tmp_path / "o2"), "limit": 0,
                          "workers": 2, "tag": "t2"})(), llm=llm)
    r2 = ir.score(d, ir.jl(tmp_path / "o2" / "verdicts.jsonl"), 0.65, 0.65, False, relabel=True)
    e1 = next(r for r in r2["episodes"] if r["eid"] == "E1")
    assert e1["mode"] is None and e1["mode_with_ask"] == "fix_ask" and r2["caught_with_ask"] == 1
    ir.write_report(res, d, tmp_path / "r.md", "| baseline | x | y | z |")
    md = (tmp_path / "r.md").read_text()
    assert "Misses (1)" in md and '"resume"' in md and "fpcheck=real" in md and "| baseline |" in md


def test_replay_reports_catch_rate_precision_misses_and_fps(tmp_path):
    replay = load("selfheal_replay_test", ROOT / "eval" / "selfheal_replay.py")
    defects = {"i think the sort is broken": 0.9, "why are you asking me to open a terminal": 0.95,
               "the menu should be shorter": 0.7, "my alarm is not set": 0.2}

    def llm(system, prompt):
        msg = prompt.split("USER'S NEW MESSAGE:\n", 1)[-1]
        p = next((v for k, v in defects.items() if k in msg), 0.1)
        return json.dumps({"p_defect": p, "component": "desktop", "symptom": "x"})
    out = tmp_path / "out"
    replay.main(["--data", str(ROOT / "tests" / "fixtures" / "selfheal_sample.jsonl"), "--out", str(out),
                 "--workers", "2"], llm=llm)
    rep = json.loads((out / "report.json").read_text())
    a = rep["all"]
    assert (a["pos"], a["tp"], a["fn"], a["fp"]) == (4, 2, 2, 1)
    assert a["catch_rate"] == 0.5 and a["precision"] == round(2 / 3, 3)
    assert {m["id"] for m in rep["misses"]} == {"c", "d"} and [f["id"] for f in rep["false_positives"]] == ["g"]
    assert rep["prefilter_only"]["fn"] == 0  # every defect reaches the judge; "do it" never does
    md = (out / "report.md").read_text()
    assert "my alarm is not set" in md and "False positives (1)" in md
    # cached verdicts: a rerun makes no judge calls
    calls = []
    replay.main(["--data", str(ROOT / "tests" / "fixtures" / "selfheal_sample.jsonl"), "--out", str(out)],
                llm=lambda s, p: calls.append(1) or "{}")
    assert not calls


# ---- week-of-10-12 replay: overlay schema leak (stray reply-check output line in reply_profile.md) ----------------
def test_parse_skips_another_judges_schema_and_takes_the_p_defect_object():
    two = ('{"p_defect": 1.0, "kind": "agent_fault", "component": "agent", "symptom": "stopped mid-work"} '
           '{"unverified_done":0.0,"needless_handoff":0.0,"evidence":"x"}')
    assert sh.parse(two)["p_defect"] == 1.0
    other = '{"unverified_done":0.0,"needless_handoff":1.0,"rule_violation":1.0,"evidence":"Told user to run it"}'
    assert sh.parse(other) is None          # no opinion, not a 0 vote
    fenced = '```json\n{"p_defect": 0.7, "kind": "hermes_bug", "component": "desktop", "symptom": "s"}\n```'
    assert sh.parse(fenced)["p_defect"] == 0.7


def test_other_schema_stage_is_left_out_of_the_cascade_mean():
    answers = iter(['{"p_defect": 0.9, "kind": "agent_fault", "component": "agent", "symptom": "s"}',
                    '{"unverified_done":0.0,"needless_handoff":1.0,"evidence":"e"}',
                    '{"unverified_done":0.0,"needless_handoff":1.0,"evidence":"e"}'])
    v = sh.complaint_verdict("prev", "why did you stop", lambda s, p: next(answers))
    assert v["stages"] == [0.9] and v["p_defect"] == 0.9


def test_overlay_output_spec_line_is_stripped(tmp_path, monkeypatch):
    prof = tmp_path / "reply_profile.md"
    prof.write_text("- Rule: self-serve.\n- Reply in plain English to the user.\n\n"
                    'Return ONLY JSON {"unverified_done":p,"needless_handoff":p,"evidence":"<=25 words"}\n')
    monkeypatch.setenv("COACH_REPLY_PROFILE", str(prof))
    t = sh.profile_text()
    assert "self-serve" in t and "plain English" in t and "unverified_done" not in t
    assert "unverified_done" not in sh._with_profile(sh.COMPLAINT_SYS)


def test_backstop_context_carries_the_agents_reply_to_the_turn():
    turns = [{"t": 1, "text": "why are all the dots red? [screenshot]", "prev": ""},
             {"t": 2, "text": "root cause it", "prev": "The panel conflates quota exhausted with cooling down."}]
    dg = {"title": "Explain why all dots are red", "last_reply": "Found the cooldown bug."}
    c0 = sh.backstop_context(dg, turns, turns[0])
    assert "reply to this message" in c0 and "conflates" in c0       # next turn's prev
    assert "Found the cooldown bug" in sh.backstop_context(dg, turns, turns[1])   # newest turn: last_reply
    mid = {"title": "t", "last_reply": turns[1]["prev"]}                  # digest written mid-turn
    assert "reply to this message" not in sh.backstop_context(mid, turns, turns[1])
    assert "Found the cooldown bug" in sh.complaint_prompt("", turns[1]["text"], context=sh.backstop_context(dg, turns, turns[1]))


# ---- t_a871080a: flash variance + strict-stage misses (ask-gate week of 10-12 follow-up) ---------------------------
def seq(*answers):
    """Fake judge answering in order; records (system, prompt)."""
    it, calls = iter(answers), []

    def llm(system, prompt):
        calls.append((system, prompt))
        p, kind = next(it)
        return json.dumps({"p_defect": p, "kind": kind, "component": "plugin", "symptom": "s"})
    return llm, calls


def test_borderline_stage1_is_resampled_and_the_max_counts(monkeypatch):
    """'why are all the dots red? [screenshot]' scored 0.7, 0.6 and 0.05 on three identical temp-0 flash calls."""
    monkeypatch.setattr(sh, "RESAMPLE_LO", 0.3)
    llm, calls = seq((0.45, "hermes_bug"), (0.8, "hermes_bug"), (0.8, "hermes_bug"))
    hit = sh.detect_complaint("", "the sidebar looks odd", llm)
    assert hit and hit["stages"] == [0.8, 0.8] and len(calls) == 3
    assert calls[0] == calls[1]                                # the resample is the identical stage-1 call
    llm, calls = seq((0.45, "hermes_bug"), (0.2, "none"))      # a lower resample never lowers the score
    v = sh.complaint_verdict("", "the sidebar looks odd", llm)
    assert v["stages"] == [0.45] and len(calls) == 2
    llm, calls = seq((0.1, "none"))                            # clear no, no fix word: one cheap call
    assert sh.detect_complaint("", "add a dark mode toggle", llm) is None and len(calls) == 1


def test_fix_request_below_gate_gets_one_more_stage1_sample(monkeypatch):
    monkeypatch.setattr(sh, "RESAMPLE_LO", None)
    monkeypatch.setattr(sh, "FIX_RESAMPLE", True)
    for text in ("root cause it, and fix the pluggin", "why are all the dots red? [screenshot]",
                 "figure ut why the plugin fails", "seems broken, still in meeting"):
        assert sh.resample_stage1(0.0, text), text
    assert not sh.resample_stage1(0.0, "make a card to do the nest investigation please")
    assert not sh.resample_stage1(0.7, "fix it")               # at/above GATE: no resample
    llm, calls = seq((0.05, "none"), (0.7, "hermes_bug"), (0.9, "hermes_bug"))
    hit = sh.detect_complaint("The account_usage plugin conflates quota with cooldown.", "root cause it, and fix the pluggin", llm)
    assert hit and hit["stages"] == [0.7, 0.9] and len(calls) == 3
    monkeypatch.setattr(sh, "FIX_RESAMPLE", False)
    llm, calls = seq((0.05, "none"))
    assert sh.detect_complaint("", "root cause it, and fix the pluggin", llm) is None and len(calls) == 1


def test_strict_prompt_counts_fix_requests_for_described_defects_and_never_calls_hermes_a_product():
    s = sh.COMPLAINT_STRICT_SYS
    assert "{strict" not in s and s.rstrip().endswith('"symptom": "..."}')
    assert "previous message (or its reply to this\n  message) describes" in s       # candidate 2
    assert "agent's own earlier work in this session caused" in s                    # candidate 2
    assert "is never a product_bug" in s                                             # candidate A
    bare = sh.strict_sys(False, False)
    assert "describes" not in bare and "never a product_bug" not in bare and "{strict" not in bare
    llm, calls = judge(0.9, kind="hermes_bug")
    sh.detect_complaint("Done.", "still broken", llm)
    assert calls[1][0].startswith(sh.COMPLAINT_STRICT_SYS)


def _digest(tmp_path, now):
    p = tmp_path / "data" / "hub" / "default" / "s1.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"id": "s1", "host": "hub", "profile": "default", "source": "desktop", "title": "x",
                             "user_turns": [{"t": now - 60, "text": "that's not what i asked", "prev": "Done."}]}))
    return tmp_path / "data"


def test_backstop_passes_the_confirm_model(tmp_path, monkeypatch):
    """The backstop called detect_complaint(..., confirm=None): the confirm model never ran on digests."""
    import time as _t
    now = _t.time()
    data = _digest(tmp_path, now)
    store = core.Store(tmp_path / "coach")
    first, _ = judge(0.8, kind="agent_fault")
    no, cno = judge(0.0, kind="none")
    monkeypatch.setattr(sh, "confirm_judge", lambda: no)
    filed = []
    sh.backstop(data, store=store, judge=first, now=now, m="shadow",
                filer=lambda d, store=None, m=None: filed.append(d) or d)
    assert len(cno) == 1 and not filed                         # mean(0.8, 0.8, 0) < 0.6: confirm vetoed it
    yes, cyes = judge(0.9, kind="agent_fault")
    (tmp_path / "coach" / "selfheal_backstop.json").unlink(missing_ok=True)
    sh.backstop(data, store=store, judge=first, now=now, m="shadow", confirm=yes,
                filer=lambda d, store=None, m=None: filed.append(d) or d)
    assert len(cyes) == 1 and len(filed) == 1 and filed[0]["stages"] == [0.8, 0.8, 0.9]


def test_handoff_nudge_counts_show_resolved_versus_filed(tmp_path):
    """Detection for agent-e278947864: `self_heal.py status` reports how many handoff nudges the revised reply
    resolved versus how many still file, so a judge that never resolves (or always does) stays visible."""
    log = tmp_path / "log.jsonl"
    now = 1792660000.0
    rows = [
        {"ts": now - 3000, "event": "reply_nudge", "rule": "handoff", "session_id": "a", "draft_id": "d1"},
        {"ts": now - 2990, "event": "reply_nudge_outcome", "rule": "handoff", "session_id": "a", "draft_id": "d1",
         "resolved": True},
        {"ts": now - 2500, "event": "reply_nudge", "rule": "handoff", "session_id": "b"},
        {"ts": now - 2490, "event": "reply_nudge_outcome", "rule": "handoff", "session_id": "b", "resolved": False},
        {"ts": now - 2000, "event": "reply_nudge", "rule": "handoff", "session_id": "c"},   # no outcome, past grace
        {"ts": now - 60, "event": "reply_nudge", "rule": "handoff", "session_id": "d"},     # no outcome yet
        {"ts": now - 50, "event": "reply_nudge", "rule": "confirm", "session_id": "e"},     # other rule: ignored
        {"ts": now - 9 * 86400, "event": "reply_nudge", "rule": "handoff", "session_id": "old"},  # outside window
    ]
    log.write_text("".join(json.dumps(r) + "\n" for r in rows))
    c = sh.handoff_nudge_counts(tmp_path, now=now)
    assert c == {"days": 7, "nudges": 4, "resolved": 1, "still_handed_off": 1, "no_outcome": 1, "waiting": 1,
                 "filed": 2}


# ── gateway-9aaf151a15: core's update-pause note is a planned pause, not a backend crash ──
def _pause_note(prompt="fix the panel"):
    """The exact text of tui_gateway/update_pause.py pause_note() (no killed jobs)."""
    return ("[System note: Your previous turn was interrupted mid-run — it was paused at a step boundary for a Hermes "
            "update. Every tool result above is complete; continue with the next step and don't redo finished steps. "
            f"The paused request was:]\n\n{prompt}")


def test_update_pause_note_within_land_window_files_nothing():
    llm, calls = judge(0.95, "gateway", "turn interrupted")
    assert sh.resume_signal(_pause_note(), gap_s=540) is None
    assert sh.resume_signal(_pause_note(), gap_s=None) is None
    # nor does the note fall through to the complaint judge
    assert sh.detect_complaint("working on it", _pause_note(), llm, gap_s=540) is None and not calls


def test_update_pause_note_that_did_not_continue_in_time_files_its_own_signal():
    llm, calls = judge(0.0)
    hit = sh.detect_complaint("working on it", _pause_note(), llm, gap_s=2400)
    assert hit and hit["source"] == "signal" and hit["component"] == "gateway" and not calls
    assert hit["symptom"] == "update pause did not continue in time" and hit["gap_s"] == 2400
    crash = sh.resume_signal("[System note: Your previous turn was interrupted mid-run — the app or its backend "
                             "process stopped before the turn could finish. The interrupted request was:]\n\nx", 2400)
    assert hit["fingerprint"] != crash["fingerprint"]


def test_pre_llm_call_update_pause_note_files_nothing(tmp_path, on):
    llm, calls = judge(0.95)
    now = [1000.0]
    store = core.Store(tmp_path / "coach", clock=lambda: now[0])
    _, hooks = plugin.build(store=store, checkout=None, selfheal_sync=True, selfheal_llm=llm)
    hooks["post_llm_call"](session_id="s1", assistant_response="Working on the panel fix now.")
    now[0] += 540  # the observed 9-minute update pause
    hooks["pre_llm_call"](session_id="s1", user_message=_pause_note())
    assert events(tmp_path) == [] and not calls


def test_incident_replay_keeps_update_pause_note_quiet(tmp_path):
    """Detection guard: the replay runs resume notes through resume_signal like production; a pause note is a
    negative turn that must not fire, while a crash note still does."""
    ir = load("selfheal_incident_replay_pause", ROOT / "eval" / "selfheal_incident_replay.py")
    d = tmp_path / "data"
    d.mkdir()
    crash = ("[System note: Your previous turn was interrupted mid-run — the app or its backend process stopped "
             "before the turn could finish. The interrupted request was:]\n\nfix it")
    turns = [("p", 0, _pause_note(), "working", 540), ("k", 0, crash, "working", 1200), ("z", 0, "hi", "", None)]
    (d / "turns.jsonl").write_text("".join(json.dumps({"sid": s, "idx": i, "text": t, "prev_reply": p}) + "\n"
                                           for s, i, t, p, _ in turns))
    (d / "turns2.jsonl").write_text("".join(json.dumps({"sid": s, "idx": i, "prev_log": [], "gap_s": g}) + "\n"
                                            for s, i, _, _, g in turns))
    (d / "episodes.json").write_text(json.dumps([{"eid": "Z", "sid": "z", "first": 0, "last": 0,
                                                  "turns": [{"sid": "z", "idx": 0}]}]))
    (d / "fivewhys.jsonl").write_text(json.dumps({"eid": "Z", "real_incident": True}) + "\n")
    llm, calls = judge(0.95, "gateway", "turn interrupted", "agent_fault")
    ir.run(type("A", (), {"hermes_root": "", "data": str(d), "out": str(tmp_path / "o"), "limit": 0, "workers": 1,
                          "tag": "t"})(), llm=llm)
    res = ir.score(d, ir.jl(tmp_path / "o" / "verdicts.jsonl"), 0.65, 0.65, False, relabel=False)
    fired = {(f["sid"], f["idx"]): f for f in res["fps"]}
    assert ("p", 0) not in fired
    assert fired[("k", 0)]["sym"] == "turn interrupted (backend stopped)"


# Live check of agent-6eb3a51e94: the terminal tool's error line is its JSON result (truncated to 120 chars by the turn
# log), so the symptom read `repeated terminal error: {"output": "Error: unable to open database \" \": unabl`.
def test_repeat_failure_unwraps_a_json_tool_result():
    res = ('{"output": "Error: unable to open database \\"/home/u/.hermes/cache/scratch/probe/x.db\\": unable to open '
           'database file", "exit_code": 1, "error": null}')[:120]
    log = ["-> terminal(sqlite3 /home/u/.hermes/cache/scratch/probe/x.db 'select 1')", f"   <- ERROR {res}"] * 3
    hit = sh.machine_signal("done", log)
    assert hit["symptom"].startswith("repeated terminal error: Error: unable to open database"), hit["symptom"]
    assert "{" not in hit["symptom"] and '\\"' not in hit["symptom"] and '"' not in hit["symptom"]
    err = '{"error": "kanban.db: database is locked (5)", "success": false}'
    lk = sh.machine_signal("done", ["-> kanban_show(task_id='t_01a18f450bc2683c')", f"   <- ERROR {err}"] * 3)
    assert lk["symptom"] == "repeated kanban_show error: kanban.db: database is locked", lk["symptom"]


# Live check of agent-6eb3a51e94: a long-running gateway hot-reloads self_heal.py but keeps the coach __init__ it
# booted with, which passed only the reply tail as evidence. handle() itself must lead with the hit's evidence.
def test_handle_leads_with_the_hit_evidence_whatever_the_caller_passes(tmp_path):
    store = core.Store(tmp_path / "coach")
    hit = sh.machine_signal("done", OVERLAP)
    got = []
    sh.handle(hit, "s1", store, evidence="Done; the fleet shows a clean trend.", m="shadow",
              filer=lambda d, store=None, m=None: got.append(d))
    (det,) = events(tmp_path)
    assert det["evidence"].startswith("kanban_create failed 3x") and "clean trend" in det["evidence"]
    assert "create_overlap: refused" in got[0]["quote"] and "clean trend" in got[0]["quote"]
    # a caller that already leads with it is not doubled
    sh.handle({**hit, "fingerprint": "agent-other"}, "s2", store,
              evidence=f"{hit['evidence']} | reply: Done.", m="shadow", filer=lambda d, store=None, m=None: None)
    det2 = events(tmp_path)[-1]
    assert det2["evidence"].count("kanban_create failed 3x") == 1
