"""report_only vs a run-ending park (self-heal agent-9f966b0682).

A Kanban worker hit a real conflict between two of the user's rules, blocked its card for his decision with
kanban_block(kind="needs_input", human_request=...) (the coach's block-time check passed it), and closed with "H5 is
blocked, not complete, on the remaining scope decision". report_only nudged that reply as a diagnosis left unowned.
A block ends the run, so the revision turn had no run to act in: its kanban_schedule was refused and its revised reply
failed again. A successful human block or kanban_schedule this turn is the protocol's park, already judged at block
time by block_handoff; report_only must not judge the same turn again. Dependency/transient/assignment blocks ask
nobody and stay judged.
"""
import importlib.util
import json
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]


def _mod(name):
    spec = importlib.util.spec_from_file_location(f"t_park_{name}", HERE / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


rc = _mod("reply_check")
ar = _mod("agent_rules")

ASK = ("work kanban task t_0000000000000005\n\nH5: free disk on the build host, then soak the cleanup timer for 24 h "
       "and prove it held.")
DRAFT = ("H5 is blocked, not complete, on the remaining scope decision. The cleanup timer is installed and ran once "
         "(freed 41 GB). Two of your rules conflict: \"don't wait for any soaking\" and \"weakening acceptance is "
         "failure\" — the 24 h soak is in the acceptance. I blocked the card asking which one wins; the soak still "
         "needs to run if you keep it.")
BLOCK_ARGS = {"kind": "needs_input",
              "reason": "1) Decide whether the 24 h soak stays in the acceptance or is dropped, then unblock.",
              "human_request": {"action": "Reply keep or drop. Unblock = run the soak; archive = drop the card.",
                                "why_human": "two of the user's standing rules conflict", "interaction": "reply",
                                "scope": "H5 acceptance", "evidence": "AGENTS.md soak rule vs Definition of Done"}}
OK = json.dumps({"ok": True, "task_id": "t_0000000000000005", "status": "blocked"})
ERR = json.dumps({"ok": False, "error": "task t_0000000000000005 is not running"})
SCHED_ARGS = {"reason": "SCHEDULED_UNTIL=2026-11-03T12:00:00Z — waiting for the 24 h soak window",
              "clock_only": True}
BEFORE = [("terminal", {"command": "systemctl --user status disk-cleanup.timer"}, json.dumps({"exit_code": 0}))]


def _log(calls):
    """The turn log exactly as AgentCoach.observe writes it."""
    coach = ar.AgentCoach()
    for tool, args, res in calls:
        coach.observe("s", tool, args, res)
    return list(coach.sessions["s"]["turn_log"])


def _deferred(name, args):
    return ("tool_call", {"calls": [{"name": name, "arguments": args}]})


def _judge(p):
    def llm(system, prompt):
        return json.dumps({"handoff": 0, "confirm": 0, "unverified": 0, "unwatched": 0, "report_only": p,
                           "evidence": "Reports H5 blocked without fixing it"})
    return llm


CONFIRMED = lambda: (0.9, "stops at the diagnosis")  # noqa: E731


def _check(log):
    return rc.check(DRAFT, ASK, log, _judge(0.95), threshold=0.7, thresholds=rc.settings()["thresholds"],
                    confirm=CONFIRMED, confirm_floor=rc.CONFIRM_FLOOR)


def test_direct_human_block_is_a_lifecycle_park():
    log = _log(BEFORE + [("kanban_block", BLOCK_ARGS, OK)])
    assert _check(log) is None  # the replayed H5 turn: no report_only nudge after a successful human block
    assert rc.lifecycle_park(log)
    assert rc.report_only_guard(ASK, DRAFT, log) == "lifecycle_park"


def test_deferred_human_block_is_a_lifecycle_park():
    log = _log(BEFORE + [_deferred("kanban_block", BLOCK_ARGS) + (OK,)])
    assert rc.report_only_guard(ASK, DRAFT, log) == "lifecycle_park"
    assert _check(log) is None


def test_kanban_schedule_direct_and_deferred_is_a_lifecycle_park():
    for call in (("kanban_schedule", SCHED_ARGS, OK), _deferred("kanban_schedule", SCHED_ARGS) + (OK,)):
        log = _log(BEFORE + [call])
        assert rc.report_only_guard(ASK, DRAFT, log) == "lifecycle_park", call[0]


def test_goal_mode_time_block_is_a_lifecycle_park():
    log = _log(BEFORE + [("kanban_block", {"kind": "time", "reason": SCHED_ARGS["reason"]}, OK)])
    assert rc.report_only_guard(ASK, DRAFT, log) == "lifecycle_park"


def test_refused_block_or_schedule_still_nudges():
    for call in (("kanban_block", BLOCK_ARGS, ERR), _deferred("kanban_schedule", SCHED_ARGS) + (ERR,)):
        log = _log(BEFORE + [call])
        assert not rc.lifecycle_park(log), log
        assert rc.report_only_guard(ASK, DRAFT, log) is None
        hit = _check(log)
        assert hit and hit["rule"] == "report_only"


def test_agent_owned_blocks_stay_judged():
    for args in ({"kind": "dependency", "reason": "waiting on t_0000000000000006"},
                 {"kind": "transient", "reason": "flaky", "wake_check": "true"},
                 {"kind": "assignment", "reason": "wrong backend"},
                 {"kind": "capability", "reason": "needs ssh", "needs": ["ssh:example"]}):
        log = _log(BEFORE + [("kanban_block", args, OK)])
        assert not rc.lifecycle_park(log), args
        assert rc.report_only_guard(ASK, DRAFT, log) is None
        hit = _check(log)
        assert hit and hit["rule"] == "report_only", args


def test_lines_that_only_mention_the_tools_are_not_parks():
    log = _log(BEFORE + [("tool_describe", {"names": ["kanban_block", "kanban_schedule"]}, OK),
                         ("terminal", {"command": "grep -n kanban_block reply_check.py"}, json.dumps({"exit_code": 0}))])
    assert not rc.lifecycle_park(log)
    # hand-written lines (no observe marker) that only mention the tools
    assert not rc.lifecycle_park(["-> tool_describe(kanban_schedule)", "-> terminal(grep kanban_block x.py)"])
    assert rc.report_only_guard(ASK, DRAFT, log) is None


def _hooks(tmp_path, monkeypatch):
    monkeypatch.delenv("COACH_REPLY_RULES", raising=False)
    spec = importlib.util.spec_from_file_location("coach_init_park", HERE / "__init__.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    core = mod._load("core")
    rcl = mod._load("reply_check")
    monkeypatch.setattr(rcl, "aux_judge", lambda s, p: json.dumps(
        {"handoff": 0, "confirm": 0, "unverified": 0, "unwatched": 0, "report_only": 0.97, "evidence": "e"}))
    monkeypatch.setattr(rcl, "aux_confirm", lambda s, p: json.dumps({"outcome": "report_only", "agent_fixable": True, "quote": "H5 is blocked"}))
    store = core.Store(root=tmp_path / "coach")
    _, hooks = mod.build(store=store, checkout=None)
    return hooks, tmp_path / "coach" / "log.jsonl"


def test_pre_verify_logs_guard_lifecycle_park_after_a_human_block(tmp_path, monkeypatch):
    hooks, logf = _hooks(tmp_path, monkeypatch)
    sid = "w1"
    hooks["pre_llm_call"](session_id=sid, user_message=ASK, platform="kanban")
    hooks["post_tool_call"](tool_name="kanban_block", session_id=sid, args=BLOCK_ARGS, result=OK)
    assert hooks["pre_verify"](session_id=sid, platform="kanban", attempt=0, final_response=DRAFT) is None
    logged = [json.loads(l) for l in logf.read_text().splitlines()]
    assert any(r["event"] == "reply_checked" and r.get("guard") == "lifecycle_park" for r in logged)
    assert not any(r["event"] == "reply_nudge" for r in logged)
