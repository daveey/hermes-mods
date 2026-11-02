"""report_only nudge for a Kanban worker whose card forbids both exits (self-heal agent-0e5f1ab1f9).

Hub card t_01a194ca90736cea (run 13, Nov 2 2026) parked with kanban_block(kind="dependency") behind
t_01a18a95223f7036, a scheduled card idle 81 h behind a quota-reserve reset, and replied "BB-324's repair has **not
started**". The report_only nudge offered "do it here or re-place it" (STALLED_OWNER) and "propose the exact fix".
The card body forbade both moves and a headless worker's proposal reaches nobody, so the agent argued ("would violate
the explicit task constraints") and failed again. The parks the check does accept (lifecycle_park: a timed park or a
human_request block) were never named. On a Kanban turn the nudge now names both; other platforms keep their text.
"""
import importlib.util
import json
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]


def _mod(name):
    spec = importlib.util.spec_from_file_location(f"t_kparks_{name}", HERE / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


rc = _mod("reply_check")
ar = _mod("agent_rules")

FIXTURE = json.loads((HERE / "tests" / "fixtures" / "report_only_kanban_parks.json").read_text())
INCIDENT = next(f for f in FIXTURE if f["id"] == "kanban_dependency_park_0e5f1ab1f9")
ASK = INCIDENT["user"]
DRAFT = INCIDENT["reply"]
CARD = "t_01a194ca90736cea"
DEP_ARGS = {"kind": "dependency", "reason": "Await linked existing admission producer t_01a18a95223f7036, not soaking."}
DEP_OK = json.dumps({"ok": True, "task_id": CARD, "run_id": 27746, "status": "todo", "block_kind": "dependency",
                     "warning": "kanban-wait-chain-guard: t_01a18a95223f7036 has made no progress in 81h (status "
                                "scheduled, idle 81h). parking here is not a started fix"})
TIME_ARGS = {"kind": "time", "reason": "SCHEDULED_UNTIL=2026-11-03T13:00:00Z — Claude weekly reset gates the BB-324 "
                                       "admission (mini 0.75 reserve); a quota reset is not a soak."}
TIME_OK = json.dumps({"ok": True, "task_id": CARD, "status": "scheduled"})
HUMAN_ARGS = {"kind": "needs_input", "reason": "1) Decide whether BB-324 may exceed the mini's 0.75 quota reserve.",
              "human_request": {"action": "Reply yes or no. Unblock = run BB-324 above the reserve; archive = wait "
                                          "for the reset.",
                                "why_human": "the card forbids exceeding the quota reserve without David",
                                "interaction": "approval", "scope": "BB-324 admission",
                                "evidence": "pool .790 >= .75 reserve; reset 2026-11-03T12:59Z"}}
BLOCKED_OK = json.dumps({"ok": True, "task_id": CARD, "status": "blocked"})
SCHED_ARGS = {"reason": "SCHEDULED_UNTIL=2026-11-03T13:00:00Z — quota reset", "clock_only": True}


def _log(calls):
    coach = ar.AgentCoach()
    for tool, args, res in calls:
        coach.observe("s", tool, args, res)
    return list(coach.sessions["s"]["turn_log"])


def _judge(p):
    def llm(system, prompt):
        return json.dumps({"handoff": 0, "confirm": 0, "unverified": 0, "unwatched": 0, "report_only": p,
                           "evidence": "BB-324's repair has not started"})
    return llm


CONFIRMED = lambda: (0.9, "ends on an unstarted repair")  # noqa: E731


def _check(log, **kw):
    return rc.check(DRAFT, ASK, log, _judge(0.95), threshold=0.7, thresholds=rc.settings()["thresholds"],
                    confirm=CONFIRMED, confirm_floor=rc.CONFIRM_FLOOR, **kw)


def _has_park_exits(text):
    assert "kanban_block(kind='time'" in text and "SCHEDULED_UNTIL=" in text
    assert "kanban_schedule" in text
    assert "kind='needs_input'" in text and "human_request" in text
    assert "dependency park" in text and "not an ending" in text


def test_kanban_nudge_names_both_parks():
    _has_park_exits(rc.nudge_for("report_only", "BB-324's repair has not started", platform="kanban"))


def test_other_platforms_keep_the_old_text():
    expected = (rc.NUDGES["report_only"].format(evidence="ev") + rc.REPORT_ONLY_WHOLE_REPLY)
    for plat in ("", "desktop", "tui", "cli", "subagent"):
        assert rc.nudge_for("report_only", "ev", platform=plat) == expected
        assert rc.nudge_for("report_only", "ev") == expected
    # the other rules are untouched on Kanban too
    assert rc.nudge_for("handoff", "ev", platform="kanban") == rc.NUDGES["handoff"].format(evidence="ev")


def test_card_closed_and_kanban_down_variants_have_no_park_exit():
    # a closed card can't park, and a broken board can't take the park call: those templates stay as they were
    assert "SCHEDULED_UNTIL" not in rc.nudge_for("report_only", "ev", platform="kanban", card_closed=True)
    assert "SCHEDULED_UNTIL" not in rc.nudge_for("report_only", "ev", platform="kanban", kanban_down=True)


def test_check_on_the_incident_dependency_park_nudges_with_park_exits():
    log = _log([("kanban_block", DEP_ARGS, DEP_OK)])
    assert not rc.lifecycle_park(log)  # a dependency park asks nobody: still judged
    hit = _check(log, platform="kanban")
    assert hit and hit["rule"] == "report_only"
    _has_park_exits(hit["nudge"])
    desktop = _check(log)
    assert "SCHEDULED_UNTIL" not in desktop["nudge"]


def test_time_park_and_human_block_end_the_turn():
    for calls in ([("kanban_block", DEP_ARGS, DEP_OK), ("kanban_block", TIME_ARGS, TIME_OK)],
                  [("kanban_block", HUMAN_ARGS, BLOCKED_OK)],
                  [("kanban_schedule", SCHED_ARGS, TIME_OK)]):
        log = _log(calls)
        assert rc.lifecycle_park(log), calls
        assert rc.report_only_guard(ASK, DRAFT, log) == "lifecycle_park"
        assert _check(log, platform="kanban") is None


def test_judges_say_a_dependency_park_is_not_started():
    for sys_prompt in (rc.JUDGE_SYS, rc.CONFIRM_SYS):
        assert "kind=dependency" in sys_prompt and "not a started fix" in sys_prompt


def test_fixture_is_a_positive_in_the_eval():
    assert INCIDENT["expect_report_only"] is True
    assert "has **not started**" in INCIDENT["reply"]
    ev = (HERE / "eval" / "report_only_eval.py").read_text()
    assert "report_only_kanban_parks.json" in ev


def _hooks(tmp_path, monkeypatch):
    monkeypatch.delenv("COACH_REPLY_RULES", raising=False)
    spec = importlib.util.spec_from_file_location("coach_init_kparks", HERE / "__init__.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    core = mod._load("core")
    rcl = mod._load("reply_check")
    monkeypatch.setattr(rcl, "aux_judge", lambda s, p: json.dumps(
        {"handoff": 0, "confirm": 0, "unverified": 0, "unwatched": 0, "report_only": 0.97,
         "evidence": "BB-324's repair has not started"}))
    monkeypatch.setattr(rcl, "aux_confirm", lambda s, p: json.dumps(
        {"problem": True, "agent_fixable": True, "outcome": "report_only", "user_wanted_action": True,
         "quote": "BB-324's repair has not started"}))
    store = core.Store(root=tmp_path / "coach")
    _, hooks = mod.build(store=store, checkout=None)
    return hooks


def test_pre_verify_passes_kanban_platform_into_the_nudge(tmp_path, monkeypatch):
    hooks = _hooks(tmp_path, monkeypatch)
    hooks["pre_llm_call"](session_id="w1", user_message=ASK, platform="kanban")
    hooks["post_tool_call"](tool_name="kanban_block", session_id="w1", args=DEP_ARGS, result=DEP_OK)
    res = hooks["pre_verify"](session_id="w1", platform="kanban", attempt=0, final_response=DRAFT)
    assert res and res["action"] == "continue"
    _has_park_exits(res["message"])


def test_pre_verify_desktop_nudge_unchanged(tmp_path, monkeypatch):
    hooks = _hooks(tmp_path, monkeypatch)
    hooks["pre_llm_call"](session_id="d1", user_message="fix BB-324", platform="desktop")
    res = hooks["pre_verify"](session_id="d1", platform="desktop", attempt=0, final_response=DRAFT)
    assert res and "SCHEDULED_UNTIL" not in res["message"]
