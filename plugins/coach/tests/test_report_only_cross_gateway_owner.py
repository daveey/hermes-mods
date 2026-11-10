"""report_only vs an owner card whose home is another gateway (self-heal agent-a8184f8541).

Hub Kanban worker, 2026-11-06 22:11Z: the card's authorized fallback filed a comment-only card on the MacBook board
(t_01a1b2df741df295, created 12 min earlier, ready, no runs) and the agent ended "the Mac card owns it". cross_task
was down (MacBook backend unreachable), so it read the card with `hermes-hub tasks --gw macbook --all --json > file`
plus a python filter of that file by card id. The coach held the reply and its revision at p 0.9
(owner_fresh_ready=false). The owner was real: it ran and completed 12 min later.

1. Neither call was an owner read the coach knew (OWNER_READ_SHELL_FORMS: kanban CLI, board sqlite, ps), so no
   `<- OK` line.
2. The coach's own fallback read (named_card_facts -> board_card_facts) looked only at this host's boards; the hub
   holds every gateway's board replica in kanban/sync-shadow/<board>.db and the card was there (created_on=macbook).

Now board_card_facts falls back to the sync-shadow replicas (line tagged `home=<gw> (replica)`), `hermes-hub tasks`
and `hermes kanban wait-check task` are owner-read forms (a read saved to a file counts when a later call filters it
by card id), and report_only_scored logs where the owner evidence came from (`owner_source`).
"""
import importlib.util
import json
import sqlite3
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]


def _mod(name):
    spec = importlib.util.spec_from_file_location(f"t_xgw_{name}", HERE / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


rules = _mod("agent_rules")
rc = _mod("reply_check")

OWNER = "t_01a1b2df741df295"
TARGET = "t_01a1a6dc1e62c8ea"
CREATED = 1794002351          # 2026-11-06T21:59:11Z, the owner card's `created` event on the MacBook board
T_REPLY = 1794003065.0        # the revised reply (state.db msg 969847), 12 min later

# The revised reply, verbatim (msg 969847).
REPLY = (
    f"Verified fallback card **{OWNER}** exists on MacBook with the original darkwake evidence, including "
    "`11578c5b63`. Its creation receipt is `done`; independent owner-published readback shows it **ready**, newly "
    "created with no failed run.\n\n"
    f"That open, comment-only card owns posting the evidence onto **{TARGET}** and reading back the resulting "
    "comment. No duplicate follow-up is needed. This task is complete under its explicitly authorized fallback "
    "branch; actual comment delivery is not yet confirmed.\n\nNo Mac services restarted or updates forced.")

SHADOW_SCHEMA = """
create table tasks (id text primary key, title text, status text, created_at integer, started_at integer,
                    completed_at integer, current_run_id integer, created_on text);
create table task_events (id integer primary key autoincrement, task_id text, run_id integer, kind text,
                          payload text, created_at integer);
create table task_runs (id integer primary key autoincrement, task_id text, status text, outcome text,
                        started_at integer, ended_at integer);
create table task_links (parent_id text, child_id text, primary key (parent_id, child_id));
"""


def _shadow(path, created_on="macbook"):
    """The hub's replica of the MacBook default board, row as it stood at the reply (ready), plus the owner's real
    later events (claimed 22:21:59Z, completed 22:23:03Z): events and runs after the reply must not count."""
    c = sqlite3.connect(path)
    c.executescript(SHADOW_SCHEMA)
    c.execute("insert into tasks (id, status, created_at, created_on) values (?,?,?,?)",
              (OWNER, "ready", CREATED - 1, created_on))
    for kind, at in (("created", CREATED), ("claimed", 1794003719), ("spawned", 1794003720),
                     ("heartbeat", 1794003744), ("completed", 1794003783)):
        c.execute("insert into task_events (task_id, kind, created_at) values (?,?,?)", (OWNER, kind, at))
    c.execute("insert into task_runs (task_id, status, outcome, started_at) values (?,?,?,?)",
              (OWNER, "done", "completed", 1794003719))
    c.commit()
    c.close()
    return str(path)


def _local(path):
    c = sqlite3.connect(path)
    c.executescript(SHADOW_SCHEMA)
    c.commit()
    c.close()
    return str(path)


# ── (a) the coach's own read finds a card that lives only in another gateway's replica ──────────────────────────
def test_a_named_card_found_in_sync_shadow_replica(tmp_path, monkeypatch):
    monkeypatch.setenv("COACH_BOARD_DBS", _local(tmp_path / "kanban.db"))
    monkeypatch.setenv("COACH_SHADOW_DBS", _shadow(tmp_path / "default.db"))
    lines = rc.named_card_facts(REPLY, [], now=T_REPLY)
    assert len(lines) == 1, lines  # TARGET is on no board here
    line = lines[0]
    assert line.lstrip().startswith(f"<- BOARD task={OWNER} status=") and "home=macbook (replica)" in line, line
    assert "created=11m" in line and "claimed=never" in line and "run=none" in line, line
    assert "last=11m(created)" in line, line


def test_a_later_claim_and_run_do_not_count(tmp_path, monkeypatch):
    monkeypatch.setenv("COACH_BOARD_DBS", _local(tmp_path / "kanban.db"))
    monkeypatch.setenv("COACH_SHADOW_DBS", _shadow(tmp_path / "default.db"))
    facts = rules.board_card_facts(OWNER, now=T_REPLY)
    assert facts and facts["home"] == "macbook" and facts["replica"] is True, facts
    assert facts["status"] == "ready" and facts["run"] is None and facts["claimed_at"] is None, facts


def test_a_local_board_wins_and_explicit_dbs_skip_replicas(tmp_path, monkeypatch):
    shadow = _shadow(tmp_path / "default.db")
    monkeypatch.setenv("COACH_SHADOW_DBS", shadow)
    local = _shadow(tmp_path / "kanban.db", created_on="hub")  # same card on this host's own board
    monkeypatch.setenv("COACH_BOARD_DBS", local)
    facts = rules.board_card_facts(OWNER, now=T_REPLY)
    assert facts and not facts.get("replica") and facts["db"] == local, facts
    assert "(replica)" not in rules.board_fact_line(facts, now=T_REPLY)
    # explicit dbs (replays) read only those
    assert rules.board_card_facts(OWNER, now=T_REPLY, dbs=[_local(tmp_path / "other.db")]) is None


def test_a_test_board_override_never_reads_this_hosts_real_replicas(tmp_path, monkeypatch):
    monkeypatch.setenv("COACH_BOARD_DBS", _local(tmp_path / "kanban.db"))
    monkeypatch.delenv("COACH_SHADOW_DBS", raising=False)
    assert rules.shadow_dbs() == []


def test_a_shadow_dbs_default_lists_only_db_files(tmp_path, monkeypatch):
    monkeypatch.delenv("COACH_BOARD_DBS", raising=False)
    monkeypatch.delenv("COACH_SHADOW_DBS", raising=False)
    monkeypatch.setenv("HERMES_KANBAN_HOME", str(tmp_path))
    d = tmp_path / "kanban" / "sync-shadow"
    d.mkdir(parents=True)
    _shadow(d / "default.db")
    for junk in ("default.db-wal", "default.db-shm", "default.db.dispatch.lock"):
        (d / junk).write_text("")
    assert rules.shadow_dbs() == [str(d / "default.db")]


# ── (b) the two real tool calls of the turn produce an `<- OK` owner line ───────────────────────────────────────
HUB_TASKS_CMD = ("/usr/bin/python3 -S fallback_wake.py; hermes-hub tasks --gw macbook --all --json > "
                 "macbook-tasks.json")
FILTER_CMD = ("/usr/bin/python3 -S -c 'import json; d=json.load(open(\"macbook-tasks.json\")); "
              "print(type(d).__name__); rows=d if isinstance(d,list) else d.get(\"records\",d.get(\"tasks\",[])); "
              f"print(json.dumps([r for r in rows if any(x in json.dumps(r) for x in [\"{OWNER}\",\"{TARGET}\"])],"
              "indent=2))'")


def _hub_row(cid, status, created, started=None, run=None, children=(), parents=()):
    """One `hermes-hub tasks --json` record (real key set, bodies trimmed)."""
    return {"assignee": "default", "block_kind": None, "board": "default", "body": "comment-only fallback",
            "children": list(children), "comments": 0, "completed_at": None, "created_at": created,
            "created_by": "hub:gw:hub", "gateway": "macbook", "human_request": None, "id": cid, "last_comments": [],
            "origin_bindings": [], "owner_profile": "default", "parents": list(parents), "priority": 0,
            "profile": "default", "result": None, "run": run, "sessions": [], "started_at": started,
            "status": status, "title": "Post approved evidence", "_gw": "macbook",
            "_key": f"default/default/{cid}", "_version": 1, "_updated_at": created * 1000 + 2884,
            "ownership_state": "qualified", "gw": "macbook"}


FILTER_OUT = "list\n" + json.dumps([
    _hub_row(OWNER, "ready", CREATED - 1),
    _hub_row(TARGET, "running", 1793800804, started=1793800825,
             run={"ended_at": None, "outcome": None, "profile": "default", "status": "running"}),
], indent=2)
WAKE_OUT = json.dumps({"id": "placement:hub:03d9d3cf5b90ada0028f5e74", "state": "done", "claims": 1,
                       "result": {"output": "{\"id\": \"%s\"}" % OWNER}})


def _turn(coach=None, filter_cmd=FILTER_CMD, now=T_REPLY):
    coach = coach or rules.AgentCoach(None)
    coach.clock = lambda: now
    sid = "s"
    coach.observe(sid, "terminal", {"command": HUB_TASKS_CMD, "timeout": 60},
                  json.dumps({"output": WAKE_OUT, "exit_code": 0, "error": None}))
    coach.observe(sid, "terminal", {"command": filter_cmd, "timeout": 30},
                  json.dumps({"output": FILTER_OUT, "exit_code": 0, "error": None}))
    return [l for l in coach.turn_context(sid)[1] if l.lstrip().startswith("<- OK")]


def test_b_hub_tasks_read_filtered_by_card_id_is_an_owner_read(monkeypatch):
    monkeypatch.setattr(rules.time, "time", lambda: T_REPLY)
    ok = _turn()
    assert len(ok) == 1, ok
    line = ok[0]
    assert f"task={OWNER} status=ready" in line and "gw=macbook" in line, line
    assert "last=created @ 2026-11-06T21:59Z (11m ago)" in line, line
    # the deterministic `unverified` rule and the fresh-ready telemetry read it as a checked, fresh, queued owner
    assert rc.OWNER_CHECK_RE.search(line), line
    assert rc.fresh_ready_owner(REPLY, ok), line


def test_b_filter_of_an_unrelated_file_is_not_an_owner_read(monkeypatch):
    monkeypatch.setattr(rules.time, "time", lambda: T_REPLY)
    assert _turn(filter_cmd=FILTER_CMD.replace("macbook-tasks.json", "notes.json")) == []


def test_b_hub_tasks_with_a_card_id_and_wait_check_are_owner_read_forms():
    assert "hub_tasks" in rules.owner_read_forms(
        "terminal", {"command": f"hermes-hub tasks --gw macbook --json | grep {OWNER}"})
    assert "hub_tasks" in rules.owner_read_forms(
        "terminal", {"command": f"hermes kanban wait-check task {OWNER} --gateway macbook"})
    assert rules.owner_read_forms("terminal", {"command": "hermes-hub tasks --gw macbook --json"}) == []


def test_b_wait_check_not_yet_exit_1_is_still_a_read():
    coach = rules.AgentCoach(None)
    coach.observe("s", "terminal", {"command": f"hermes kanban wait-check task {OWNER} --gateway macbook"},
                  json.dumps({"output": f"{OWNER} ready\n", "exit_code": 1, "error": None}))
    log = coach.turn_context("s")[1]
    ok = [l for l in log if l.lstrip().startswith("<- OK")]
    assert ok and f"task={OWNER}" in ok[0] and "status=ready" in ok[0], log
    assert not any(l.lstrip().startswith("<- ERROR") for l in log), log


# ── (c) both rubrics count a replica BOARD line; the audit says where the owner evidence came from ──────────────
def test_c_rubrics_count_replica_board_lines():
    for rubric in (rc.JUDGE_SYS, rc.CONFIRM_SYS):
        assert "(replica)" in rubric, rubric[:200]


def test_c_judge_prompts_carry_the_replica_line(tmp_path, monkeypatch):
    monkeypatch.setenv("COACH_BOARD_DBS", _local(tmp_path / "kanban.db"))
    monkeypatch.setenv("COACH_SHADOW_DBS", _shadow(tmp_path / "default.db"))
    monkeypatch.setattr(rc.time, "time", lambda: T_REPLY)
    for prompt in (rc.confirm_prompt("[coach nudge]", [], REPLY), rc.judge_prompt("[coach nudge]", [], REPLY)):
        assert f"<- BOARD task={OWNER}" in prompt and "home=macbook (replica)" in prompt, prompt[-1200:]


def _scored(reply, log, board_env):
    rows = []
    verdict = json.dumps({"handoff": 0, "confirm": 0, "unverified": 0, "unwatched": 0, "report_only": 0.9,
                          "evidence": "not yet confirmed"})
    rc.check(reply, "work kanban task t_01a1a9753ef4748e", log, lambda s, p: verdict,
             thresholds={"report_only": 0.7}, confirm=lambda: (0.2, "x"), confirm_floor=0.5,
             audit=rows.append, card_status=lambda c: "ready", only=["report_only"])
    return [r for r in rows if r.get("event") == "report_only_scored"]


def test_c_report_only_scored_logs_owner_source(tmp_path, monkeypatch):
    monkeypatch.setenv("COACH_BOARD_DBS", _local(tmp_path / "kanban.db"))
    monkeypatch.setenv("COACH_SHADOW_DBS", _shadow(tmp_path / "default.db"))
    monkeypatch.setattr(rc.time, "time", lambda: T_REPLY)
    monkeypatch.setattr(rules.time, "time", lambda: T_REPLY)
    rows = _scored(REPLY, [], None)
    assert rows and rows[0].get("owner_source") == "replica", rows
    rows = _scored(REPLY, _turn(), None)
    assert rows and rows[0].get("owner_source") == "ok", rows
    assert rows[0].get("owner_fresh_ready") is True, rows
    monkeypatch.setenv("COACH_SHADOW_DBS", "")
    rows = _scored(REPLY, [], None)
    assert rows and rows[0].get("owner_source") == "none", rows
