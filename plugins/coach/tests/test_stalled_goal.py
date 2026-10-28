"""stalled_goal: a chat-linked card graph whose delivery card has not moved for 24 h while the graph's other cards keep
spending runs (defect agent-38a817308d: the subscription-balancer rollout card sat in todo for 4 days while 3 watcher
cards ran 306 times).

The fixture is the real graph, copied from the hub's replica of the MacBook board (titles scrubbed): ids, statuses,
links, chat links, status events and run start times are the board's own rows."""
import copy
import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent.parent
FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "stalled_goal_balancer.json").read_text())
SID = FIXTURE["origin_session"]
CUTOFF = FIXTURE["cutoff"]
ROLLOUT = "t_82f0fe15"


def _mod():
    spec = importlib.util.spec_from_file_location("t_stalled_goal", HERE / "stalled_goal.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


SCHEMA = """
create table tasks (id text primary key, title text not null, status text not null, created_at integer not null,
                    block_kind text, human_request text);
create table task_links (parent_id text not null, child_id text not null, primary key (parent_id, child_id));
create table task_chat_links (id integer primary key, task_id text not null, chat_gateway text not null,
                              chat_profile text, chat_session text not null, kind text not null,
                              released integer not null default 0, created_at integer not null);
create table task_events (id integer primary key autoincrement, task_id text not null, run_id integer,
                          kind text not null, payload text, created_at integer not null);
create table task_runs (id integer primary key autoincrement, task_id text not null, status text not null,
                        started_at integer not null, ended_at integer, outcome text);
"""


def write_board(path: Path, fx: dict, shift: int = 0) -> Path:
    """A minimal board db holding the fixture's rows; `shift` moves every timestamp (seconds)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(path)
    c.executescript(SCHEMA)
    for t in fx["tasks"]:
        c.execute("insert into tasks values (?,?,?,?,?,?)",
                  (t["id"], t["title"], t["status"], t["created_at"] + shift, t.get("block_kind"),
                   t.get("human_request")))
    c.executemany("insert into task_links values (?,?)", [tuple(l) for l in fx["links"]])
    for l in fx["chat_links"]:
        c.execute("insert into task_chat_links (task_id, chat_gateway, chat_profile, chat_session, kind, released, "
                  "created_at) values (?,?,?,?,?,?,?)",
                  (l["task_id"], "macbook", "default", l["chat_session"], l["kind"], l["released"], 0))
    c.executemany("insert into task_events (task_id, kind, created_at) values (?,?,?)",
                  [(e[0], e[1], e[2] + shift) for e in fx["events"]])
    c.executemany("insert into task_runs (task_id, status, started_at, ended_at, outcome) values (?,?,?,?,?)",
                  [(r[0], "done", r[1] + shift, (r[2] or r[1]) + shift, r[3]) for r in fx["runs"]])
    c.commit()
    c.close()
    return path


def test_flags_the_real_balancer_graph(tmp_path):
    sg = _mod()
    db = write_board(tmp_path / "kanban.db", FIXTURE)
    stalls = sg.find_stalls([db], now=CUTOFF)
    assert [s["card"] for s in stalls] == [ROLLOUT]
    s = stalls[0]
    assert s["status"] == "todo" and SID in s["sessions"]
    assert s["stalled_hours"] >= 24 * 4  # unchanged since it was created on Oct 23
    assert s["other_runs"] >= 150  # the watcher/incident cards' runs since then
    assert {b["card"] for b in s["busy"]} >= {"t_e3e216d4", "t_7e2ee45c"}
    msg = sg.takeover_message(s)
    assert ROLLOUT in msg and "take over" in msg.lower()


def test_quiet_on_a_graph_that_is_progressing(tmp_path):
    sg = _mod()
    fx = copy.deepcopy(FIXTURE)
    # the rollout card was claimed and ran 3 hours ago: the graph is moving
    fx["events"].append([ROLLOUT, "claimed", CUTOFF - 3 * 3600])
    fx["runs"].append([ROLLOUT, CUTOFF - 3 * 3600, None, None])
    db = write_board(tmp_path / "kanban.db", fx)
    assert sg.find_stalls([db], now=CUTOFF) == []


def test_quiet_when_the_rest_of_the_graph_is_idle(tmp_path):
    sg = _mod()
    fx = copy.deepcopy(FIXTURE)
    fx["runs"] = [r for r in fx["runs"] if r[1] < CUTOFF - 30 * 86400]  # nobody spent runs: a slow graph, not churn
    db = write_board(tmp_path / "kanban.db", fx)
    assert sg.find_stalls([db], now=CUTOFF) == []


def test_scheduled_or_human_blocked_delivery_card_is_an_intended_wait(tmp_path):
    sg = _mod()
    for status, kind in (("scheduled", None), ("blocked", "needs_input"), ("done", None)):
        fx = copy.deepcopy(FIXTURE)
        for t in fx["tasks"]:
            if t["id"] == ROLLOUT:
                t["status"], t["block_kind"] = status, kind
        db = write_board(tmp_path / status / "kanban.db", fx)
        assert sg.find_stalls([db], now=CUTOFF) == [], status


def test_released_chat_link_is_not_a_goal_of_that_chat(tmp_path):
    sg = _mod()
    fx = copy.deepcopy(FIXTURE)
    for l in fx["chat_links"]:
        l["released"] = 1
    db = write_board(tmp_path / "kanban.db", fx)
    assert sg.find_stalls([db], now=CUTOFF) == []


def test_session_filter(tmp_path):
    sg = _mod()
    db = write_board(tmp_path / "kanban.db", FIXTURE)
    assert [s["card"] for s in sg.find_stalls([db], now=CUTOFF, sessions={SID})] == [ROLLOUT]
    assert sg.find_stalls([db], now=CUTOFF, sessions={"20261101_000000_aaaaaa"}) == []


def test_thresholds_come_from_config(tmp_path, monkeypatch):
    sg = _mod()
    db = write_board(tmp_path / "kanban.db", FIXTURE)
    monkeypatch.setenv("COACH_STALLED_GOAL_RUNS", "100000")
    assert sg.find_stalls([db], now=CUTOFF) == []
    monkeypatch.setenv("COACH_STALLED_GOAL_RUNS", "20")
    monkeypatch.setenv("COACH_STALLED_GOAL_HOURS", "200")  # 8+ days: the 4-day stall is under it
    assert sg.find_stalls([db], now=CUTOFF) == []


def _hooks(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("coach_init_sg", HERE / "__init__.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    core = mod._load("core")
    sg = mod._load("stalled_goal")
    comments = []
    monkeypatch.setattr(sg, "post_comment", lambda board, card, text: comments.append((board, card, text)) or True)
    monkeypatch.setattr(sg, "_bg", lambda fn, *a: fn(*a))  # run the comment inline
    store = core.Store(root=tmp_path / "coach")
    coach, hooks = mod.build(store=store, checkout=None)
    return hooks, comments, tmp_path / "coach"


def test_origin_chat_gets_one_takeover_message_and_the_card_one_comment(tmp_path, monkeypatch):
    import time
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    write_board(home / "kanban.db", FIXTURE, shift=int(time.time()) - CUTOFF)
    hooks, comments, root = _hooks(tmp_path, monkeypatch)
    out = hooks["pre_llm_call"](session_id=SID, user_message="what are you doing?", platform="desktop")
    assert out and ROLLOUT in out["context"] and "take over" in out["context"].lower()
    assert comments and comments[0][1] == ROLLOUT and comments[0][0] is None  # default board
    # one incident: the next turns (and a second session on the same card) get no repeat comment
    out2 = hooks["pre_llm_call"](session_id=SID, user_message="why are we waiting?", platform="desktop")
    assert not out2 or ROLLOUT not in (out2.get("context") or "")
    assert len(comments) == 1
    logged = [json.loads(l) for l in (root / "log.jsonl").read_text().splitlines()]
    assert any(r.get("event") == "agent_hint" and r.get("rule") == "stalled_goal" and r.get("card") == ROLLOUT
               for r in logged)


def test_hook_is_quiet_for_an_unrelated_session(tmp_path, monkeypatch):
    import time
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    write_board(home / "kanban.db", FIXTURE, shift=int(time.time()) - CUTOFF)
    hooks, comments, _ = _hooks(tmp_path, monkeypatch)
    out = hooks["pre_llm_call"](session_id="20261101_000000_aaaaaa", user_message="hi", platform="desktop")
    assert not out or "take over" not in (out.get("context") or "").lower()
    assert comments == []


def test_cli_dry_run_reports_without_commenting(tmp_path, capsys):
    sg = _mod()
    db = write_board(tmp_path / "kanban.db", FIXTURE)
    assert sg.main(["--db", str(db), "--now", str(CUTOFF), "--json"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert [r["card"] for r in rows] == [ROLLOUT]
    assert rows[0]["kind"] == "stalled_goal"


ASK = json.dumps({"action": "Choose a host", "why_human": "only the operator can name the machine",
                  "interaction": "reply", "scope": "one build host", "evidence": "no free host is registered"})
NOW = 1_000_000
OLD = 1_000


def _mini(path, tasks, links):
    """A delivery card plus a chat-linked watcher that has spent runs, so the stall threshold is met."""
    fx = {
        "tasks": list(tasks) + [{"id": "t_watch", "title": "Watcher", "status": "running", "created_at": OLD}],
        "links": links,
        "chat_links": [
            {"task_id": "t_deliv", "chat_session": SID, "kind": "origin", "released": 0},
            {"task_id": "t_watch", "chat_session": SID, "kind": "context", "released": 0},
        ],
        "events": [],
        "runs": [["t_watch", OLD + 100 + i, OLD + 200 + i, "done"] for i in range(21)],
    }
    return write_board(path, fx)


def _incidents(sg, db):
    return sg.find_incidents([db], now=NOW, h=1, n=1)


def test_human_wait_frontier_emits_waiting_on_human_not_stalled_goal(tmp_path):
    sg = _mod()
    # open intermediate whose own parent is the human wall: the frontier is the wall, not the intermediate
    db = _mini(tmp_path / "kanban.db", [
        {"id": "t_deliv", "title": "Ship the result", "status": "todo", "created_at": OLD},
        {"id": "t_mid", "title": "Middle step", "status": "todo", "created_at": OLD},
        {"id": "t_human", "title": "Choose a host", "status": "blocked", "created_at": OLD,
         "human_request": ASK},
    ], [["t_human", "t_mid"], ["t_mid", "t_deliv"]])
    assert sg.find_stalls([db], now=NOW, h=1, n=1) == []
    inc = _incidents(sg, db)
    assert [r["kind"] for r in inc] == ["waiting_on_human"]
    assert inc[0]["card"] == "t_deliv"
    assert [w["card"] for w in inc[0]["waiting_on"]] == ["t_human"]
    hint = sg.session_hint([SID], tmp_path / "coach", now=NOW, dbs=[db])
    assert hint is None


def test_blocked_human_request_without_block_kind_is_a_human_wait(tmp_path, capsys):
    sg = _mod()
    db = _mini(tmp_path / "kanban.db", [
        {"id": "t_deliv", "title": "Ship the result", "status": "todo", "created_at": OLD},
        {"id": "t_human", "title": "Choose a host", "status": "blocked", "created_at": OLD, "block_kind": None,
         "human_request": ASK},
    ], [["t_human", "t_deliv"]])
    assert sg.find_stalls([db], now=NOW, h=1, n=1) == []
    assert sg.main(["--db", str(db), "--now", str(NOW)]) == 0
    out = capsys.readouterr().out
    assert "waiting_on_human t_deliv" in out
    assert "t_human" in out and "human_request" in out
    assert "stalled_goal t_deliv" not in out


def test_mixed_frontier_stays_stalled_goal(tmp_path):
    sg = _mod()
    db = _mini(tmp_path / "kanban.db", [
        {"id": "t_deliv", "title": "Ship the result", "status": "todo", "created_at": OLD},
        {"id": "t_human", "title": "Choose a host", "status": "blocked", "created_at": OLD, "block_kind": "needs_input"},
        {"id": "t_agent", "title": "Agent step", "status": "todo", "created_at": OLD},
    ], [["t_human", "t_deliv"], ["t_agent", "t_deliv"]])
    stalls = sg.find_stalls([db], now=NOW, h=1, n=1)
    assert [s["card"] for s in stalls] == ["t_deliv"]
    assert stalls[0]["kind"] == "stalled_goal"
    inc = _incidents(sg, db)
    assert all(r["kind"] != "waiting_on_human" for r in inc)


@pytest.mark.parametrize("status", ["scheduled", "todo", "ready", "running"])
def test_scheduled_agent_parent_stays_stalled_goal(tmp_path, status):
    sg = _mod()
    db = _mini(tmp_path / "kanban.db", [
        {"id": "t_deliv", "title": "Ship the result", "status": "todo", "created_at": OLD},
        # a stale needs_input on an agent-status card must not silence the stall
        {"id": "t_agent", "title": "Agent step", "status": status, "created_at": OLD, "block_kind": "needs_input"},
    ], [["t_agent", "t_deliv"]])
    stalls = sg.find_stalls([db], now=NOW, h=1, n=1)
    assert [s["card"] for s in stalls] == ["t_deliv"]
    assert _incidents(sg, db)[0]["kind"] == "stalled_goal"


def test_delivery_card_blocked_with_only_a_human_request_is_not_a_stall(tmp_path):
    sg = _mod()
    db = _mini(tmp_path / "kanban.db", [
        {"id": "t_deliv", "title": "Ship the result", "status": "blocked", "created_at": OLD,
         "human_request": ASK},
        {"id": "t_parent", "title": "Done parent", "status": "done", "created_at": OLD},
    ], [["t_parent", "t_deliv"]])
    assert sg.find_stalls([db], now=NOW, h=1, n=1) == []
    assert _incidents(sg, db) == []
