"""report_only vs an owner checked with a read-only board query or `ps` (self-heal agent-25f447092d).

A Kanban notice said a PR review asked for one more test. The agent read the runners board with
`sqlite3 -readonly …/kanban.db` (card running, claimed 03:14 UTC, heartbeats every minute) and `ps -p <worker_pid>`
(worker alive), then replied that the author card was already writing the test. Coach held the reply and the rewrite
(p 0.9): agent_rules.owner_read_call counted only kanban_show, cross_task and `hermes kanban show` as owner reads, so
these reads got no `<- OK` line and the judges saw a bare claim. Any successful read-only look at a card's state
(board-db query, kanban CLI read, the worker's process) now writes one.
"""
import importlib.util
import json
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]


def _mod(name):
    spec = importlib.util.spec_from_file_location(f"t_ownersql_{name}", HERE / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


rules = _mod("agent_rules")
rc = _mod("reply_check")

OWNER = "t_01a188c7c974c529"
NOW = 1793589324.0  # 2026-11-02T03:15:24Z, the second read's `date -u`

# The two real terminal calls and results (state.db msgs 631647/631649 and 631661/631662), verbatim.
CMD1 = ("db=~/.hermes/kanban/boards/runners/kanban.db; date -u +%H:%M; sqlite3 -readonly $db \"select summary from "
        "task_runs where task_id='t_01a19a222a77d3c8' order by id desc limit 1\" | cut -c1-1200; echo ===; sqlite3 "
        f"-readonly $db \"select status from tasks where id='{OWNER}'; select kind,datetime(created_at,'unixepoch') "
        f"from task_events where task_id='{OWNER}' order by id desc limit 3\"; T=\"$HOME/.local/bin/token-broker exec "
        "--scope user/github --reason splits -- $HOME/bin/gh\"; $T pr view 2612 -R daveey/babybear --json state -q .state")
OUT1 = ("03:15\nVERDICT: REQUEST_CHANGES (test-only) for PR #2625 at exact head 640717897b6b9f459b9d51cbdd0bc548b1edae65, "
        "base 8733c29aa. R1: no test pins that Replay(allowMissing=true) returns a non-ENOENT read error, and two "
        "error-as-absence mutants survive; the production code is correct (ENOENT-only), so the stop rule is not "
        "triggered. The canonical() delta itself is verified.\n===\nrunning\nheartbeat|2026-11-02 03:14:24\n"
        "claimed|2026-11-02 03:14:17\nworker_bound|2026-11-02 03:14:20\nCLOSED")
CMD2 = (f"db=~/.hermes/kanban/boards/runners/kanban.db; date -u +%T; sqlite3 -readonly $db \"select status,worker_pid "
        f"from tasks where id='{OWNER}'; select kind,datetime(created_at,'unixepoch') from task_events where "
        f"task_id='{OWNER}' order by id desc limit 2\"; ps -o pid,etime,stat --no-headers -p $(sqlite3 -readonly $db "
        f"\"select worker_pid from tasks where id='{OWNER}'\")")
OUT2 = "03:15:24\nrunning|2259396\nheartbeat|2026-11-02 03:15:24\nheartbeat|2026-11-02 03:14:24\n2259396       01:06 Ssl"

ASK = ("[Kanban notice] t_01a19a222a77d3c8 completed: R1 REQUEST_CHANGES (test-only) for PR #2625; the author card "
       f"{OWNER} owns the follow-up test.")
REPLY = ("The missing test for part 7 (PR #2625) is already being written by its author card. I checked that it's "
         "actually working, not just listed as running. The worker process started at 03:14 UTC and has been sending "
         "updates every minute, the last at 03:15:24. Starting a second fix would duplicate the same work, so I "
         f"haven't. The follow-up is owned by card {OWNER}.")


def _res(out):
    return json.dumps({"output": out, "exit_code": 0, "error": None})


def _ok(lines):
    return [l for l in lines if l.lstrip().startswith("<- OK")]


def _incident_turn():
    coach = rules.AgentCoach(None)
    coach.observe("s", "terminal", {"command": CMD1, "timeout": 60}, _res(OUT1))
    coach.observe("s", "terminal", {"command": CMD2, "timeout": 30}, _res(OUT2))
    return coach


# 1. the real commands and outputs produce `<- OK` lines naming the owner, its status and newest activity.
def test_real_sqlite_and_ps_owner_reads_get_ok_lines():
    l1 = rules.owner_result_line("terminal", {"command": CMD1}, _res(OUT1), now=NOW)
    l2 = rules.owner_result_line("terminal", {"command": CMD2}, _res(OUT2), now=NOW)
    for line in (l1, l2):
        assert line and OWNER in line and "status=running" in line, line
        assert len(line) <= rules.OK_LINE_MAX, line
    assert "last=" in l1 and "(60s ago)" in l1, l1
    assert "last=" in l2 and "(0s ago)" in l2, l2
    assert "pid=2259396 alive" in l2, l2
    ok = _ok(_incident_turn().turn_context("s")[1])
    assert len(ok) == 2, ok


# 2. a write to the board never counts as an owner read.
def test_kanban_db_write_gets_no_ok_line():
    for cmd in (f"sqlite3 ~/.hermes/kanban.db \"update tasks set status='ready' where id='{OWNER}'\"",
                f"sqlite3 ~/.hermes/kanban.db \"delete from task_events where task_id='{OWNER}'\"",
                f"sqlite3 ~/.hermes/kanban.db \"update kanban_meta set v=1; select status from tasks where id='{OWNER}'\""):
        assert rules.owner_result_line("terminal", {"command": cmd}, _res("running"), now=NOW) is None, cmd
    code = (f"import sqlite3; c=sqlite3.connect('/home/u/.hermes/kanban.db'); "
            f"c.execute(\"update tasks set status='done' where id='{OWNER}'\")")
    assert rules.owner_result_line("execute_code", {"code": code}, _res("ok"), now=NOW) is None


def test_unrelated_reads_get_no_ok_line():
    for cmd in ("ps aux | grep python", "sqlite3 -readonly ~/.hermes/state.db 'select count(*) from messages'",
                f"echo {OWNER}", "ls ~/.hermes/kanban/boards"):
        assert rules.owner_result_line("terminal", {"command": cmd}, _res("running|1"), now=NOW) is None, cmd


def test_worker_process_reads_by_pgrep_or_proc_count():
    for cmd in (f"pgrep -af 'kanban.*{OWNER}'", f"pid=2259396; cat /proc/$pid/status | head -3  # worker of {OWNER}"):
        assert "worker_ps" in rules.owner_read_forms("terminal", {"command": cmd}), cmd
        line = rules.owner_result_line("terminal", {"command": cmd}, _res("2259396 python3 -m hermes kanban-worker"),
                                       now=NOW)
        assert line and OWNER in line, (cmd, line)


def test_execute_code_board_read_gets_ok_line():
    code = (f"import sqlite3\nc=sqlite3.connect('/home/u/.hermes/kanban.db')\n"
            f"print(c.execute(\"select status from tasks where id='{OWNER}'\").fetchall())")
    line = rules.owner_result_line("execute_code", {"code": code},
                                   json.dumps({"status": "success", "output": "[('running',)]\n"}), now=NOW)
    assert line and OWNER in line and "status=running" in line, line


# 3. the second opinion's prompt built from the turn's rich log carries the OK lines.
def test_confirm_prompt_contains_ok_line():
    coach = _incident_turn()
    rich = coach.turn_rich("s")
    ok = _ok(rich)
    assert len(ok) == 2, rich
    prompt = rc.confirm_prompt(ASK, rich, REPLY)
    for line in ok:
        assert line.strip() in prompt


# 4. the deterministic `unverified` rule counts these reads as an owner check.
def test_unverified_owner_counts_sqlite_and_ps_reads():
    assert rc.unverified_owner(REPLY, []) is not None  # the reply does hand off: without a check it would fire
    coach = _incident_turn()
    assert rc.unverified_owner(REPLY, coach.turn_context("s")[1]) is None
    assert rc.unverified_owner(REPLY, coach.turn_rich("s")) is None
    # the call line alone (OK line lost) still counts, like `-> terminal(hermes kanban show …)`
    assert rc.unverified_owner(REPLY, [f"-> terminal({CMD2[:140]})"]) is None


def test_rubric_names_any_read_of_the_owner_state():
    for text in (rc.JUDGE_SYS, rc.CONFIRM_SYS):
        flat = " ".join(text.split())
        assert "read-only query of the board db" in flat and "worker's process" in flat, flat[:300]
