"""report_only vs an owner card the reply names but the turn never read (self-heal agent-9ac78980e2, with
agent-6504f6447c, agent-fabde565ec and agent-61f2644a68).

Hub 20261103_040440_1bf3a7 (state.db msg 756426): after a nudge the agent fixed the nudged item and said the live
flip waits on core card t_01a19fd4b6436e29, "That card is running now." True: claimed 10 min earlier, heartbeating,
landed hours later. The turn never read that card (its last read, 30 min earlier, showed ready), so neither judge
saw anything about it and the outcome judge failed the revision (p 0.9). Owner credit came only from the agent's
own reads; the coach could read the board itself but only did so for done/archived owners (closed_owner).

Now, for up to 4 cards the reply names that no `<- OK` line read, the coach reads this host's boards and adds one
`<- BOARD task=... status=... created=... claimed=... run=... last=...` line to both judge prompts. The same
board_card_facts helper fills in `last=` and `waits_on=` for a raw owner read whose output printed no timestamp
(agent-61f2644a68: a status-only sqlite read of a todo card sequenced behind a running parent).
"""
import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[1]


def _mod(name):
    spec = importlib.util.spec_from_file_location(f"t_namedcard_{name}", HERE / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


rules = _mod("agent_rules")
rc = _mod("reply_check")

SCHEMA = """
create table tasks (id text primary key, title text, status text, created_at integer, started_at integer,
                    completed_at integer, current_run_id integer);
create table task_events (id integer primary key autoincrement, task_id text, run_id integer, kind text,
                          payload text, created_at integer);
create table task_runs (id integer primary key autoincrement, task_id text, status text, outcome text,
                        started_at integer, ended_at integer);
create table task_links (parent_id text, child_id text, primary key (parent_id, child_id));
"""


def _board(path, tasks=(), events=(), runs=(), links=()):
    c = sqlite3.connect(path)
    c.executescript(SCHEMA)
    for t in tasks:
        c.execute("insert into tasks (id, status, created_at, started_at, current_run_id) values (?,?,?,?,?)",
                  (t["id"], t["status"], t["created_at"], t.get("started_at"), t.get("run")))
    for task_id, kind, at in events:
        c.execute("insert into task_events (task_id, kind, created_at) values (?,?,?)", (task_id, kind, at))
    for task_id, status, outcome, started in runs:
        c.execute("insert into task_runs (task_id, status, outcome, started_at) values (?,?,?,?)",
                  (task_id, status, outcome, started))
    for parent, child in links:
        c.execute("insert into task_links values (?,?)", (parent, child))
    c.commit()
    c.close()
    return str(path)


def _res(out):
    return json.dumps({"output": out, "exit_code": 0, "error": None})


# ── (a) agent-9ac78980e2: hub msg 756426, as of its timestamp ──────────────────────────────────────────────────
CORE = "t_01a19fd4b6436e29"
T_9AC = 1793688098.0  # 2026-11-03T06:41:38Z
# The revised reply's ending, verbatim (msg 756426; the table above it trimmed).
REPLY_9AC = (
    "I fixed the stray load I'd mentioned. A request to remove the email-digest module from the BB Worker loaded "
    "`babybear-development` and `babybear-worker-deployment` in 1 of 3 runs, when it belongs to "
    "`babybear-ship-and-ops`.\n\n**Fix:** both skills now have a \"not for\" line sending Worker module removal to "
    "`babybear-ship-and-ops`. Pushed to the private state repo as `381e44c4d0`.\n\n"
    "- **On the live version the flip is still there:** \"not for\" lines and example lists only reach the router "
    f"once the core card that shows them (`{CORE}`) lands. That card is running now.\n"
    "- **The other flipping pair**, `babybear-client-ui` vs `codebase-inspection`, came up on one run of a "
    "`babybear-development` request. The right skill still loaded every time, so it's run-to-run noise.\n\n"
    "Nothing waits on you.")
# Its turn's owner lines (rebuilt through AgentCoach.observe from the 19 tool results): one read of a DIFFERENT card.
LOG_9AC = [
    "-> terminal(sqlite3 -readonly ~/.hermes/kanban.db \"select substr(payload,1,1500) from task_events where "
    "task_id='t_01a19fd4b6ffb6e6' and kind='completed)",
    "   <- OK task=t_01a19fd4b6ffb6e6 rows: {\"notice_version\": 1, \"result_len\": 0, \"summary\": \"Narrowed "
    "babybear-ship-and-ops\"}",
    "-> skill_manage({\"operations\": [{\"action\": \"patch\", \"name\": \"babybear-development\"}]})",
    "-> terminal(bash ~/.hermes/scripts/skills_sync.sh 2>&1 | tail -5)",
]


def _board_9ac(tmp_path):
    # Board truth (hub kanban.db): created 05:14:40Z ready, claimed 06:31:22Z (run 29111), heartbeat every minute.
    return _board(tmp_path / "kanban.db",
                  tasks=[{"id": CORE, "status": "running", "created_at": 1793682880, "started_at": 1793687482,
                          "run": 29111}],
                  events=[(CORE, "created", 1793682880), (CORE, "commented", 1793683897),
                          (CORE, "claimed", 1793687482), (CORE, "spawned", 1793687482),
                          (CORE, "heartbeat", 1793688017), (CORE, "heartbeat", 1793688077),
                          (CORE, "heartbeat", 1793688137)],  # after the reply: must not count
                  runs=[(CORE, "running", None, 1793687482)])


def test_a_unread_running_owner_gets_a_board_line(tmp_path):
    db = _board_9ac(tmp_path)
    lines = rc.named_card_facts(REPLY_9AC, LOG_9AC, now=T_9AC, dbs=[db])
    assert len(lines) == 1, lines
    line = lines[0]
    assert line.lstrip().startswith(f"<- BOARD task={CORE} status=running"), line
    assert "claimed=10m" in line and "last=21s(heartbeat)" in line, line  # 06:41:17 heartbeat, 21 s before
    assert "run=running" in line, line


def test_a_both_judge_prompts_carry_the_board_line(tmp_path, monkeypatch):
    monkeypatch.setenv("COACH_BOARD_DBS", _board_9ac(tmp_path))
    board = rc.named_card_facts(REPLY_9AC, LOG_9AC, now=T_9AC)
    assert board, "COACH_BOARD_DBS names the boards the coach reads"
    for prompt in (rc.confirm_prompt("[coach nudge]", LOG_9AC, REPLY_9AC, board=board),
                   rc.judge_prompt("[coach nudge]", LOG_9AC, REPLY_9AC, board=board)):
        assert f"<- BOARD task={CORE} status=running" in prompt, prompt[-1500:]
        assert "COACH'S OWN BOARD READ" in prompt
    # board=None reads the boards itself when the prompt is built (the live path in __init__ and check())
    live = rc.confirm_prompt("[coach nudge]", LOG_9AC, REPLY_9AC)
    assert f"<- BOARD task={CORE} status=" in live, live[-800:]


# ── (b) a card the turn already read gets no second line ────────────────────────────────────────────────────────
def test_b_card_read_this_turn_gets_no_board_line(tmp_path):
    db = _board_9ac(tmp_path)
    log = LOG_9AC + [f"-> kanban_show({{\"task_id\": \"{CORE}\"}})",
                     f"   <- OK task={CORE} status=running run=running last=heartbeat @ 2026-11-03T06:41Z (21s ago)"]
    assert rc.named_card_facts(REPLY_9AC, log, now=T_9AC, dbs=[db]) == []
    listed = LOG_9AC + [f"   <- OK list: {CORE}=running(10m ago) t_01a1a00d9e0fe31e=ready(6m ago)"]
    assert rc.named_card_facts(REPLY_9AC, listed, now=T_9AC, dbs=[db]) == []


# ── (c) fail-open: no board, unknown card, unreadable db ───────────────────────────────────────────────────────
def test_c_missing_db_or_unknown_id_adds_nothing(tmp_path):
    assert rc.named_card_facts(REPLY_9AC, LOG_9AC, now=T_9AC, dbs=[str(tmp_path / "absent.db")]) == []
    assert rc.named_card_facts(REPLY_9AC, LOG_9AC, now=T_9AC, dbs=[]) == []
    empty = _board(tmp_path / "empty.db")
    assert rc.named_card_facts(REPLY_9AC, LOG_9AC, now=T_9AC, dbs=[empty]) == []
    bad = tmp_path / "bad.db"
    bad.write_text("not a database")
    assert rc.named_card_facts(REPLY_9AC, LOG_9AC, now=T_9AC, dbs=[str(bad)]) == []
    assert rules.board_card_facts(CORE, now=T_9AC, dbs=[str(bad)]) is None


# ── (d) a stale ready card shows its age ────────────────────────────────────────────────────────────────────────
def test_d_stale_ready_card_shows_its_age(tmp_path):
    cid = "t_01a1aaaa00000001"
    now = 1_800_000_000
    db = _board(tmp_path / "kanban.db", tasks=[{"id": cid, "status": "ready", "created_at": now - 30 * 3600}],
                events=[(cid, "created", now - 30 * 3600)])
    lines = rc.named_card_facts(f"Card {cid} owns the rest.", [], now=now, dbs=[db])
    assert lines and f"task={cid} status=ready created=30h claimed=never run=none last=30h(created)" in lines[0], lines


# ── (e) both rubrics say how to read a BOARD line ──────────────────────────────────────────────────────────────
def test_e_both_rubrics_score_board_lines():
    for rubric in (rc.JUDGE_SYS, rc.CONFIRM_SYS, rc.REPORT_ONLY_RULE):
        assert "<- BOARD" in rubric, rubric[:200]
        assert "contradicts" in rubric and "waits_on=" in rubric


# ── (f) agent-6504f6447c: hub msg 754781, a fresh ready owner read only through its parent's prose ─────────────
FRESH = "t_01a19ff4a730454c"
T_6504 = 1793687226.0  # 2026-11-03T06:27:06Z
REPLY_6504 = (
    "Already in motion, nothing needed from you:\n"
    "- **t_01a1a00d9e0fe31e** (ready): moves the core fix onto daveey/hermes main and makes it pass the health "
    "checks.\n"
    f"- **{FRESH}** (ready): the original project chat still can't resume the build from inside itself; this card "
    "owns that.\n")
LOG_6504 = [
    "-> kanban_show({\"task_id\": \"t_01a19f6eb64ca98b\"})",
    "   <- OK task=t_01a19f6eb64ca98b status=done run=done last=worker_result @ 2026-11-03T06:20Z (5m ago)",
    "-> kanban_show({\"task_id\": \"t_01a1a00d9e0fe31e\"})",
    "   <- OK task=t_01a1a00d9e0fe31e status=ready last=promoted @ 2026-11-03T06:19Z (6m ago)",
]


def test_f_fresh_ready_owner_named_only_in_prose(tmp_path):
    # Board truth then: created 05:49:33Z as ready, no runs (claimed only at 06:50:34Z, after the reply).
    db = _board(tmp_path / "kanban.db", tasks=[{"id": FRESH, "status": "ready", "created_at": 1793684973}],
                events=[(FRESH, "created", 1793684973), (FRESH, "creator_chats_resolved", 1793684973),
                        (FRESH, "claimed", 1793688634)])
    lines = rc.named_card_facts(REPLY_6504, LOG_6504, now=T_6504, dbs=[db])
    assert len(lines) == 1, lines  # t_01a1a00d9e0fe31e was read this turn
    assert lines[0].lstrip().startswith(f"<- BOARD task={FRESH} status=ready"), lines
    assert "created=37m" in lines[0] and "claimed=never" in lines[0] and "last=37m" in lines[0], lines


# ── agent-fabde565ec: hub msg 738155, a ready check card touched only by a comment ─────────────────────────────
def test_fresh_ready_check_card_read_only_by_comment(tmp_path):
    cid, now = "t_01a19f631679c557", 1793678799.0
    db = _board(tmp_path / "kanban.db", tasks=[{"id": cid, "status": "ready", "created_at": 1793675433}],
                events=[(cid, "created", 1793675433), (cid, "commented", 1793675898), (cid, "commented", 1793678619)])
    reply = (f"- **The check:** card {cid} waits for the install. Then it blocks and unblocks a test chat on the "
             "live hub.")
    log = ["-> kanban_comment({\"body\": \"MERGED: PR #742 landed on main as 12dec1499c0.\"})"]
    lines = rc.named_card_facts(reply, log, now=now, dbs=[db])
    assert lines and f"task={cid} status=ready created=56m claimed=never run=none last=3m(commented)" in lines[0]


# ── scope: quoted ids, at most 4 cards, a status that contradicts the reply ────────────────────────────────────
def test_quoted_ids_skipped_and_capped_at_four(tmp_path):
    now = 1_800_000_000
    ids = [f"t_01a1bbbb0000000{i}" for i in range(6)]
    db = _board(tmp_path / "kanban.db", tasks=[{"id": i, "status": "blocked", "created_at": now - 60} for i in ids],
                events=[(i, "blocked", now - 60) for i in ids])
    reply = f'The old note said "{ids[0]} owns it". ' + " ".join(f"{i} is open." for i in ids[1:])
    lines = rc.named_card_facts(reply, [], now=now, dbs=[db])
    assert [l.split("task=")[1].split()[0] for l in lines] == ids[1:5], lines
    assert all("status=blocked" in l for l in lines)


# ── agent-61f2644a68: a raw status-only owner read gets the card's newest event and its unfinished parents ─────
DEPLOY, FIXCARD = "t_01a194b9de51e620", "t_01a1a0ae16be800c"
T_61F = 1793699993.0  # 2026-11-03T09:59:53Z, state.db row 769050
CMD_61F = ("python3 -c 'import sqlite3,json; c=sqlite3.connect(\"file:/home/ubuntu/.hermes/kanban.db?mode=ro\","
           f"uri=True); t=\"{DEPLOY}\"; print(c.execute(\"select status,current_run_id from tasks where id=?\",(t,))"
           ".fetchone())'")
OUT_61F = "('todo', None)\n[('assistant', None, 'Kept the deployment safety gates intact')]"


def _board_61f(tmp_path, parent_status="running"):
    return _board(tmp_path / "kanban.db",
                  tasks=[{"id": DEPLOY, "status": "todo", "created_at": 1793500000},
                         {"id": FIXCARD, "status": parent_status, "created_at": 1793696900, "run": 32070},
                         {"id": "t_01a1a0b18d2dfb9d", "status": "done", "created_at": 1793697300}],
                  events=[(DEPLOY, "claimed", 1793697020), (DEPLOY, "dependency_wait", 1793697577),
                          (FIXCARD, "heartbeat", 1793699900)],
                  links=[(FIXCARD, DEPLOY), ("t_01a1a0b18d2dfb9d", DEPLOY)])


def test_raw_owner_read_without_timestamp_gets_board_last_and_parents(tmp_path, monkeypatch):
    monkeypatch.setenv("COACH_BOARD_DBS", _board_61f(tmp_path))
    line = rules.owner_result_line("terminal", {"command": CMD_61F}, _res(OUT_61F), now=T_61F)
    assert line and f"task={DEPLOY} status=todo" in line, line
    assert "last=dependency_wait @ 2026-11-03T09:19Z (40m ago)" in line, line
    assert f"waits_on={FIXCARD}:running(1m)" in line, line
    assert "t_01a1a0b18d2dfb9d" not in line, line  # done parents are not waits
    assert len(line) <= rules.OK_LINE_MAX, line


def test_raw_owner_read_without_board_keeps_old_line(tmp_path, monkeypatch):
    monkeypatch.setenv("COACH_BOARD_DBS", str(tmp_path / "absent.db"))
    line = rules.owner_result_line("terminal", {"command": CMD_61F}, _res(OUT_61F), now=T_61F)
    assert line and f"task={DEPLOY} status=todo" in line and "last=" not in line and "waits_on=" not in line, line


def test_blocked_parent_shows_as_blocked(tmp_path):
    db = _board_61f(tmp_path, parent_status="blocked")
    facts = rules.board_card_facts(DEPLOY, now=T_61F, dbs=[db])
    assert facts and facts["waits_on"] == [(FIXCARD, "blocked", 93.0)], facts
