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


# agent-8a6863884a: the same class again, for the review handoff. A MacBook Kanban worker sent its card to review with
# kanban_request_review, which returned run_ended=true ("This run is over ... write your final reply"), and replied
# that venv retirement was "not delivered" (its card forbade renaming/deleting the venv). report_only nudged; the run
# was over, so the revision could change nothing and failed again. A successful review handoff (or a reviewer's
# kanban_request_changes) ends the run and names the next owner: it is a lifecycle park.
RR_ASK = ("work kanban task t_0000000000000007\n\nRetire the old venv: point every launcher at the new runtime. Do NOT "
          "rename or delete the venv and do not start a soak; send the card to review when the launchers moved.")
RR_DRAFT = ("All four launchers now run the new runtime (verified with `which` + a smoke run each). Venv retirement "
            "itself is **not delivered** and still needs a scope decision: the card forbids renaming or deleting the "
            "venv and starting the soak, so I sent the card to review for it.")
RR_ARGS = {"summary": "Launchers moved to the new runtime; venv left in place per the card.",
           "metadata": {"changed_files": ["bin/launch"]}}
RR_OK = json.dumps({"ok": True, "task_id": "t_0000000000000007", "status": "review", "run_ended": True,
                    "next": "This run is over; the card is review. Don't call kanban lifecycle tools "
                            "(block/schedule/continue/complete/request_review) or kanban_link/kanban_unlink on it "
                            "again; write your final reply."})
RR_ERR = json.dumps({"error": "could not request review for t_0000000000000007: unknown id or not in running/ready"})
RC_ARGS = {"reason": "Add the regression test for the refused path before requesting review again."}
RC_OK = json.dumps({"ok": True, "task_id": "t_0000000000000007", "status": "ready", "run_ended": True})


def _rr_check(log):
    return rc.check(RR_DRAFT, RR_ASK, log, _judge(0.95), threshold=0.7, thresholds=rc.settings()["thresholds"],
                    confirm=CONFIRMED, confirm_floor=rc.CONFIRM_FLOOR)


def test_review_handoff_direct_and_deferred_is_a_lifecycle_park():
    for call in (("kanban_request_review", RR_ARGS, RR_OK), _deferred("kanban_request_review", RR_ARGS) + (RR_OK,),
                 ("mcp__kanban_request_review", RR_ARGS, RR_OK)):
        log = _log(BEFORE + [call])
        assert rc.lifecycle_park(log), log
        assert rc.report_only_guard(RR_ASK, RR_DRAFT, log) == "lifecycle_park", call[0]
        assert _rr_check(log) is None, call[0]


def test_reviewer_request_changes_is_a_lifecycle_park():
    for call in (("kanban_request_changes", RC_ARGS, RC_OK), _deferred("kanban_request_changes", RC_ARGS) + (RC_OK,)):
        log = _log(BEFORE + [call])
        assert rc.report_only_guard(RR_ASK, RR_DRAFT, log) == "lifecycle_park", call[0]


def test_refused_review_handoff_still_nudges():
    for call in (("kanban_request_review", RR_ARGS, RR_ERR), _deferred("kanban_request_review", RR_ARGS) + (RR_ERR,)):
        log = _log(BEFORE + [call])
        assert not rc.lifecycle_park(log), log
        assert rc.report_only_guard(RR_ASK, RR_DRAFT, log) is None
        hit = _rr_check(log)
        assert hit and hit["rule"] == "report_only"


def test_kanban_complete_stays_judged():
    log = _log(BEFORE + [("kanban_complete", {"summary": "done"}, json.dumps({"ok": True, "run_ended": True}))])
    assert not rc.lifecycle_park(log)
    hit = _rr_check(log)
    assert hit and hit["rule"] == "report_only"


def test_pre_verify_logs_guard_lifecycle_park_after_a_review_handoff(tmp_path, monkeypatch):
    hooks, logf = _hooks(tmp_path, monkeypatch)
    sid = "w2"
    hooks["pre_llm_call"](session_id=sid, user_message=RR_ASK, platform="kanban")
    hooks["post_tool_call"](tool_name="terminal", session_id=sid, args={"command": "which python3"},
                            result=json.dumps({"exit_code": 0, "output": "/opt/new/bin/python3"}))
    hooks["post_tool_call"](tool_name="kanban_request_review", session_id=sid, args=RR_ARGS, result=RR_OK)
    assert hooks["pre_verify"](session_id=sid, platform="kanban", attempt=0, final_response=RR_DRAFT) is None
    logged = [json.loads(l) for l in logf.read_text().splitlines()]
    assert any(r["event"] == "reply_checked" and r.get("guard") == "lifecycle_park" for r in logged)
    assert not any(r["event"] == "reply_nudge" for r in logged)


# Detection guard: every Kanban lifecycle tool that ends a worker's run is classified, so a new one fails here until
# someone decides whether report_only may still judge the turn it ended. agent-9f966b0682 and agent-8a6863884a were
# both a run-ending tool nobody had classified.
RUN_ENDING_TOOLS = ("kanban_block", "kanban_schedule", "kanban_continue", "kanban_complete", "kanban_move",
                    "kanban_request_review", "kanban_request_changes")


def _classified(tool):
    exempt = set(ar.PARK_TOOLS) | {"kanban_continue"}  # kanban_continue: reply_check.continued_run
    judged = dict(rc.JUDGED_AFTER_RUN_END)
    assert not (tool in exempt and tool in judged), f"{tool} is both exempt and judged"
    if tool in judged:
        assert len(judged[tool].split()) >= 5, f"{tool}: JUDGED_AFTER_RUN_END needs a real reason"
    return tool in exempt or tool in judged


def test_every_run_ending_lifecycle_tool_is_exempt_or_deliberately_judged():
    missing = [t for t in RUN_ENDING_TOOLS if not _classified(t)]
    assert not missing, f"classify in agent_rules.PARK_TOOLS or reply_check.JUDGED_AFTER_RUN_END: {missing}"


def test_core_run_lifecycle_tools_are_all_classified():
    """Read core's own _RUN_LIFECYCLE_TOOLS so a tool core adds there can't slip past the hand-written list."""
    import importlib.util as iu
    import re
    import pytest
    spec = iu.find_spec("hermes_cli")
    src = Path(spec.origin).resolve().parents[1] / "tools" / "kanban_tools.py" if spec and spec.origin else None
    if not src or not src.exists():
        pytest.skip("no Hermes core on the path")
    m = re.search(r"_RUN_LIFECYCLE_TOOLS\s*=\s*frozenset\(\{(.*?)\}\)", src.read_text(), re.S)
    assert m, "core's _RUN_LIFECYCLE_TOOLS moved; update this guard"
    core_tools = set(re.findall(r"[\"'](kanban_\w+)[\"']", m.group(1)))
    assert core_tools, m.group(0)
    assert core_tools <= set(RUN_ENDING_TOOLS), f"new run-ending core tools: {core_tools - set(RUN_ENDING_TOOLS)}"
    assert not [t for t in core_tools if not _classified(t)]
