"""report_only vs an owner the turn found running through a LIST read (self-heal agent-5f2ca3a822).

A "M3 done" notice arrived; the agent ran `kanban_list(board=tools)`, saw M4 `t_01a19715a1a9ccef` running
with its review and deploy cards queued, and said so. Coach held the reply as report_only and the rewrite failed too
("only successful owner check shown is for completed M3; kanban_list has no result"): agent_rules.observe wrote a
`<- OK` line only for single-card reads (kanban_show, cross_task, `hermes kanban show`), so the judges saw a bare
`-> kanban_list(...)`. observe now writes one `<- OK list: <id>=<status>(<age> ago) ...` line for a successful list
read: every open card (not done/archived), oldest first, then how many were closed.
"""
import importlib.util
import json
import re
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
FIXTURE = json.loads((HERE / "tests" / "fixtures" / "owner_read_kanban_list.json").read_text())
M4 = "t_01a19715a1a9ccef"
TODO = ("t_01a19716163f8f2f", "t_01a197166756bd53", "t_01a19716a7861c04")


def _mod(name):
    spec = importlib.util.spec_from_file_location(f"t_ownerlist_{name}", HERE / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


rules = _mod("agent_rules")
rc = _mod("reply_check")


def _coach():
    return rules.AgentCoach(None)  # observe ages cards from wall time; exact ages are checked with now= below


def _line_at_result_time(tool="kanban_list", args=None):
    return rules.owner_result_line(tool, FIXTURE["call"] if args is None else args, json.dumps(FIXTURE["result"]),
                                   now=FIXTURE["result_at"])


def _ok(lines):
    return [l for l in lines if l.lstrip().startswith("<- OK")]


def _assert_list_line(line):
    assert line.lstrip().startswith("<- OK list:"), line
    assert f"{M4}=running" in line, line
    for t in TODO:
        assert f"{t}=todo" in line, line
    assert "t_01a197156f70b7ff" not in line, line  # done M3 is counted, not listed
    assert "done" in line, line
    assert line.index(M4) < line.index(TODO[0]), line  # oldest first
    assert len(line) <= rules.OK_LIST_LINE_MAX, line


# The real incident result (state.db message 612735), replayed through observe: both judge logs carry the line.
def test_observe_logs_open_cards_from_real_kanban_list_result():
    coach = _coach()
    coach.observe("s", "kanban_list", FIXTURE["call"], json.dumps(FIXTURE["result"]))
    _, log = coach.turn_context("s")
    ok = _ok(log)
    assert len(ok) == 1, log
    _assert_list_line(ok[0])
    assert re.search(M4 + r"=running\(\d+[smhd] ago\)", ok[0]), ok[0]
    assert _ok(coach.turn_rich("s")) == ok
    # at the result's own time (22:42:53Z) M4 had started 59 s earlier (22:41:54Z); the todo cards were filed 10 h before
    exact = _line_at_result_time()
    assert f"{M4}=running(59s ago)" in exact and f"{TODO[0]}=todo(10h ago)" in exact, exact


def test_observe_logs_list_via_tool_call_bridge():
    coach = _coach()
    call = {"calls": [{"name": "kanban_list", "arguments": FIXTURE["call"]}]}
    coach.observe("s", "tool_call", call, json.dumps(FIXTURE["result"]))
    coach.observe("s", "tool_call", call, json.dumps(
        {"results": [{"name": "kanban_list", "result": json.dumps(FIXTURE["result"])}]}))
    ok = _ok(coach.turn_context("s")[1])
    assert len(ok) == 2, ok
    for line in ok:
        _assert_list_line(line)


def test_observe_logs_cli_list_text_and_json():
    tasks = FIXTURE["result"]["tasks"]
    glyph = {"done": "✓", "running": "●", "todo": "◻"}
    table = "Board: tools (8 other boards)\n\n" + "\n".join(
        f"{glyph.get(t['status'], '?')} {t['id']}  {t['status']:<9} default               {t['title']}" for t in tasks)
    coach = _coach()
    coach.observe("s", "terminal", {"command": "hermes kanban --board tools list"},
                  json.dumps({"output": table, "exit_code": 0, "error": None}))
    coach.observe("s", "terminal", {"command": "hermes kanban --board tools list --json"},
                  json.dumps({"output": json.dumps(tasks, indent=2), "exit_code": 0, "error": None}))
    ok = _ok(coach.turn_context("s")[1])
    assert len(ok) == 2, ok
    for line in ok:
        _assert_list_line(line)
    assert "ago)" in ok[1], ok[1]  # JSON carries times; the table does not


def test_failed_or_unrelated_list_calls_log_no_ok_line():
    coach = _coach()
    coach.observe("s", "kanban_list", {"board": "nope"}, json.dumps({"error": "unknown board: nope"}))
    coach.observe("s", "terminal", {"command": "hermes kanban boards list"},
                  json.dumps({"output": "default\ntools", "exit_code": 0}))
    coach.observe("s", "terminal", {"command": "ls"}, json.dumps({"output": "kanban list", "exit_code": 0}))
    assert _ok(coach.turn_context("s")[1]) == []


def test_list_line_is_not_a_call_or_error_line():
    coach = _coach()
    coach.observe("s", "kanban_list", FIXTURE["call"], json.dumps(FIXTURE["result"]))
    line = _ok(coach.turn_context("s")[1])[0]
    assert rc._tool_kind(line) is None


def test_existing_single_card_reads_keep_their_form():
    show = {"task": {"id": M4, "status": "running"}, "runs": [{"id": 1, "status": "running", "started_at": 1}],
            "events": [{"kind": "heartbeat", "created_at": FIXTURE["result_at"] - 60}]}
    line = rules.owner_result_line("kanban_show", {"task_id": M4}, json.dumps(show), now=FIXTURE["result_at"])
    assert line.startswith("   <- OK task=") and "status=running" in line and len(line) <= rules.OK_LINE_MAX, line


def test_fresh_ready_owner_reads_list_form():
    line = _line_at_result_time()  # the todo cards were filed 10 h before the read: fresh (< 24 h)
    assert rc.fresh_ready_owner(f"review {TODO[0]} is queued; M4 {M4} runs", [line]) == [TODO[0]]
    stale = line.replace("todo(10h ago)", "todo(3d ago)")
    assert rc.fresh_ready_owner(f"review {TODO[0]} is queued", [stale]) == []


# Detection: the incident's draft is in the report_only gold set, labeled not report_only, with this log.
def test_incident_draft_in_report_only_gold_set():
    fx = json.loads((HERE / "tests" / "fixtures" / "report_only_owner_list.json").read_text())
    case = next(f for f in fx if f["id"] == "owner_list_5f2ca3a822")
    assert case["expect_report_only"] is False and M4 in case["reply"]
    assert _line_at_result_time() in case["log"]
