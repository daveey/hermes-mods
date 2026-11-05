"""report_only vs an owner the turn found running through its own subagent list (self-heal agent-19504d5444).

A background-job notice arrived in an orchestrator chat; the agent ran `delegate_task(action=list)`, saw its recovery
subagent `sa-0-a3d2aa81` running (goal: the work under card t_01a1a000e3d7df47), steered it, and replied "I verified
its recovery subagent is running and directed it to ...". Coach's second opinion still scored the reply 0.9
report_only: agent_rules.observe wrote a `<- OK` line only for Kanban owner reads, and rich_tool_line rendered the list
and steer calls as `-> delegate_task(None)`, so the judges saw no check and no steer text. observe now writes
`<- OK subagents: <id>=<status>(<running>) goal=<head>` after a successful list, and the tool line of a list/steer/stop
call carries its action, subagent id and message.
"""
import importlib.util
import json
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
FIXTURE = json.loads((HERE / "tests" / "fixtures" / "owner_read_delegate_list.json").read_text())
SA = "sa-0-a3d2aa81"
CARD = "t_01a1a000e3d7df47"


def _mod(name):
    spec = importlib.util.spec_from_file_location(f"t_ownersub_{name}", HERE / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


rules = _mod("agent_rules")
rc = _mod("reply_check")


def _ok(lines):
    return [l for l in lines if l.lstrip().startswith("<- OK")]


def _observe_incident_turn(coach, sid="s", with_list=True):
    if with_list:
        coach.observe(sid, "delegate_task", FIXTURE["list_call"], json.dumps(FIXTURE["list_result"]))
    coach.observe(sid, "delegate_task", FIXTURE["steer_call"], json.dumps(FIXTURE["steer_result"]))


def test_list_result_gives_subagent_ok_line():
    line = rules.owner_result_line("delegate_task", FIXTURE["list_call"], json.dumps(FIXTURE["list_result"]))
    assert line is not None, "a successful delegate_task(action=list) must leave a <- OK line"
    assert line.lstrip().startswith("<- OK subagents:"), line
    assert f"{SA}=running" in line, line
    assert "55m" in line, line  # running_seconds 3320.6
    assert CARD in line, line  # the goal head names the owner card
    assert len(line) <= rules.OK_LIST_LINE_MAX, line


def test_list_result_dict_form_and_empty_list():
    assert f"{SA}=running" in (rules.owner_result_line("delegate_task", {"action": "list"}, FIXTURE["list_result"])
                               or "")
    empty = {"action": "list", "count": 0, "subagents": [], "note": "No live subagents right now."}
    line = rules.owner_result_line("delegate_task", {"action": "list"}, json.dumps(empty))
    assert line is not None and "none live" in line, line
    assert "running" not in line, line


def test_finished_awaiting_batch_is_not_running():
    res = dict(FIXTURE["list_result"], finished_awaiting_batch=[{"subagent_id": "sa-1-deadbeef", "status": "completed"}])
    line = rules.owner_result_line("delegate_task", {"action": "list"}, json.dumps(res))
    assert "sa-1-deadbeef=completed" in line and f"{SA}=running" in line, line


def test_spawn_steer_stop_get_no_ok_line():
    assert rules.owner_result_line("delegate_task", FIXTURE["steer_call"], json.dumps(FIXTURE["steer_result"])) is None
    assert rules.owner_result_line("delegate_task", {"action": "stop", "subagent_id": SA}, "{}") is None
    assert rules.owner_result_line("delegate_task", {"tasks": [{"goal": "x"}]}, '{"status": "dispatched"}') is None


def test_steer_tool_line_carries_id_and_message():
    line = rules.rich_tool_line("delegate_task", FIXTURE["steer_call"])
    assert line.startswith("-> delegate_task(action=steer"), line
    assert f"subagent_id={SA}" in line, line
    assert "lock-timing evidence and Board gate checks" in line, line
    assert "None" not in line, line
    lst = rules.rich_tool_line("delegate_task", FIXTURE["list_call"])
    assert lst == "-> delegate_task(action=list)", lst
    stop = rules.rich_tool_line("delegate_task", {"action": "stop", "subagent_id": SA})
    assert stop == f"-> delegate_task(action=stop, subagent_id={SA})", stop
    # spawn keeps its goal form
    assert rules.rich_tool_line("delegate_task", {"tasks": [{"goal": "do X"}]}) == "-> delegate_task(do X)"


def test_observe_incident_turn_feeds_both_judges():
    coach = rules.AgentCoach(None)
    _observe_incident_turn(coach)
    _, log = coach.turn_context("s")
    ok = _ok(log)
    assert len(ok) == 1 and f"{SA}=running" in ok[0], log
    rich = coach.turn_rich("s")
    assert _ok(rich) == ok
    assert any(f"subagent_id={SA}" in l and "lock-timing" in l for l in rich), rich
    # the confirm prompt the outcome judge reads carries the check and the steer text
    prompt = rc.confirm_prompt("[background job finished]", rich, FIXTURE["reply"])
    assert f"<- OK subagents: {SA}=running" in prompt
    assert "lock-timing evidence" in prompt


def test_steer_without_list_has_no_ok_line():
    coach = rules.AgentCoach(None)
    _observe_incident_turn(coach, with_list=False)
    assert _ok(coach.turn_context("s")[1]) == []
    assert _ok(coach.turn_rich("s")) == []


def test_failed_list_has_no_ok_line():
    coach = rules.AgentCoach(None)
    coach.observe("s", "delegate_task", {"action": "list"}, json.dumps({"error": "boom"}), status="error")
    assert _ok(coach.turn_context("s")[1]) == []


def test_rubrics_count_a_running_subagent_as_checked_owner():
    for sys_prompt in (rc.JUDGE_SYS, rc.CONFIRM_SYS):
        assert "<- OK subagents:" in sys_prompt
