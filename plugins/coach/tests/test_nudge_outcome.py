"""Self-heal agent-3ba1979f34: the coach's own nudges were filed as user complaints.

A `[coach] status_only: ...` nudge is stored as a user-role message, so the coach digest lists it as a user turn, and
the self-heal backstop judged it as if David had written it (prefilter() let lowercase bracket tags through). The
earlier fix (agent-e278947864) recorded "did the revised reply fix it" only for the handoff rule. These tests pin:
1. prefilter()/backstop() never judge a `[coach]` turn;
2. every reply_check rule's nudge gets one reply_nudge_outcome on the revised reply (live pre_verify hook);
3. signals.collect_coach files a nudge only when its outcome still fails, or no outcome came within the grace window;
4. `self_heal.py status` counts nudges per rule.
"""
import importlib.util
import json
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


sh = load("coach_self_heal_nudge_outcome", ROOT / "self_heal.py")
sg = load("coach_signals_nudge_outcome", ROOT / "signals.py")
core = load("coach_core_nudge_outcome", ROOT / "core.py")
rc = load("coach_reply_check_nudge_outcome", ROOT / "reply_check.py")

# The real nudge text of the 2026-10-29 23:37 PT turn (rc.STATUS_ONLY_NUDGE, evidence filled in).
REAL_NUDGE = rc.STATUS_ONLY_NUDGE.format(
    evidence='asked "why are we still waiting on bb recovery?"; this turn only ran kanban_show, kanban_comment')


# ── 1. the backstop never judges coach nudges ──────────────────────────────────────────────────────────────────

def test_prefilter_skips_coach_nudges_in_any_case():
    for t in (REAL_NUDGE, "[coach] Held this block once: it asks the user to do a step",
              "[Coach] report_only: x", "  [COACH] laptop_job: your reply proposes a cron on the MacBook"):
        assert sh.prefilter(t) is False, t
    # a real user message that merely mentions the coach is still judged
    assert sh.prefilter("why did the [coach] nudge fire on that reply?") is True


def test_backstop_files_nothing_when_the_only_new_turn_is_a_coach_nudge(tmp_path):
    now = time.time()
    p = tmp_path / "data" / "MacBook-Pro-177" / "default" / "20261025_104956_99e2e1.json"
    p.parent.mkdir(parents=True)
    p.write_text(json.dumps({"id": "20261025_104956_99e2e1", "host": "MacBook-Pro-177", "profile": "default",
                             "source": "desktop", "title": "BB recovery",
                             "user_turns": [{"t": now - 60, "text": REAL_NUDGE,
                                             "prev": "Recovery owner t_01a18a95223f7036 is running."}]}))
    calls, filed = [], []

    def judge(system, prompt):
        calls.append(prompt)
        return json.dumps({"p_defect": 0.9, "kind": "agent_fault", "component": "agent",
                           "symptom": "Reported coordination status without advancing or verifying recovery"})
    sh.backstop(tmp_path / "data", store=core.Store(tmp_path / "coach"), judge=judge, now=now, m="shadow",
                confirm=judge, filer=lambda d, store=None, m=None: filed.append(d) or d)
    assert calls == [] and filed == []


# ── 2. every rule's nudge gets an outcome on the revised reply (live pre_verify hook) ───────────────────────────

def _hooks(tmp_path, monkeypatch, verdicts=None, rules=None):
    mod = load("coach_init_nudge_outcome", ROOT / "__init__.py")
    c = mod._load("core")
    rcm = mod._load("reply_check")
    calls = []

    def judge(s, p):
        calls.append(p)
        vs = verdicts or [{k: 0 for k in rcm.RULES}]
        return json.dumps(vs[min(len(calls), len(vs)) - 1])
    monkeypatch.setattr(rcm, "aux_judge", judge)
    monkeypatch.setenv("COACH_SYSTEM_ONE", "off")
    monkeypatch.setenv("COACH_STALLED_GOAL", "0")
    if rules:
        monkeypatch.setenv("COACH_REPLY_RULES", rules)
    else:
        monkeypatch.delenv("COACH_REPLY_RULES", raising=False)
    root = tmp_path / "coach"
    _, hooks = mod.build(store=c.Store(root=root), checkout=None)
    rows = lambda: [json.loads(l) for l in (root / "log.jsonl").read_text().splitlines()]  # noqa: E731
    return hooks, calls, rows


STATUS_REPLY = ("I reopened the recovery card and verified it is ready; the owner t_01a18a95223f7036 has the fix. "
                "It will land once the build finishes.")
FIXED_REPLY = ("I checked the mini directly: fd7d62ac is not installed yet, the build is 55 min in and the governor "
               "holds load at 6.57/core, so the install waits on that build.")


def _status_turn(hooks, sid):
    hooks["pre_llm_call"](session_id=sid, user_message="why are we still waiting on bb recovery?", platform="desktop")
    hooks["post_tool_call"](tool_name="kanban_show", args={"task_id": "t_01a18a95223f7036"}, result="{}",
                            session_id=sid)
    hooks["post_tool_call"](tool_name="kanban_comment", args={"task_id": "t_01a18a95223f7036", "body": "x"},
                            result="{}", session_id=sid)
    out = hooks["pre_verify"](session_id=sid, platform="desktop", attempt=0, final_response=STATUS_REPLY)
    assert out and out["action"] == "continue" and "status_only" in out["message"]


def test_status_only_nudge_fixed_by_the_revision_logs_a_resolved_outcome(tmp_path, monkeypatch):
    hooks, calls, rows = _hooks(tmp_path, monkeypatch)
    sid = "20261025_104956_99e2e1"
    _status_turn(hooks, sid)
    # the revision does the real step (reads the mini) and names the wall
    hooks["post_tool_call"](tool_name="terminal", args={"command": "ssh mini 'cd ~/code/babybear && git log -1'"},
                            result="{}", session_id=sid)
    assert hooks["pre_verify"](session_id=sid, platform="desktop", attempt=1, final_response=FIXED_REPLY) is None
    nudge = [r for r in rows() if r["event"] == "reply_nudge"]
    outs = [r for r in rows() if r["event"] == "reply_nudge_outcome"]
    assert len(nudge) == 1 and nudge[0]["rule"] == "status_only" and nudge[0].get("outcome") is True
    assert len(outs) == 1 and outs[0]["rule"] == "status_only" and outs[0]["resolved"] is True
    assert outs[0]["session_id"] == sid and outs[0].get("draft_id") == nudge[0].get("draft_id")
    # once per nudge: a further attempt logs nothing more
    hooks["pre_verify"](session_id=sid, platform="desktop", attempt=2, final_response=FIXED_REPLY)
    assert len([r for r in rows() if r["event"] == "reply_nudge_outcome"]) == 1


def test_status_only_nudge_ignored_by_the_revision_logs_a_failing_outcome_with_the_reply(tmp_path, monkeypatch):
    hooks, calls, rows = _hooks(tmp_path, monkeypatch)
    sid = "20261025_104956_aaaaaa"
    _status_turn(hooks, sid)
    assert hooks["pre_verify"](session_id=sid, platform="desktop", attempt=1, final_response=STATUS_REPLY) is None
    outs = [r for r in rows() if r["event"] == "reply_nudge_outcome"]
    assert len(outs) == 1 and outs[0]["rule"] == "status_only" and outs[0]["resolved"] is False
    assert "verified it is ready" in outs[0]["reply"]


def test_judged_rule_nudge_gets_an_outcome_from_the_same_judge(tmp_path, monkeypatch):
    z = {k: 0 for k in rc.RULES}
    hooks, calls, rows = _hooks(tmp_path, monkeypatch, rules="confirm", verdicts=[
        {**z, "confirm": 0.9, "evidence": "asks permission to run the tests"},
        {**z, "confirm": 0.1, "evidence": "ran them"}])
    hooks["pre_llm_call"](session_id="s9", user_message="fix the flaky test", platform="desktop")
    out = hooks["pre_verify"](session_id="s9", platform="desktop", attempt=0,
                              final_response="I found the race. Want me to apply the fix and run the tests?")
    assert out and out["action"] == "continue" and len(calls) == 1
    assert hooks["pre_verify"](session_id="s9", platform="desktop", attempt=1,
                               final_response="Applied the fix; tests pass. Should I also open a PR?") is None
    assert len(calls) == 2
    outs = [r for r in rows() if r["event"] == "reply_nudge_outcome"]
    assert len(outs) == 1 and outs[0]["rule"] == "confirm" and outs[0]["resolved"] is True and outs[0]["p"] == 0.1


def test_nudge_outcome_rules_cover_every_reply_check_rule():
    assert set(sg.OUTCOME_RULES) == set(rc.RULES)


# ── 3. signals files the outcome, not the nudge, for every rule ──────────────────────────────────────────────────

NOW = 1793340000.0
SNUDGE = {"ts": NOW - 120, "event": "reply_nudge", "session_id": "20261025_104956_99e2e1", "rule": "status_only",
          "p": 1.0, "evidence": 'asked "why are we still waiting"; this turn only ran kanban_show', "draft_id": "d7",
          "outcome": True}


def _sout(resolved, ts=NOW - 80, **kw):
    return {"ts": ts, "event": "reply_nudge_outcome", "rule": "status_only", "session_id": SNUDGE["session_id"],
            "draft_id": "d7", "p": 0.0 if resolved else 1.0, "resolved": resolved, **kw}


def _home(tmp_path, rows):
    h = tmp_path / "home"
    (h / "coach").mkdir(parents=True)
    (h / "coach" / "log.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    return h


def _nudges(sigs):
    return [s for s in sigs if s["kind"] == "coach" and "fixloop" not in s["fp"]]


def test_status_only_nudge_with_a_resolved_outcome_files_nothing(tmp_path):
    h = _home(tmp_path, [SNUDGE, _sout(True)])
    cur = {}
    assert _nudges(sg.collect_coach(h, cur, NOW)) == []
    assert _nudges(sg.collect_coach(h, cur, NOW + 3600)) == []


def test_status_only_nudge_still_failing_files_one_defect_quoting_the_reply(tmp_path):
    h = _home(tmp_path, [SNUDGE, _sout(False, reply="I reopened the card and verified it is ready.")])
    got = _nudges(sg.collect_coach(h, {}, NOW))
    assert len(got) == 1 and got[0]["session_id"] == SNUDGE["session_id"]
    assert "status_only" in got[0]["symptom"] and "verified it is ready" in got[0]["details"]


def test_status_only_nudge_without_outcome_files_after_the_grace_window(tmp_path):
    h = _home(tmp_path, [SNUDGE])
    cur = {}
    assert _nudges(sg.collect_coach(h, cur, NOW)) == []
    late = _nudges(sg.collect_coach(h, cur, SNUDGE["ts"] + sg.HANDOFF_GRACE_S + 1))
    assert len(late) == 1 and "no outcome" in late[0]["details"]
    assert _nudges(sg.collect_coach(h, cur, SNUDGE["ts"] + 3600)) == []


def test_old_plugin_nudge_without_outcome_marker_is_not_filed_for_non_handoff_rules(tmp_path):
    """A backend still running the old plugin logs non-handoff nudges with no outcome to come: not an ignored nudge."""
    old = {k: v for k, v in SNUDGE.items() if k != "outcome"}
    h = _home(tmp_path, [old])
    assert _nudges(sg.collect_coach(h, {}, NOW + 3600)) == []


# ── 4. detection: status reports nudges per rule ─────────────────────────────────────────────────────────────

def test_nudge_counts_per_rule(tmp_path):
    now = 1792660000.0
    rows = [
        {"ts": now - 3000, "event": "reply_nudge", "rule": "status_only", "session_id": "a", "outcome": True},
        {"ts": now - 2990, "event": "reply_nudge_outcome", "rule": "status_only", "session_id": "a", "resolved": True},
        {"ts": now - 2500, "event": "reply_nudge", "rule": "status_only", "session_id": "b", "outcome": True},
        {"ts": now - 2490, "event": "reply_nudge_outcome", "rule": "status_only", "session_id": "b",
         "resolved": False},
        {"ts": now - 2000, "event": "reply_nudge", "rule": "report_only", "session_id": "c", "outcome": True},
        {"ts": now - 1900, "event": "reply_nudge", "rule": "report_only", "session_id": "c2"},  # old plugin
        {"ts": now - 60, "event": "reply_nudge", "rule": "handoff", "session_id": "d"},
        {"ts": now - 50, "event": "reply_nudge", "rule": "compute_envelope", "session_id": "e"},  # not tracked
    ]
    (tmp_path / "log.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    c = sh.nudge_counts(tmp_path, now=now)
    assert c["status_only"] == {"nudges": 2, "resolved": 1, "still_failing": 1, "no_outcome": 0, "waiting": 0,
                                "untracked": 0, "unconfirmed": 0, "filed": 1}
    assert c["report_only"] == {"nudges": 2, "resolved": 0, "still_failing": 0, "no_outcome": 1, "waiting": 0,
                                "untracked": 1, "unconfirmed": 0, "filed": 1}
    assert c["handoff"]["waiting"] == 1 and "compute_envelope" not in c


# ── 5. report_only outcomes get the same second opinion as the nudge (self-heal agent-e5b6dcf428) ───────────────
# 11-01 00:05 PT, MacBook: the nudge's report_only was confirmed by the strong model, but the revision's outcome was
# judged by the screen alone (0.88 >= report_only_screen 0.85) and filed a defect. Replaying the real turn, the strong
# model said "started" twice: the agent had corrected the running owner card. Each test gives the screen and the
# second opinion separate fakes, so one judge can no longer stand in for both.

RO_ASK = "why does the watch still say the wrong room in the daytime?"
RO_DRAFT = ("What's missing: a daytime source that knows which room you're in. I haven't fixed that; the card I named, "
            "t_01a195df6ca256eb, covers it.")
# The real revised reply (msg 749067), head as logged in the outcome record plus the line the screen quoted.
RO_REVISED = ("The coach was right: the card I named, t_01a195df6ca256eb, only covered Voice ID, so nothing owned the "
              "rest of the missing daytime room source. That gap now has an owner. Checked at 00:05 PT; nothing needs "
              "you.\n\n- **Home Assistant's room sensors can't be used.** I carded this as t_01a195e4c73f27e1; the "
              "worker got the correction at 00:04.\n- Until that card lands, the watch and iPhone's wrong-room "
              "answers stay unmeasured.")
SCREEN_EV = ("'watch and the iPhone's wrong-room answers stay unmeasured' leaves an agent-fixable measurement gap "
             "unaddressed")
CONFIRM_RO = json.dumps({"outcome": "report_only", "agent_fixable": True, "quote": "I haven't fixed that"})
CONFIRM_STARTED = json.dumps({"outcome": "started", "agent_fixable": True, "quote": "That gap now has an owner."})


def _ro_hooks(tmp_path, monkeypatch, confirms, cap=None):
    """Live hooks with a report_only screen that scores every draft 0.88, and a separate strong-model fake."""
    z = {k: 0 for k in rc.RULES}
    mod = load("coach_init_nudge_outcome_ro", ROOT / "__init__.py")
    c = mod._load("core")
    rcm = mod._load("reply_check")
    calls, asked = [], []

    def judge(s, p):
        calls.append(p)
        return json.dumps({**z, "report_only": 0.88, "evidence": SCREEN_EV})

    def aux_confirm(system, prompt):
        asked.append(prompt)
        return confirms[min(len(asked), len(confirms)) - 1]
    monkeypatch.setattr(rcm, "aux_judge", judge)
    monkeypatch.setattr(rcm, "aux_confirm", aux_confirm)
    monkeypatch.setenv("COACH_SYSTEM_ONE", "off")
    monkeypatch.setenv("COACH_STALLED_GOAL", "0")
    monkeypatch.setenv("COACH_REPLY_RULES", "report_only")
    if cap is not None:
        monkeypatch.setenv("COACH_REPORT_ONLY_CONFIRM_CAP", str(cap))
    else:
        monkeypatch.delenv("COACH_REPORT_ONLY_CONFIRM_CAP", raising=False)
    root = tmp_path / "coach"
    _, hooks = mod.build(store=c.Store(root=root), checkout=None)
    rows = lambda: [json.loads(l) for l in (root / "log.jsonl").read_text().splitlines()]  # noqa: E731
    return hooks, calls, rows, asked


def _ro_turn(hooks, sid, revised=RO_REVISED):
    hooks["pre_llm_call"](session_id=sid, user_message=RO_ASK, platform="desktop")
    hooks["post_tool_call"](tool_name="kanban_show", args={"task_id": "t_01a195df6ca256eb"}, result="{}",
                            session_id=sid)
    out = hooks["pre_verify"](session_id=sid, platform="desktop", attempt=0, final_response=RO_DRAFT)
    assert out and out["action"] == "continue", out
    hooks["post_tool_call"](tool_name="kanban_comment", args={"task_id": "t_01a195df6ca256eb",
                                                             "body": "also own the daytime room source"},
                            result="{}", session_id=sid)
    assert hooks["pre_verify"](session_id=sid, platform="desktop", attempt=1, final_response=revised) is None


def test_report_only_outcome_screen_088_confirm_started_is_resolved(tmp_path, monkeypatch):
    """(a) the incident: screen 0.88 on the revision, the strong model says "started" -> resolved, confirm called."""
    hooks, calls, rows, asked = _ro_hooks(tmp_path, monkeypatch, [CONFIRM_RO, CONFIRM_STARTED])
    sid = "20261026_235722_bef6c6"
    _ro_turn(hooks, sid)
    assert len(asked) == 2, "the outcome check never asked the second opinion"
    assert "That gap now has an owner." in asked[1]
    outs = [r for r in rows() if r["event"] == "reply_nudge_outcome"]
    assert len(outs) == 1 and outs[0]["rule"] == "report_only"
    assert outs[0]["resolved"] is True and outs[0]["p"] == 0.1 and outs[0].get("src") == "confirm"
    confs = [r for r in rows() if r["event"] == "report_only_confirm"]
    assert len(confs) == 2 and confs[1].get("via") == "outcome" and confs[1]["session_id"] == sid
    assert confs[0].get("via") != "outcome"
    # the self-heal collector files nothing for it
    h = _home(tmp_path / "h", rows())
    assert _nudges(sg.collect_coach(h, {}, time.time())) == []


def test_report_only_outcome_confirmed_by_the_strong_model_still_fails_and_files(tmp_path, monkeypatch):
    """(b) the second opinion says report_only again -> not resolved, and that verdict files one defect."""
    hooks, calls, rows, asked = _ro_hooks(tmp_path, monkeypatch, [CONFIRM_RO, CONFIRM_RO])
    _ro_turn(hooks, "s_ro_b", revised=RO_DRAFT + " Still looking into it.")
    assert len(asked) == 2
    outs = [r for r in rows() if r["event"] == "reply_nudge_outcome"]
    assert len(outs) == 1 and outs[0]["resolved"] is False and outs[0].get("src") == "confirm"
    assert outs[0]["p"] == 0.9
    h = _home(tmp_path / "h", rows())
    got = _nudges(sg.collect_coach(h, {}, time.time()))
    assert len(got) == 1 and "still failed report_only" in got[0]["details"]


def test_report_only_outcome_with_the_second_opinion_capped_is_unconfirmed_and_not_filed(tmp_path, monkeypatch):
    """(c) daily cap hit after the nudge's own confirm: the outcome is unconfirmed (resolved=None), nothing is filed,
    and the pending nudge does not file later as 'no outcome' either."""
    hooks, calls, rows, asked = _ro_hooks(tmp_path, monkeypatch, [CONFIRM_RO], cap=1)
    _ro_turn(hooks, "s_ro_c")
    assert len(asked) == 1
    outs = [r for r in rows() if r["event"] == "reply_nudge_outcome"]
    assert len(outs) == 1 and outs[0]["resolved"] is None and outs[0].get("unconfirmed") is True
    assert any(r["event"] == "report_only_confirm_capped" for r in rows())
    h = _home(tmp_path / "h", rows())
    cur, now = {}, time.time()
    first = _nudges(sg.collect_coach(h, cur, now))
    # the cap itself still files its own (coach plugin) signal; no agent defect is filed for the outcome
    assert [s["component"] for s in first] == ["plugin"] and "daily cap" in first[0]["symptom"]
    assert [s for s in _nudges(sg.collect_coach(h, cur, now + sg.NUDGE_OPEN_S + 60)) if s["component"] == "agent"] == []
    logged = [json.loads(l) for l in (h / "coach" / "log.jsonl").read_text().splitlines()]
    assert any(r["event"] == "nudge_outcome_unconfirmed" and r["rule"] == "report_only" for r in logged)


def test_replay_of_the_incident_outcome(tmp_path):
    """(d) replay of the 11-01 00:05 PT turn through reply_check.nudge_outcome: the recorded screen verdict (0.88, its
    evidence) and the strong model's replayed label ("started")."""
    z = {k: 0 for k in rc.RULES}
    ths = rc.settings()["thresholds"]
    screen = lambda s, p: json.dumps({**z, "report_only": 0.88, "evidence": SCREEN_EV})  # noqa: E731
    log = ["kanban_show(task_id=t_01a195df6ca256eb) <- OK",
           "kanban_comment(task_id=t_01a195df6ca256eb) <- OK"]
    asked = []
    res = rc.nudge_outcome("report_only", RO_REVISED, RO_ASK, log, screen, threshold=0.7, thresholds=ths,
                           confirm=lambda: asked.append(1) or rc.parse_confirm(CONFIRM_STARTED),
                           confirm_floor=rc.CONFIRM_FLOOR, human_confirm_floor=rc.human_confirm_floor("desktop"))
    assert asked == [1]
    assert res["resolved"] is True and res["p"] == 0.1 and res["src"] == "confirm"
    # without a second opinion at all (the old call) the screen alone is not a verdict that files
    res0 = rc.nudge_outcome("report_only", RO_REVISED, RO_ASK, log, screen, threshold=0.7, thresholds=ths)
    assert res0["resolved"] is False and res0["src"] == "screen"
    # the record the incident actually logged (screen-only, no src) files nothing now
    nudge = {"ts": NOW - 700, "event": "reply_nudge", "session_id": "20261026_235722_bef6c6", "rule": "report_only",
             "nudge_id": "n_e5b6", "p": 0.9, "evidence": "What's missing: a daytime source that knows which room "
             "you're in.", "outcome": True}
    out = {"ts": NOW - 10, "event": "reply_nudge_outcome", "session_id": nudge["session_id"], "rule": "report_only",
           "nudge_id": "n_e5b6", "p": 0.88, "resolved": False, "judged": True, "evidence": SCREEN_EV,
           "reply": RO_REVISED[:300]}
    h = _home(tmp_path, [nudge, out])
    assert _nudges(sg.collect_coach(h, {}, NOW)) == []
    # a deterministic owner hit (p=1.0, closed/unverified owner) still files
    det = {**out, "p": 1.0, "src": "unverified_owner", "evidence": "owned by card t_x (done)"}
    h2 = _home(tmp_path / "2", [nudge, det])
    assert len(_nudges(sg.collect_coach(h2, {}, NOW))) == 1


def test_strong_report_only_sources_match_between_reply_check_signals_and_self_heal():
    assert set(sg.STRONG_REPORT_ONLY_SRC) == set(rc.STRONG_REPORT_ONLY_SRC)
    for src in ("confirm", "closed_owner", "unverified_owner", "screen", "screen_fallback"):
        o = {"rule": "report_only", "resolved": False, "p": 0.9, "src": src}
        assert sg.unconfirmed_outcome(o) is sh._unconfirmed_outcome(o) is (src not in rc.STRONG_REPORT_ONLY_SRC)


def test_nudge_counts_puts_screen_only_report_only_outcomes_under_unconfirmed(tmp_path):
    now = 1792660000.0
    rows = [{"ts": now - 300, "event": "reply_nudge", "rule": "report_only", "session_id": "u", "outcome": True},
            {"ts": now - 290, "event": "reply_nudge_outcome", "rule": "report_only", "session_id": "u",
             "resolved": None, "unconfirmed": True, "src": "screen_fallback", "p": 0.9}]
    (tmp_path / "log.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    c = sh.nudge_counts(tmp_path, now=now)["report_only"]
    assert c["unconfirmed"] == 1 and c["still_failing"] == 0 and c["filed"] == 0
