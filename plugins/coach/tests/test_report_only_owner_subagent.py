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


def test_spawn_and_stop_get_no_ok_line():
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
    assert len(ok) == 2 and f"{SA}=running" in ok[0] and f"steer queued: {SA}" in ok[1], log
    rich = coach.turn_rich("s")
    assert _ok(rich) == ok
    assert any(f"subagent_id={SA}" in l and "lock-timing" in l for l in rich), rich
    # the confirm prompt the outcome judge reads carries the check and the steer text
    prompt = rc.confirm_prompt("[background job finished]", rich, FIXTURE["reply"])
    assert f"<- OK subagents: {SA}=running" in prompt
    assert "lock-timing evidence" in prompt


def test_steer_without_list_shows_the_subagent_alive():
    # agent-ef1e146131: a queued steer is itself proof the subagent is live (core answers `queued` only for a live,
    # steer-accepting record), so a steer-only turn gets an OK line too
    coach = rules.AgentCoach(None)
    _observe_incident_turn(coach, with_list=False)
    ok = _ok(coach.turn_context("s")[1])
    assert len(ok) == 1 and f"steer queued: {SA}" in ok[0], ok
    assert _ok(coach.turn_rich("s")) == ok


def test_failed_list_has_no_ok_line():
    coach = rules.AgentCoach(None)
    coach.observe("s", "delegate_task", {"action": "list"}, json.dumps({"error": "boom"}), status="error")
    assert _ok(coach.turn_context("s")[1]) == []


def test_rubrics_count_a_running_subagent_as_checked_owner():
    for sys_prompt in (rc.JUDGE_SYS, rc.CONFIRM_SYS):
        assert "<- OK subagents:" in sys_prompt


# --- agent-f4adc31e67 / agent-ef1e146131: a steer-only turn, and the owner-kind coverage guard ---------------------
STEER = json.loads((HERE / "tests" / "fixtures" / "owner_read_delegate_steer.json").read_text())
SA2 = "sa-0-ccc4caa7"


def test_steer_only_incident_turn_gets_ok_line():
    """Hub 20261031_064316_d54356, msgs 802934/802936: the turn's only owner check was a steer that came back
    `queued`; reply 802941 "The worker is still active..." was held at p 0.9 because the log showed no result."""
    coach = rules.AgentCoach(None)
    coach.observe("s", "delegate_task", STEER["steer_call"], json.dumps(STEER["steer_result"]))
    rich = coach.turn_rich("s")
    ok = _ok(rich)
    assert ok == [f"   <- OK steer queued: {SA2} (live, accepting steer)"], rich
    prompt = rc.confirm_prompt(STEER["notice"], rich, STEER["reply"])
    assert f"<- OK steer queued: {SA2}" in prompt and "Started by subagent sa-0-ccc4caa7" in prompt


def test_steer_via_tool_call_and_dict_result():
    line = rules.owner_result_line("delegate_task", STEER["steer_call"], STEER["steer_result"])
    assert line and f"steer queued: {SA2}" in line, line


def test_steer_to_finished_subagent_is_marked_not_delivered():
    # core's _finished_payload is a non-error answer, so without this line a steer to a dead subagent looked identical
    # to a live one in the tool log and the rubric's "a steer hands it the work" passed it
    for status in ("already_finished", "finishing"):
        res = dict(STEER["finished_steer_result"], status=status)
        line = rules.owner_result_line("delegate_task", STEER["steer_call"], json.dumps(res))
        assert line == f"   <- OK steer NOT delivered: {SA2}={status} (not running)", line
        assert "queued" not in line


def test_failed_steer_has_no_ok_line():
    coach = rules.AgentCoach(None)
    coach.observe("s", "delegate_task", STEER["steer_call"],
                  json.dumps({"error": f"No live subagent '{SA2}' in this conversation's spawn tree."}), status="error")
    assert _ok(coach.turn_context("s")[1]) == []


def test_rubrics_read_steer_lines():
    for sys_prompt in (rc.JUDGE_SYS, rc.CONFIRM_SYS):
        assert "<- OK steer queued:" in sys_prompt and "<- OK steer NOT delivered:" in sys_prompt


# Detection guard (Root, not caught): every owner kind the report_only rubric accepts as a checked owner needs a
# real-format read that yields an `<- OK` line, or the judges cannot see the check and hold a true reply. The fifth
# false hold in this class (8d8563ea, 1d0803fa, 7649f1d4, 1cd30bb2, this one) came from a kind with no such read.
NOW = 1793737054.0
SESSION = json.loads((HERE / "tests/fixtures/owner_read_cross_session.json").read_text())
OWNER_KIND_READS = {
    "other session": [("cross_session", SESSION["call"], SESSION["result"])],
    "card": [("kanban_show", {"task_id": "t_01a1a273a0cccc63"},
              {"task": {"id": "t_01a1a273a0cccc63", "status": "running"}, "runs": [], "events": []})],
    "worker": [("terminal", {"command": "ps -p 4242 -o pid,etime,cmd; hermes kanban show t_01a1a273a0cccc63"},
                {"output": "  PID ELAPSED CMD\n 4242   10:00 hermes chat\nstatus: running"})],
    "subagent": [("delegate_task", FIXTURE["list_call"], FIXTURE["list_result"]),
                 ("delegate_task", STEER["steer_call"], STEER["steer_result"])],
}
# A kind the rubric names with no OK-producing read yet; each entry names the card that owns adding it.
KNOWN_OWNER_KIND_GAPS = {}


def _rubric_owner_kinds():
    import re
    m = re.search(r"existing owner \(([^)]*)\)", rc.JUDGE_SYS)
    assert m, "JUDGE_SYS no longer lists its owner kinds as 'existing owner (...)'; update this guard"
    kinds = {k.strip() for k in m.group(1).split(",")}
    for sys_prompt in (rc.JUDGE_SYS, rc.CONFIRM_SYS):
        if "subagent the agent spawned is an owner too" in sys_prompt:
            kinds.add("subagent")
    return kinds


def test_every_rubric_owner_kind_has_an_ok_read():
    kinds = _rubric_owner_kinds()
    assert {"card", "worker", "subagent"} <= kinds, kinds
    missing = kinds - set(OWNER_KIND_READS) - set(KNOWN_OWNER_KIND_GAPS)
    assert not missing, f"rubric owner kind(s) {missing} have no real-format read fixture producing an <- OK line"
    for kind, reads in OWNER_KIND_READS.items():
        for tool, args, result in reads:
            line = rules.owner_result_line(tool, args, json.dumps(result), now=NOW)
            assert line and line.lstrip().startswith("<- OK"), (kind, tool, args, line)
    for kind in KNOWN_OWNER_KIND_GAPS:  # a gap that got a read must move into OWNER_KIND_READS
        assert kind not in OWNER_KIND_READS, kind
