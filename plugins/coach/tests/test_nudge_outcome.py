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
                                "untracked": 0, "filed": 1}
    assert c["report_only"] == {"nudges": 2, "resolved": 0, "still_failing": 0, "no_outcome": 1, "waiting": 0,
                                "untracked": 1, "filed": 1}
    assert c["handoff"]["waiting"] == 1 and "compute_envelope" not in c
