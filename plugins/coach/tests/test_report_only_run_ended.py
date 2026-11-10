"""report_only after the worker's run already ended (self-heal agent-689886b70b).

A hub Kanban worker (t_01a194ca90736cea) parked its card with kanban_block(kind="dependency") on another team's card. The
block ended the run. 53 s later report_only nudged its reply ("Still unresolved: MacBook source access and mini's ...
gates"). Nothing the revision could do would pass: every lifecycle call is refused after the run ended, so it reworded
the item ("Remaining prerequisites: ...") and failed again, and self-heal counted the reworded line as a different
item. The card then spent 10 more runs ending on the same items.

Fix: after a successful run-ending park on the worker's OWN card (the `<- OK park={...,"run_ended":true}` line), a
report_only hit sends no in-turn nudge; the finding goes to that card as a comment the next run reads, logged as
`report_only_to_card`. A refused park, a park on another card (an orchestrator), kanban_complete, and non-Kanban
platforms still nudge. signals.item_key ignores a short leading label, so a relabelled item is the same item.
"""
import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[1]


def _mod(name):
    spec = importlib.util.spec_from_file_location(f"t_runend_{name}", HERE / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


rc = _mod("reply_check")
ar = _mod("agent_rules")
sig = _mod("signals")

SELF = "t_01a194ca90736cea"
PARENT = "t_01a19ec521f4df8d"  # the other team's card: archived since, so the dependency park is not a live-parent park
ASK = (f"# Kanban task {SELF}: Run all pending soak checks now and close under David's immediate-check scope\n\n"
       "Assignee: default\nStatus:   running\n\n## Body\nRun every pending soak check now and close the card.")
# the replayed turn's real reply (draft d_21402dd6c3dd4aad)
DRAFT = ("Corrected the report and dependency deadlock:\n\n"
         "- **Counts validated:** 19 selected observations + 2 separate gates = 21 dispositions.\n"
         "- **Gmail:** now waits on its existing decision card; identity, provider and privacy requirements remain "
         "unchanged.\n"
         "- **Partner team:** released for owner disposition; this coordinator now waits in the correct dependency direction.\n"
         "- **Still unresolved:** MacBook source access and mini’s verified quota/native-authorization gates. "
         "Neither is a soak or a passing result.\n\n"
         "Evidence and lifecycle readback are saved in `immediate-soak-closeout.json`, section `run18`. Delivery "
         "remains open; no further soak was scheduled.")
EVIDENCE = ("Still unresolved: MacBook source access and mini’s verified quota/native-authorization gates. Neither "
            "is a soak or a passing result.")
REVISED_EVIDENCE = ("Remaining prerequisites: MacBook source access and mini's verified quota/native-authorization "
                    "gates. Neither is a soak or a passing result.")
# the replayed block's real arguments (msg 924217) and result (msg 924218: no run_ended field in that core)
BLOCK_ARGS = {"kind": "dependency",
              "reason": f"Await existing partner lawful disposition owner {PARENT}, freshly promoted READY via supported "
                        "own-edge unlink. Mini remains real .783>=.75quota/native-policy gate and Mac source "
                        "unavailable with 4 existing requests QUEUED; neither passed or newly waived."}
BLOCK_OK = json.dumps({"ok": True, "task_id": SELF, "run_id": 159672, "status": "todo", "block_kind": "dependency"})
BLOCK_ERR = json.dumps({"ok": False, "error": f"task {SELF} is not running"})


def _board(tmp_path, monkeypatch, status="todo"):
    db = tmp_path / "kanban.db"
    c = sqlite3.connect(db)
    c.executescript("create table tasks(id text primary key, status text, created_at integer);"
                    "create table task_events(id integer primary key autoincrement, task_id text, kind text, "
                    "payload text, created_at integer);")
    c.execute("insert into tasks values(?,?,?)", (SELF, status, 1793800000))
    c.execute("insert into tasks values(?,?,?)", (PARENT, "archived", 1793800000))
    c.commit()
    c.close()
    monkeypatch.setenv("COACH_BOARD_DBS", str(db))
    monkeypatch.setenv("HERMES_KANBAN_TASK", SELF)
    return db


def _hooks(tmp_path, monkeypatch):
    monkeypatch.delenv("COACH_REPLY_RULES", raising=False)
    spec = importlib.util.spec_from_file_location("coach_init_runend", HERE / "__init__.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    core = mod._load("core")
    rcl = mod._load("reply_check")
    monkeypatch.setattr(rcl, "aux_judge", lambda s, p: json.dumps(
        {"handoff": 0, "confirm": 0, "unverified": 0, "unwatched": 0, "report_only": 0.9, "evidence": EVIDENCE}))
    monkeypatch.setattr(rcl, "aux_confirm", lambda s, p: json.dumps(
        {"outcome": "report_only", "agent_fixable": True, "quote": "Still unresolved"}))
    posted = []
    monkeypatch.setattr(rcl, "post_card_comment",
                        lambda card, body, db=None: posted.append((card, body, db)) or 1)
    store = core.Store(root=tmp_path / "coach")
    _, hooks = mod.build(store=store, checkout=None)
    return hooks, tmp_path / "coach" / "log.jsonl", posted


def _run(hooks, sid, calls, platform="kanban"):
    hooks["pre_llm_call"](session_id=sid, user_message=ASK, platform=platform)
    for tool, args, res in calls:
        hooks["post_tool_call"](tool_name=tool, session_id=sid, args=args, result=res)
    return hooks["pre_verify"](session_id=sid, platform=platform, attempt=0, final_response=DRAFT)


def _events(logf):
    return [json.loads(l) for l in logf.read_text().splitlines()] if logf.exists() else []


def _deferred(name, args):
    return ("tool_call", {"calls": [{"name": name, "arguments": args}]})


# (a) the replayed incident turn: no in-turn nudge, one comment on the worker's own (still open) card
def test_incident_dependency_park_sends_the_finding_to_the_card_not_a_nudge(tmp_path, monkeypatch):
    db = _board(tmp_path, monkeypatch)
    hooks, logf, posted = _hooks(tmp_path, monkeypatch)
    assert _run(hooks, "w1", [("kanban_block", BLOCK_ARGS, BLOCK_OK)]) is None
    ev = _events(logf)
    assert not [r for r in ev if r["event"] == "reply_nudge"], ev
    to_card = [r for r in ev if r["event"] == "report_only_to_card"]
    assert len(to_card) == 1, ev
    assert to_card[0]["card"] == SELF and to_card[0]["posted"] is True
    assert to_card[0]["run_end"] == "kanban_block:dependency"
    assert len(posted) == 1, posted
    card, body, where = posted[0]
    assert card == SELF and where == str(db)
    assert "coach report_only" in body and "MacBook source access" in body
    assert "live owner card" in body


# (b) a refused block ended nothing: the worker can still act, so it is nudged as before
def test_refused_block_still_nudges(tmp_path, monkeypatch):
    _board(tmp_path, monkeypatch)
    hooks, logf, posted = _hooks(tmp_path, monkeypatch)
    got = _run(hooks, "w2", [("kanban_block", BLOCK_ARGS, BLOCK_ERR)])
    assert got and got.get("action") == "continue"
    ev = _events(logf)
    assert [r for r in ev if r["event"] == "reply_nudge"]
    assert not [r for r in ev if r["event"] == "report_only_to_card"] and not posted


# (c) the same park through the deferred tool_call wrapper
def test_deferred_park_through_tool_call_goes_to_the_card(tmp_path, monkeypatch):
    _board(tmp_path, monkeypatch)
    hooks, logf, posted = _hooks(tmp_path, monkeypatch)
    assert _run(hooks, "w3", [_deferred("kanban_block", BLOCK_ARGS) + (BLOCK_OK,)]) is None
    ev = _events(logf)
    assert not [r for r in ev if r["event"] == "reply_nudge"], ev
    assert len([r for r in ev if r["event"] == "report_only_to_card"]) == 1 and len(posted) == 1


def test_park_line_records_run_ended_and_target_card():
    line = ar.park_result_line("kanban_block", BLOCK_ARGS, result=BLOCK_OK)
    facts = json.loads(line.split("park=", 1)[1])
    assert facts["run_ended"] is True and "task" not in facts
    other = ar.park_result_line("kanban_block", {**BLOCK_ARGS, "task_id": "t_0000000000000abc"}, result=BLOCK_OK)
    assert json.loads(other.split("park=", 1)[1])["task"] == "t_0000000000000abc"
    for tool in ar.PARK_TOOLS:
        assert json.loads(ar.park_result_line(tool, {}).split("park=", 1)[1])["run_ended"] is True, tool


def test_run_ended_reads_own_card_parks_only():
    def log(args, res=BLOCK_OK):
        coach = ar.AgentCoach()
        coach.observe("s", "kanban_block", args, res)
        return list(coach.sessions["s"]["turn_log"])
    assert rc.own_run_ended(log(BLOCK_ARGS), SELF) == "kanban_block:dependency"
    assert rc.own_run_ended(log({**BLOCK_ARGS, "task_id": SELF}), SELF) == "kanban_block:dependency"
    # an orchestrator blocking another card ended that card's run, not its own
    assert rc.own_run_ended(log({**BLOCK_ARGS, "task_id": "t_0000000000000abc"}), SELF) is None
    assert rc.own_run_ended(log(BLOCK_ARGS, BLOCK_ERR), SELF) is None
    assert rc.own_run_ended(log(BLOCK_ARGS), "") is None  # not a Kanban worker


def test_orchestrator_park_on_another_card_still_nudges(tmp_path, monkeypatch):
    _board(tmp_path, monkeypatch)
    hooks, logf, posted = _hooks(tmp_path, monkeypatch)
    got = _run(hooks, "w4", [("kanban_block", {**BLOCK_ARGS, "task_id": "t_0000000000000abc"}, BLOCK_OK)])
    assert got and got.get("action") == "continue" and not posted


def test_kanban_complete_still_nudges(tmp_path, monkeypatch):
    _board(tmp_path, monkeypatch)
    hooks, logf, posted = _hooks(tmp_path, monkeypatch)
    got = _run(hooks, "w5", [("kanban_complete", {"summary": "done"}, json.dumps({"ok": True, "run_ended": True}))])
    assert got and got.get("action") == "continue" and not posted


def test_closed_or_unknown_card_gets_no_comment_and_no_nudge(tmp_path, monkeypatch):
    """The run is over either way, so no nudge; a done card (or one no board here has) gets no comment, logged."""
    _board(tmp_path, monkeypatch, status="done")
    hooks, logf, posted = _hooks(tmp_path, monkeypatch)
    assert _run(hooks, "w6", [("kanban_block", BLOCK_ARGS, BLOCK_OK)]) is None
    to_card = [r for r in _events(logf) if r["event"] == "report_only_to_card"]
    assert to_card and to_card[0]["posted"] is False and to_card[0]["why"] == "card_done" and not posted


# (d) a relabelled item is the same item; different items stay different
def test_item_key_ignores_a_short_leading_label():
    assert sig.item_key(EVIDENCE) == sig.item_key(REVISED_EVIDENCE)
    assert sig.item_key("Still unresolved: A is missing") != sig.item_key("Still unresolved: B is missing")
    assert sig.item_key("**Still unresolved:** X y z") == sig.item_key("Open: X y z")
    # a long lead-in is content, not a label
    assert sig.item_key("The deploy of the new hub image failed: X") != sig.item_key("X")


# (e) every run-ending Kanban tool is classified: its report_only finding goes to the card, or it stays nudged
RUN_ENDING_TOOLS = ("kanban_block", "kanban_schedule", "kanban_continue", "kanban_complete", "kanban_move",
                    "kanban_request_review", "kanban_request_changes")


def test_every_run_ending_tool_routes_to_card_or_is_deliberately_judged():
    to_card = set(rc.RUN_ENDED_TO_CARD)
    judged = dict(rc.JUDGED_AFTER_RUN_END)
    assert not to_card & set(judged)
    missing = [t for t in RUN_ENDING_TOOLS if t not in to_card and t not in judged]
    assert not missing, f"classify in reply_check.RUN_ENDED_TO_CARD or JUDGED_AFTER_RUN_END: {missing}"
    assert set(ar.PARK_TOOLS) <= to_card


def test_post_card_comment_writes_through_core(tmp_path, monkeypatch):
    """The real writer: core's kanban_db.add_comment on the board the card was found on."""
    kb = pytest.importorskip("hermes_cli.kanban_db")
    kc = pytest.importorskip("hermes_cli.kanban_db_connect")
    db = tmp_path / "kanban.db"
    conn = kc.connect(db_path=db)
    try:
        tid = kb.create_task(conn, title="probe", assignee="default")
    finally:
        conn.close()
    cid = rc.post_card_comment(tid, "coach report_only, last run's reply: X", db=str(db))
    assert cid
    conn = kc.connect(db_path=db)
    try:
        bodies = [c.body for c in kb.list_comments(conn, tid)]
    finally:
        conn.close()
    assert any("coach report_only" in b for b in bodies), bodies
