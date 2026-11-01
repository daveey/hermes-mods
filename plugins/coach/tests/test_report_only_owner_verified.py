"""report_only vs an owner the turn checked and found running (self-heal agent-9516de5af8).

A relay said a fix was in progress on a Kanban card. The agent ran `hermes kanban show --json <card>`, saw the card
`running` with fresh heartbeats, and replied that the fix was underway with an active worker. Coach held the reply
as report_only (p 0.9) and the rewrite failed too ("second lookup has no result"): agent_rules.observe logged each
call's arguments and only FAILED results (`<- ERROR`), so the judges saw the failed first lookup and nothing from the
one that worked. The rubrics already exempt "an owner the tool log shows running", but nothing showed it.

observe now adds one `   <- OK ...` line after a successful owner check (task status, latest run, newest event and
its age), the judges' prompts carry it, and the rubrics say how to read it.
"""
import importlib.util
import json
import time
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]


def _mod(name):
    spec = importlib.util.spec_from_file_location(f"t_ownerok_{name}", HERE / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


rules = _mod("agent_rules")
rc = _mod("reply_check")

CARD = "t_0000000000000abc"
NOW = time.time()


def _show(status="running", run_status="running", events=None):
    """`hermes kanban show --json` / kanban_show shape: task, runs (oldest first), events (oldest first)."""
    events = events if events is not None else [
        {"kind": "created", "payload": {"status": "ready"}, "created_at": int(NOW - 3600), "run_id": None},
        {"kind": "claimed", "payload": {"lock": "hub:1"}, "created_at": int(NOW - 900), "run_id": 11},
        {"kind": "heartbeat", "payload": None, "created_at": int(NOW - 120), "run_id": 11},
    ]
    return {"task": {"id": CARD, "title": "fix the collapsed arm", "body": "x" * 4000, "status": status},
            "latest_summary": None, "parents": [], "children": [], "comments": [], "events": events,
            "runs": [{"id": 10, "status": "crashed", "started_at": int(NOW - 7200)},
                     {"id": 11, "status": run_status, "started_at": int(NOW - 900)}]}


ASK = ("[Message from another Hermes session \"Stage B campaign\" (@session:default@hub/20261028_000000_aaaaaa)]\n\n"
       f"Stage B final. The arm collapse fix is in progress on card {CARD} (1 GPU).")
REPLY = (f"The fix is already underway on **`{CARD}`**: I verified its active worker and fresh heartbeats. It owns "
         "root-cause tracing, regression tests and the repair. I'm keeping that owner rather than launching a duplicate.")


def _observe_lookup(coach, sid, result):
    coach.observe(sid, "terminal", {"command": f"hermes kanban show --json {CARD}"}, json.dumps(result))


def _turn(result_first_fails=True, show=None):
    coach = rules.AgentCoach(None)
    sid = "s1"
    if result_first_fails:  # the real turn: the first lookup hit the wrong board
        coach.observe(sid, "terminal", {"command": f"hermes kanban show {CARD}"},
                      json.dumps({"output": f"no such task: {CARD}", "exit_code": 1, "error": None}))
    _observe_lookup(coach, sid, {"output": json.dumps(show or _show(), indent=2), "exit_code": 0, "error": None})
    return coach, sid


def _ok_lines(lines):
    return [l for l in lines if l.lstrip().startswith("<- OK")]


# (a) observe writes the OK line for the real `hermes kanban show --json` shape (terminal) ...
def test_observe_logs_owner_status_after_successful_cli_show():
    coach, sid = _turn()
    _, log = coach.turn_context(sid)
    ok = _ok_lines(log)
    assert len(ok) == 1, log
    line = ok[0]
    assert CARD in line and "status=running" in line and "run=running" in line and "last=heartbeat" in line, line
    assert "ago" in line and len(line) <= 220, line
    assert log.index(line) == len(log) - 1  # right after the call it answers
    assert _ok_lines(coach.turn_rich(sid)) == ok


# ... and for the kanban_show tool and the deferred tool_call bridge.
def test_observe_logs_owner_status_for_kanban_show_tool_and_tool_call():
    coach = rules.AgentCoach(None)
    coach.observe("s", "kanban_show", {"task_id": CARD}, json.dumps({**_show(), "worker_context": "..."}))
    coach.observe("s", "tool_call", {"calls": [{"name": "kanban_show", "arguments": {"task_id": CARD}}]},
                  json.dumps({"results": [{"name": "kanban_show", "result": json.dumps(_show(status="blocked"))}]}))
    ok = _ok_lines(coach.turn_context("s")[1])
    assert len(ok) == 2 and "status=running" in ok[0] and "status=blocked" in ok[1], ok


def test_no_ok_line_for_failed_or_unrelated_calls():
    coach = rules.AgentCoach(None)
    coach.observe("s", "terminal", {"command": f"hermes kanban show {CARD}"},
                  json.dumps({"output": f"no such task: {CARD}", "exit_code": 1, "error": None}))
    coach.observe("s", "terminal", {"command": "ls -la"}, json.dumps({"output": "a b", "exit_code": 0}))
    coach.observe("s", "read_file", {"path": "/x"}, json.dumps({"content": "status: running"}))
    assert _ok_lines(coach.turn_context("s")[1]) == []


def test_ok_line_keeps_error_readers_unchanged():
    """`<- OK` lines never count as a failed call for the `<- ERROR` readers, and are not call lines."""
    coach, sid = _turn()
    _, log = coach.turn_context(sid)
    assert rc._tool_kind(_ok_lines(log)[0]) is None
    created = ["-> kanban_create(title='fix', assignee=default)", "   <- OK status=ready task=t_1"]
    assert rc.restart_deferral("works until the next restart", created) is None
    assert rc.card_closed(["-> kanban_complete({})", "   <- OK status=done"]) is True


# (b) both judges see the line: the second opinion's prompt (turn_rich) and the nudge outcome's judge (turn_log).
def test_confirm_prompt_and_nudge_outcome_show_owner_status():
    coach, sid = _turn()
    ok = _ok_lines(coach.turn_rich(sid))[0].strip()
    assert ok in rc.confirm_prompt(ASK, coach.turn_rich(sid), REPLY)
    seen = []

    def llm(system, prompt):
        seen.append(prompt)
        return json.dumps({"handoff": 0, "confirm": 0, "unverified": 0, "unwatched": 0, "report_only": 0.1,
                           "evidence": "checked owner is running"})
    ask, log = coach.turn_context(sid)
    res = rc.nudge_outcome("report_only", REPLY, ASK, log, llm, threshold=0.7)
    assert seen and ok in seen[-1], seen
    assert res["resolved"] is True


# (c) the rubrics tell the judges how to read the line.
def test_rubrics_name_the_checked_running_owner_case():
    for text in (rc.JUDGE_SYS, rc.CONFIRM_SYS):
        assert "<- OK" in text
        assert "24 h" in text


# (d) a checked owner that is idle (ready, nothing for days) is shown as such, so the rubric's exemption can't apply.
def test_idle_owner_line_shows_status_and_stale_activity():
    old = int(NOW - 3 * 86400)
    show = _show(status="ready", run_status="crashed",
                 events=[{"kind": "created", "payload": None, "created_at": old, "run_id": None}])
    coach, sid = _turn(result_first_fails=False, show=show)
    line = _ok_lines(coach.turn_context(sid)[1])[0]
    assert "status=ready" in line and "run=crashed" in line and "last=created" in line, line
    assert "3d ago" in line, line


def test_unparseable_success_falls_back_to_result_head():
    coach = rules.AgentCoach(None)
    coach.observe("s", "terminal", {"command": f"hermes kanban show {CARD}"},
                  json.dumps({"output": f"Task {CARD}: fix\n  status:    running\n  assignee:  default", "exit_code": 0}))
    line = _ok_lines(coach.turn_context("s")[1])[0]
    assert "status:    running" in line or "status=running" in line, line
    assert len(line) <= 220
