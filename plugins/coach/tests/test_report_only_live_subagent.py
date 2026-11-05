"""report_only vs a subagent the PREVIOUS turn spawned and that is still running (self-heal agent-9382d717aa).

Hub 20261031_064851_d0f106: turn N spawned `sa-0-319e4080` to provision a second worker. Turn N+1 (a batch of Kanban
notices, 3 tool calls) said "Second-worker provisioning is underway". The spawn was one turn back, and
prev_turn_section drops the previous turn once this turn has 2+ log lines, so both judges saw no started fix and held
a 1006-char answer as report_only. The coach now asks the live subagent registry before judging and writes one
`<- OK subagents: <id>=running(<age>) ... [live registry]` line for this session's running subagents under 1 h old.
"""
import importlib.util
import time
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
SA = "sa-0-319e4080"


def _mod(name):
    spec = importlib.util.spec_from_file_location(f"t_livesub_{name}", HERE / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


rules = _mod("agent_rules")
rc = _mod("reply_check")
NOW = 1_800_000_000.0


def _rec(sid="s", started=NOW - 60, status="running", sa=SA, goal="Provision the second worker and verify it"):
    return {"subagent_id": sa, "owner_agent_session_id": sid, "started_at": started, "status": status, "goal": goal}


def test_line_names_this_sessions_running_subagent():
    line = rules.live_subagents_line("s", [_rec()], now=NOW)
    assert line is not None and line.lstrip().startswith("<- OK subagents:"), line
    assert f"{SA}=running(60s)" in line, line
    assert "Provision the second worker" in line and "live registry" in line, line
    assert len(line) <= rules.OK_LIST_LINE_MAX


def test_other_sessions_old_and_finished_subagents_are_left_out():
    recs = [_rec(sid="other"), _rec(started=NOW - 2 * 3600, sa="sa-old"), _rec(status="completed", sa="sa-done")]
    assert rules.live_subagents_line("s", recs, now=NOW) is None
    assert rules.live_subagents_line("s", [], now=NOW) is None
    assert rules.live_subagents_line("", [_rec(sid="")], now=NOW) is None


def test_note_live_subagents_feeds_both_judges_once():
    coach = rules.AgentCoach(None)
    coach.turn_start("s", "[kanban notice] t_1 completed")
    coach.observe("s", "kanban_show", {"task_id": "t_1"}, '{"task": {"id": "t_1", "status": "done"}}')
    coach.observe("s", "terminal", {"command": "uptime"}, "load average: 3.1")
    for _ in range(2):
        coach.note_live_subagents("s", [_rec(started=time.time() - 60)])
    _, log = coach.turn_context("s")
    live = [l for l in log if "live registry" in l]
    assert len(live) == 1 and f"{SA}=running" in live[0], log
    assert [l for l in coach.turn_rich("s") if "live registry" in l] == live
    prompt = rc.confirm_prompt("[kanban notice]", coach.turn_rich("s"), "Second-worker provisioning is underway.")
    assert f"<- OK subagents: {SA}=running" in prompt


def test_note_live_subagents_is_a_noop_without_a_match():
    coach = rules.AgentCoach(None)
    coach.turn_start("s", "hi")
    coach.note_live_subagents("s", [_rec(sid="other")])
    assert coach.turn_context("s")[1] == []


def test_registry_reader_tolerates_a_missing_core():
    assert isinstance(rules.registry_subagents(), list)


def test_owner_lines_do_not_count_as_tool_calls_for_prev_turn():
    """The added line must not push a 1-call turn over PREV_TURN_MAX_TOOLS and hide the previous turn."""
    prev = {"gap_s": 30, "rich": ["-> delegate_task(Provision the second worker)"], "reply": "Spawned it."}
    log = ["-> kanban_show(t_1)", f"   <- OK subagents: {SA}=running(1m) [live registry]"]
    assert "PREVIOUS TURN TOOL LOG" in rc.prev_turn_section(prev, log)
    assert rc.prev_turn_section(prev, ["-> a()", "-> b()"]) == ""


def test_rubrics_count_a_live_registry_subagent_as_started():
    for sys_prompt in (rc.JUDGE_SYS, rc.CONFIRM_SYS):
        assert "[live registry]" in sys_prompt
