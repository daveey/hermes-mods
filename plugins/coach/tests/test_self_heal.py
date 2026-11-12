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
    # gateway/kanban_watchers_notifier.py stuck/wait notice (kanban-3ba089808c: filed as David's complaint, then the
    # waiting card itself was adopted as the defect's fix owner)
    '⏳ [default] @default Kanban t_01a1cc08f1eab72f: t_01a1cc08f1eab72f (Kanban: a failed worker_result), which this '
    'chat waits on, has been ready 30 min with no worker (waiting for a pool worker (admission_limits))',
    '[chat-closeout] Every kanban card this chat handed work to has settled.\nblocked: needs approval',
    # kanban-22e8da03db (same ⏳ notice), then every other watcher-notice symbol
    "⏳ [default] @default Kanban t_01a1ca3c42501a9a: t_01a1ca3c42501a9a (GitHub user quota brake tripped), which "
    "this chat waits on, has been ready 30 min with no worker (32 card(s) ahead of it, 6/7 worker slots busy).",
    "⏱ [default] @default Kanban t_12345678 ran past 60 minutes and was stopped; it will be retried automatically.",
    "⛔ [default] @default Kanban t_12345678 is now blocked: 3 failures. Fix the cause, then unblock it.",
    "🛑 [default] @default Kanban t_12345678 routed to TRIAGE — needs attention",
    "👀 [default] @default Kanban t_12345678 ready for review — Fix the bug",
    "🔄 @default Kanban t_12345678 → running",
    "ℹ [default] Kanban t_12345678: note",
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
    "Kanban t_12345678 has been ready 30 min with no worker, fix the dispatcher",
    "why is Kanban t_12345678 still unclaimed?",
    "?? Kanban t_12345678 is stuck again",
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


def test_machine_signal_flags_core_failed_turn_notices():
    """agent-c46bf512ae: 15 turns ended on core's failed-turn boundary and no turn-end signal fired."""
    partial = ("This turn did not complete. Some actions may already have run; verify their effects "
               "before resending.")
    restart = ("Hermes restarted before this turn finished. It continues automatically once the backend is back; "
               "send it again only if nothing happens within a few minutes.")
    for notice in (partial, restart,
                   "Your request was not processed. Send it again if you still want me to carry it out."):
        assert sh.machine_signal(notice)["kind"] == "failed_turn"
        assert sh.machine_signal(notice)["source"] == "signal"
    assert sh.machine_signal("Done. I noted that 'This turn did not complete' is core's notice text.") is None


# provider-1bc7008942: finished replies that QUOTED the "not processed" notice (they reported the fix for it) were
# filed as "turn failed with provider error". These are the three real replies (hub state.db rows), opening verbatim.
QUOTING_REPLIES = (
    # row 721644, 20261102_205456_6898ba
    "The fix for the refusal message is now live on hermes-hub. I checked: the installed Hermes includes commit "
    "`16d4f566e09`, and the gateway restarted at 01:26:59Z, after the update. If the model provider blocks a turn now, "
    "the chat says it was refused and gives the reason, instead of \"Your request was not processed\".\n\nThat fix is "
    "done. The only open item from that session is the DoorDash renewal check, which runs on Nov 5 at 19:00 UTC.",
    # row 732851, 20261102_205456_6898ba
    "Everything from this chat is finished. Nothing is running and nothing needs you.\n\n- **Refusal message fix:** "
    "when the model provider refuses a turn, the chat now says so and gives the reason, instead of \"Your request was "
    "not processed\". It was merged as PR #709 and is live on hermes-hub.",
    # row 244757, 20261023_052109_5b3dee
    "Ignore my last reply (\"Your request was not processed\"). That came from the interrupted turn, and nothing needs "
    "resending.\n\nA third fix in this thread is now merged and live.",
)


def test_finished_reply_quoting_a_provider_error_is_not_a_provider_error():
    for reply in QUOTING_REPLIES:
        assert sh.machine_signal(reply) is None, reply[:80]
    # row 575946: a status report naming another service's rate limit after a 40+ char lead-in
    assert sh.machine_signal("PR #511 has green CI and approved review. GitHub’s API rate limit blocked the guarded "
                             "merge; scheduled a retry after quota reset.") is None


def test_real_provider_error_replies_still_flag_with_matched_span():
    for reply in ("Sorry — this message was not processed (API call failed).", "Sorry, not processed.",
                  "API call failed after 3 retries: 529 overloaded",
                  "⚠️ Provider error: upstream returned 502",
                  "Rate limited by the provider; retry budget exhausted.",
                  # core lease notices (agent/turn_facade_lease.py): the phrase ends a short notice
                  "Stopped waiting for another Hermes process on this session. Your message was not processed.",
                  "This chat was taken over by another Hermes window. Resume it explicitly to continue here. "
                  "Your message was not processed.",
                  "⏳ Another Hermes process kept this session busy too long. Your message was not processed - wait "
                  "for the other process to finish, then send it again."):
        hit = sh.machine_signal(reply)
        assert hit and hit["kind"] == "api_error" and hit["fingerprint"] == "provider-1bc7008942", reply
        assert sh.API_ERR_RE.search(hit["evidence"]), hit  # the card shows what matched, not the reply tail


def test_quoted_or_mid_sentence_error_phrases_never_flag():
    """Detection guard: every provider-error phrase and every core failed-turn notice, quoted or mid-sentence."""
    phrases = ["not processed", "API call failed", "provider error", "rate limited by the provider, retries exhausted",
               *sorted(sh.FAILED_TURN_NOTICES)]
    for p in phrases:
        for q in ('"{}"', "'{}'", "`{}`", "“{}”"):
            quoted = q.format(p)
            for reply in (f"Fixed: a blocked turn now gives the reason instead of {quoted}.",
                          f"Ignore {quoted}; that was the cut-off turn. Everything else is done.",
                          f"{quoted} came from the interrupted turn. Nothing needs resending."):
                assert sh.machine_signal(reply) is None, reply
        mid = (f"Shipped the coach change and checked the hub log for the whole week: there was no {p} anywhere, "
               "so nothing needs you.")
        assert sh.machine_signal(mid) is None, mid


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


# agent-7f4144a961: a RED/GREEN test cycle (the same test command failed 3x while the agent edited, then passed) was
# filed as a stuck "repeated terminal error" loop. A loop the identical command later got out of is not a defect.
RED = ["-> terminal(python3 -m unittest test_listening_metadata_l)",
       "   <- ERROR test_burst_rollover_is_contiguous (test_listening_metadata_l.T) ... FAIL"]


def test_repeat_failure_skips_a_loop_the_same_call_later_passed():
    assert sh.machine_signal("done", RED * 3 + [RED[0]]) is None  # ends on the passing call (no result line)
    assert sh.machine_signal("done", RED * 3 + [RED[0], "   <- OK ran 5 tests"]) is None
    assert sh.machine_signal("done", RED * 3 + [RED[0], "-> read_file(x.py)"]) is None


def test_repeat_failure_still_fires_when_a_different_call_passes():
    hit = sh.machine_signal("done", RED * 3 + ["-> terminal(git status)", "-> read_file(x.py)"])
    assert hit and hit["kind"] == "repeat_failure"
    # the identical call passing BEFORE the failures does not excuse them, nor does a pass followed by a 4th failure
    assert sh.machine_signal("done", [RED[0]] + RED * 3)["kind"] == "repeat_failure"
    assert sh.machine_signal("done", RED * 3 + [RED[0]] + RED)["kind"] == "repeat_failure"


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


def test_audit_prompt_keeps_the_end_of_a_long_request():
    """agent-feed37636b: a reviewer brief said "Read the full diff of EVERY path" at char 1156 and allowed a coverage
    row marked "partial, with why" at char 3929. The audit kept only the first 1500 chars, so the judge saw the rule
    but not the permission and filed the honest partial row as a self-admission (p=0.99)."""
    head = "Read the full diff of EVERY path. " * 50
    tail = " A coverage row may be partial or no, with why. Deliverable: the coverage table, then the verdict."
    ask = (head + "x" * 4000)[:4000 - len(tail)] + tail
    assert len(ask) == 4000
    p = sh.admission_prompt("Lane T1 APPROVE; 51 of 52 full, 1 partial (blob-verified).", ["-> read_file(a)"], ask)
    assert ask[-200:] in p and ask[:1500] in p
    assert "[...]" in p and len(p) < len(ask) + 1000  # head + tail, not the whole brief
    short = "fix the sidebar sort " * 40  # <= 3000 chars: sent whole, no marker
    q = sh.admission_prompt("done", None, short)
    assert short.strip() in q and "[...]" not in q


def test_audit_rule_names_allowed_disclosed_scope_limits_as_honest():
    assert "disclosed scope limit" in sh.AUDIT_SYS and "unless" in sh.AUDIT_SYS.split("disclosed scope limit")[1][:500]


def test_admission_detect_logs_what_each_audit_stage_scored(tmp_path):
    """agent-c9e7ffb868, agent-110a933b6c, agent-ad20c8a5f7 were filed at p 0.92-0.96 and closed not_defect, yet a
    replay of the same prompts through the cascade scores them 0.0-0.55: selfheal_detect logged only the final p, so
    nothing showed which stage (judge or confirm model) filed them. The detection now carries both."""
    reply = "I was wrong earlier: I stopped the worker before you answered."
    s1 = lambda s, p: json.dumps({"p_defect": 0.7, "kind": "admission", "component": "agent", "symptom": "a"})
    c = lambda s, p: json.dumps({"p_defect": 0.95, "kind": "admission", "component": "agent", "symptom": "b"})
    v = sh.audit_verdict(reply, s1, confirm=c)
    assert (v["p_defect"], v["stages"], v["stage1_p"], v["stage1_kind"]) == (0.95, "judge+confirm", 0.7, "admission")
    assert sh.audit_verdict(reply, s1)["stages"] == "judge"  # no confirm model configured
    assert sh.audit_verdict(reply, lambda s, p: json.dumps({"p_defect": 0.5, "kind": "admission", "component": "a",
                                                             "symptom": "x"}), confirm=c)["stages"] == "judge"
    hit = sh.detect_admission(reply, s1, confirm=c)
    store = core.Store(tmp_path / "coach")
    sh.handle(hit, "s1", store, evidence=reply, m="shadow", filer=lambda d, store=None, m=None: None)
    (det,) = [e for e in events(tmp_path) if e["event"] == "selfheal_detect"]
    assert (det["p"], det["stages"], det["stage1_p"]) == (0.95, "judge+confirm", 0.7)


def test_replay_admission_samples_with_the_production_confirm_cascade(tmp_path):
    replay = load("selfheal_replay_adm_test", ROOT / "eval" / "selfheal_replay.py")
    data = tmp_path / "rows.jsonl"
    rows = [{"id": "neg", "label": 0, "text": "That was my mistake in my scratch script; fixed it.", "ask": "x",
             "tool_log": []},
            {"id": "pos", "label": 1, "text": "I was wrong: I stopped the worker before you answered.", "ask": "y",
             "tool_log": []},
            {"id": "quiet", "label": 0, "text": "All done, tests pass.", "ask": "z", "tool_log": []}]
    data.write_text("".join(json.dumps(r) + "\n" for r in rows))
    s1, conf = [], []

    def llm(system, prompt):
        s1.append(prompt)
        return json.dumps({"p_defect": 0.85, "kind": "admission", "component": "agent", "symptom": "x"})

    def confirm(system, prompt):
        conf.append(prompt)
        p = 0.95 if "stopped the worker" in prompt else 0.2
        return json.dumps({"p_defect": p, "kind": "admission", "component": "agent", "symptom": "y"})
    out = tmp_path / "out"
    replay.main(["--data", str(data), "--out", str(out), "--mode", "admission", "--samples", "3", "--confirm",
                 "--workers", "1"], llm=llm, confirm_llm=confirm)
    rep = json.loads((out / "report.json").read_text())
    assert rep["threshold"] == sh.AUDIT_THRESHOLD and rep["samples"] == 3 and rep["confirm"] is True
    assert rep["per_row"]["pos"]["sample_hits"] == 3 and rep["per_row"]["neg"]["sample_hits"] == 0
    assert rep["per_row"]["quiet"]["ps"] == []  # the prefilter never sends a clean reply to the judge
    assert len(s1) == 6 and len(conf) == 6  # 3 samples x 2 prefiltered rows, every admission >= GATE confirmed
    assert len(set(s1)) == 2  # samples reuse the production prompt; the cache keys keep them apart
    assert (rep["all"]["tp"], rep["all"]["fp"]) == (1, 0) and "| `pos` | 1 | 3/3 |" in (out / "report.md").read_text()
    # a rerun is served from the cache, samples and confirm stage included
    calls = []
    replay.main(["--data", str(data), "--out", str(out), "--mode", "admission", "--samples", "3", "--confirm"],
                llm=lambda s, p: calls.append(1) or "{}", confirm_llm=lambda s, p: calls.append(1) or "{}")
    assert not calls


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


@pytest.mark.parametrize("case,adopted", [
    ("late", True), ("real_reply", False), ("other_session", False),
    ("success_then_failure", False), ("declined", False), ("expired", False),
    ("filed_at", True), ("future_owner", False),
])
def test_backstop_late_failed_turn_nudge(tmp_path, monkeypatch, case, adopted):
    import time
    now = time.time()
    owner_at = now - 15 * 3600  # owner at 23:45, resume fifteen hours later
    notice = "Your request was not processed. Send it again if you still want me to carry it out."
    turns = [{"t": owner_at - 60, "text": "do the work", "prev": "Ready."},
             {"t": owner_at + 420, "text": "try again", "prev": notice}]
    if case == "success_then_failure":
        turns.append({"t": now - 600, "text": "next job", "prev": "Finished the earlier job."})
    turns.append({"t": now - 1, "text": "resume", "prev": "Finished." if case == "real_reply" else notice})
    p = tmp_path / "data" / "hub" / "default" / "s1.json"
    p.parent.mkdir(parents=True)
    p.write_text(json.dumps({"id": "s1", "source": "desktop", "user_turns": turns}))
    store = core.Store(tmp_path / "coach")
    lane = sh.LaneState(store.root)
    lane.update("owner", status="declined" if case == "declined" else "not_defect" if case == "expired" else "fixed",
                session_id="other" if case == "other_session" else "s1", fix_card="t_abcdef01",
                complaint_at=None if case == "filed_at" else now + 600 if case == "future_owner" else owner_at,
                filed_at=owner_at, closed_at=now - (sh.NOT_DEFECT_DAYS + 1) * 86400)
    llm, _ = judge(0.99, component="agent", symptom="stopped without finishing", kind="agent_fault")
    results = sh.backstop(tmp_path / "data", store=store, judge=llm, now=now, lookback=60,
                          confirm=None, m="live", filer=lambda d, **kw: sh._adopt_complaint_lane(
                              d, d["fingerprint"], store, now))
    assert len(results) == 1
    assert bool(results[0] and results[0].get("adopted")) is adopted
    if adopted:
        assert results[0]["via"] == "failed_turn_nudge"
        assert results[0]["card"] == "t_abcdef01"
        events = [json.loads(line) for line in (store.root / "log.jsonl").read_text().splitlines()]
        assert any(e.get("event") == "selfheal_adopted_owner" and e.get("via") == "failed_turn_nudge" for e in events)


@pytest.mark.parametrize("notice", [
    "Your request was not processed. Send it again if you still want me to carry it out.",
    "This turn did not complete. Some actions may already have run; verify their effects before resending.",
    "Hermes restarted before this turn finished. It continues automatically once the backend is back; "
    "send it again only if nothing happens within a few minutes.",
    "An automated delivery was interrupted and was not run again. Some actions may already have run; verify their effects.",
    "The model provider's content filter refused this turn (category: policy). "
    "Try to rephrase your question, or start a new chat or fork from before the problematic context; "
    "a long conversation can contribute to a filter trigger.",
])
def test_failed_turn_boundaries_are_exact(notice):
    assert sh._failed_turn_boundary({"prev": notice})
    assert not sh._failed_turn_boundary({"prev": "I investigated this notice: " + notice})
    assert not sh._failed_turn_boundary({"prev": ""})
    assert sh._failed_turn_boundary({"prev": "localized boundary", "prev_display_kind": "failed_turn"})


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


def test_backstop_tells_the_judge_how_many_tool_calls_followed_the_last_text():
    """agent-b6540f8eef: the digest judge saw "TOOL LOG ... (no tool calls)" for every backstop turn (it has no log),
    so a reply ending "Reading the card first:" plus "what's going on with this?" filed as "promised verification but
    made no tool calls" whether or not tools ran. The context now carries digest `prev_tools`, or says it is unknown."""
    turns = [{"t": 1, "text": "verify the card", "prev": ""},
             {"t": 2, "text": "what's going on with this?", "prev": "Reading the card first:", "prev_tools": 4}]
    dg = {"title": "Verify", "last_reply": ""}
    ctx = sh.backstop_context(dg, turns, turns[1])
    assert "Tool calls after the agent's last text: 4" in ctx
    p = sh.complaint_prompt(turns[1]["prev"], turns[1]["text"], context=ctx)
    assert "(no tool calls)" not in p  # the digest has no tool log: never state that none ran
    legacy = dict(turns[1]); legacy.pop("prev_tools")
    lctx = sh.backstop_context(dg, [turns[0], legacy], legacy)
    assert "Tool calls after the agent's last text: unknown" in lctx
    assert "never claim" in sh.COMPLAINT_SYS.lower() and "no tool calls" in sh.COMPLAINT_SYS


@pytest.mark.parametrize("prev_tools, filed_n", [(3, 0), (0, 1)])
def test_backstop_drops_a_no_tool_stall_claim_when_tools_ran(tmp_path, prev_tools, filed_n):
    import time as _t
    now = _t.time()
    p = tmp_path / "data" / "hub" / "default" / "s1.json"
    p.parent.mkdir(parents=True)
    p.write_text(json.dumps({"id": "s1", "host": "hub", "profile": "default", "source": "desktop", "title": "x",
                             "user_turns": [{"t": now - 60, "text": "what's going on with this?",
                                             "prev": "Reading the card first, together with the PR:",
                                             "prev_tools": prev_tools}]}))
    store = core.Store(tmp_path / "coach")
    llm, _ = judge(0.95, component="agent", symptom="promised verification but made no tool calls", kind="agent_fault")
    filed = []
    sh.backstop(tmp_path / "data", store=store, judge=llm, now=now, m="shadow", confirm=None,
                filer=lambda d, store=None, m=None: filed.append(d) or d)
    assert len(filed) == filed_n
    if not filed_n:
        assert any(e.get("why") == "tools_ran" for e in events(tmp_path, "selfheal_skip"))


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


def test_nudge_counts_pair_a_late_id_outcome_and_wait_for_delivery(tmp_path):
    """agent-98f77b1ca9: the status counters match by nudge_id with no time limit, like signals.collect_coach,
    and an id nudge's wait starts at its delivery record (NUDGE_OPEN_S while undelivered)."""
    now = 1792660000.0
    rows = [
        {"ts": now - 5000, "event": "reply_nudge", "rule": "report_only", "session_id": "a", "nudge_id": "n1",
         "outcome": True},
        {"ts": now - 5000 + 918, "event": "reply_nudge_delivered", "rule": "report_only", "session_id": "a",
         "nudge_id": "n1"},
        {"ts": now - 5000 + 926, "event": "reply_nudge_outcome", "rule": "report_only", "session_id": "a",
         "nudge_id": "n1", "resolved": True},
        {"ts": now - 1200, "event": "reply_nudge", "rule": "report_only", "session_id": "b", "nudge_id": "n2",
         "outcome": True},                                                   # undelivered, live: waiting
        {"ts": now - 4000, "event": "reply_nudge", "rule": "report_only", "session_id": "c", "nudge_id": "n3",
         "outcome": True},
        {"ts": now - 3000, "event": "reply_nudge_delivered", "rule": "report_only", "session_id": "c",
         "nudge_id": "n3"},                                                  # delivered 50 min ago: no outcome
    ]
    (tmp_path / "log.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    c = sh.nudge_counts(tmp_path, now=now)["report_only"]
    assert (c["resolved"], c["waiting"], c["no_outcome"], c["filed"]) == (1, 1, 1, 1), c

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


# ── gateway-833d39a686: a pause note is timed from when the paused turn stopped, not from when it started ──
def _state_db(home, rows):
    """A scratch <HERMES_HOME>/state.db holding `rows` = [(session_id, role, timestamp), ...]."""
    import sqlite3
    home.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(home / "state.db")
    c.execute("create table messages (id integer primary key, session_id text, role text, content text, "
              "timestamp real)")
    c.executemany("insert into messages (session_id, role, content, timestamp) values (?, ?, 'x', ?)", rows)
    c.commit()
    c.close()


def _long_turn_then_pause(tmp_path, monkeypatch, last_row_age_s, db=True):
    """A turn that started 146 min ago and ran until it paused `last_row_age_s` ago; then core's pause note."""
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    t0 = 1_800_000_000.0
    now = [t0]
    store = core.Store(tmp_path / "coach", clock=lambda: now[0])
    llm, calls = judge(0.0)  # the long turn's own message is no complaint
    _, hooks = plugin.build(store=store, checkout=None, selfheal_sync=True, selfheal_llm=llm)
    hooks["pre_llm_call"](session_id="s1", user_message="fix the panel")  # the long turn starts: coach file written
    assert events(tmp_path) == []
    judged = len(calls)
    end = t0 + 146 * 60
    if db:
        _state_db(home, [("s1", "user", t0), ("s1", "assistant", t0 + 60), ("s1", "tool", end - last_row_age_s),
                         ("other", "tool", end - 10)])
    else:
        home.mkdir(parents=True, exist_ok=True)
        (home / "state.db").write_text("not a database")
    now[0] = end
    hooks["pre_llm_call"](session_id="s1", user_message=_pause_note())
    assert len(calls) == judged  # a pause note never reaches the judge


def test_pre_llm_call_pause_after_long_turn_is_timed_from_the_pause(tmp_path, on, monkeypatch):
    """The observed incident: a 2 h 21 min turn paused for 5 min. The pause continued in time: nothing is filed."""
    _long_turn_then_pause(tmp_path, monkeypatch, last_row_age_s=298)
    assert events(tmp_path) == []


def test_pre_llm_call_pause_that_stalled_after_long_turn_still_files(tmp_path, on, monkeypatch):
    _long_turn_then_pause(tmp_path, monkeypatch, last_row_age_s=61 * 60)
    ev = events(tmp_path)
    assert len(ev) == 1 and ev[0]["symptom"] == "update pause did not continue in time"
    assert ev[0]["fingerprint"] == "gateway-833d39a686"
    assert ev[0]["evidence"].startswith("update pause continued 61 min later.")


def test_pre_llm_call_pause_with_unreadable_state_db_stays_quiet_and_logs_skip(tmp_path, on, monkeypatch):
    _long_turn_then_pause(tmp_path, monkeypatch, last_row_age_s=0, db=False)
    assert events(tmp_path) == []
    skip = events(tmp_path, "selfheal_skip")
    assert len(skip) == 1 and skip[0]["why"] == "pause_gap_unknown" and skip[0]["session_id"] == "s1"
    assert skip[0]["fingerprint"] == "gateway-833d39a686"


def test_incident_replay_times_pause_notes_from_the_pause(tmp_path):
    """Detection guard (gateway-833d39a686): like production, the replay times a pause note by `pause_gap_s` (last
    assistant/tool row before the note), never by the turn-start `gap_s`. Turn started 146 min ago, paused 5 min:
    must stay quiet; a pause that stalled 61 min still fires; no pause gap at all stays quiet."""
    ir = load("selfheal_incident_replay_pause2", ROOT / "eval" / "selfheal_incident_replay.py")
    d = tmp_path / "data"
    d.mkdir()
    turns = [("q", 0, 8767, 298), ("s", 0, 8767, 61 * 60), ("u", 0, 8767, None), ("z", 0, None, None)]
    (d / "turns.jsonl").write_text("".join(json.dumps({"sid": s, "idx": i, "text": _pause_note() if s != "z" else "hi",
                                                       "prev_reply": "working"}) + "\n" for s, i, _, _ in turns))
    (d / "turns2.jsonl").write_text("".join(json.dumps({"sid": s, "idx": i, "prev_log": [], "gap_s": g,
                                                        "pause_gap_s": pg}) + "\n" for s, i, g, pg in turns))
    (d / "episodes.json").write_text(json.dumps([{"eid": "Z", "sid": "z", "first": 0, "last": 0,
                                                  "turns": [{"sid": "z", "idx": 0}]}]))
    (d / "fivewhys.jsonl").write_text(json.dumps({"eid": "Z", "real_incident": True}) + "\n")
    llm, _ = judge(0.0)
    ir.run(type("A", (), {"hermes_root": "", "data": str(d), "out": str(tmp_path / "o"), "limit": 0, "workers": 1,
                          "tag": "t"})(), llm=llm)
    res = ir.score(d, ir.jl(tmp_path / "o" / "verdicts.jsonl"), 0.65, 0.65, False, relabel=False)
    fired = {(f["sid"], f["idx"]): f for f in res["fps"]}
    assert ("q", 0) not in fired and ("u", 0) not in fired
    assert fired[("s", 0)]["sym"] == "update pause did not continue in time"


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


# agent-1f57a3258a: a hub subagent read a MacBook card 3x while the MacBook slept. cross_task answered each time with
# `"retryable": true` and "the gateway was unavailable ... Retry in a few minutes", the agent said so and handed the
# read up, and the repeat-failure rule still filed "repeated cross_task error" (p 0.85): it counted a sleeping remote
# gateway as a broken tool. The turn log keeps only 120 chars of a result, which cut the error before both the
# retryable flag and RemoteBackendDownError, so observe() now marks such a line and the rule skips marked lines.
_rules_gw = load("coach_agent_rules_test_sh_gw", ROOT / "agent_rules.py")
GW_CARD = "t_01a1b2ccc8865d8f"
GW_DOWN = ("could not read task t_01a1b2ccc8865d8f: lookup failed on macbook: Error invoking remote method "
           "'hermes:api': RemoteBackendDownError: Remote backend \"conn:macbook::default\" is down (connect failed: "
           "SSH operation to daveey@test-key-gen timed out. The connection may be half-open (e.g. after sleep); "
           "reconnecting., 124s ago); next try in 14s. Failing fast instead of wa\u2026[truncated]. This does not mean "
           "the task is missing; the gateway was unavailable (e.g. draining for a restart). Retry in a few minutes.")


def _gw_turn(results, tool="cross_task"):
    """The real path: agent_rules.observe builds the turn log machine_signal reads (session 20261106_214444_efbd42)."""
    coach = _rules_gw.AgentCoach(None)
    for i, res in enumerate(results):
        coach.observe("s", tool, {"action": "read", "limit": 6 + i, "target": f"@task:default@macbook/{GW_CARD}"},
                      json.dumps(res))
    return coach.turn_context("s")[1]


def test_retryable_gateway_down_repeats_are_not_a_defect():
    log = _gw_turn([{"ok": False, "error": GW_DOWN, "retryable": True}] * 3)
    assert sum("<- ERROR" in ln for ln in log) == 3, log  # still logged as errors for the other readers
    assert sh.machine_signal("The MacBook is unreachable; handing the read to the parent.", log) is None
    # the same lines untruncated (a log that kept the whole result) are skipped too
    full = [f"-> cross_task(read {GW_CARD})", "   <- ERROR " + json.dumps({"ok": False, "error": GW_DOWN,
                                                                         "retryable": True})] * 3
    assert sh.machine_signal("done", full) is None


def test_gateway_errors_that_are_not_retryable_down_still_file():
    no_flag = sh.machine_signal("done", _gw_turn([{"ok": False, "error": GW_DOWN}] * 3))
    assert no_flag and no_flag["kind"] == "repeat_failure" and "cross_task" in no_flag["symptom"], no_flag
    alias = sh.machine_signal("done", _gw_turn([{"ok": False, "retryable": True,
                                                 "error": "no gateway named 'hub-ec2'; known: macbook, hermes-hub"}] * 3))
    assert alias and alias["kind"] == "repeat_failure", alias
    missing = sh.machine_signal("done", _gw_turn([{"ok": False, "error": f"no such task {GW_CARD}"}] * 3))
    assert missing and missing["kind"] == "repeat_failure", missing


def test_other_retryable_errors_still_file():
    rl = {"ok": False, "retryable": True, "error": "GitHub API rate limit exceeded for installation; retry after 60s"}
    hit = sh.machine_signal("done", _gw_turn([rl] * 3, tool="github_api"))
    assert hit and hit["kind"] == "repeat_failure" and "github_api" in hit["symptom"], hit
