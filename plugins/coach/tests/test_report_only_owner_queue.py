"""report_only vs a hand-off to a card buried deep in the ready queue (self-heal agent-8381864e1e).

Hub @session:default/20261023_040944_37ae77, 2026-11-04 02:40:59Z (state.db msg 819061): the reply left the
state.db-latch fix to "the self-heal card" ("I'll post the result to the self-heal card ... The card's fix is to stop
a single misread ...") without writing its id. The turn's only call on that card, t_01a1a41861cfb447, was a
kanban_comment (msg 819015). The card sat ready at priority 0 behind ~55 other ready cards from 01:07Z until 08:13Z;
a `hermes kanban edit --priority 5` then started it 26 s later. The coach's board read only looked at ids written in
the reply, so neither judge saw the card (p 0.9 at 02:41, p 0.1 at 02:54 for the same hand-off), and nothing showed
the queue depth.

Now: a reply that says "the card" with no id gets the board facts of the cards this turn's tool calls touched; a ready
card's BOARD line carries `prio=<p> ahead=<n> slots=<running>/<max>`; a stall comment on a ready card with
ahead >= max_in_progress is held even inside the 30-minute grace (the queue won't reach it), unless the turn raised
its priority; the nudge and the outcome log the touched card as their item id, so the re-check that fails on another
sentence about the same card is the same item; report_only_scored logs owner_ahead.
"""
import importlib.util
import json
import sqlite3
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]


def _mod(name):
    spec = importlib.util.spec_from_file_location(f"t_oq_{name}", HERE / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


rules = _mod("agent_rules")
rc = _mod("reply_check")
signals = _mod("signals")

SCHEMA = """
create table tasks (id text primary key, title text, status text, priority integer default 0, created_at integer,
                    started_at integer, completed_at integer, current_run_id integer, claim_lock text);
create table task_events (id integer primary key autoincrement, task_id text, run_id integer, kind text,
                          payload text, created_at integer);
create table task_runs (id integer primary key autoincrement, task_id text, status text, outcome text,
                        started_at integer, ended_at integer);
create table task_links (parent_id text, child_id text, primary key (parent_id, child_id));
"""

CARD = "t_01a1a41861cfb447"
T = 1793760059.0  # 2026-11-04T02:40:59Z, the reply (msg 819061)
CREATED = 1793754423  # 01:07:03Z
# The card's real events up to the reply (hub kanban.db task_events): 11 earlier comments, then this turn's.
EARLIER = [1793754763, 1793755016, 1793755609, 1793755864, 1793755947, 1793756204, 1793756359, 1793756814,
           1793757315, 1793757901, 1793759863]
EVENTS = [("created", CREATED)] + [("commented", t) for t in EARLIER] + [("commented", 1793760050)]
# The reply, verbatim (msg 819061).
REPLY = (
    "I'm checking the hub's session database (`state.db`) here myself, one table at a time, instead of waiting for "
    "the queued card. Checking one table holds the database open only briefly, so the whole-file problem from last "
    "time doesn't apply.\n\nThe 12 session and worker tables are all clean, including the sessions table.\n\n"
    "There was only one real detection, at 00:01:06 UTC, while a session was being compacted. After that, each "
    "process latches the error and refuses all session writes until it restarts. So the 00:26\u201301:05 crashes "
    "were that latch, not new damage. Since the gateway restarted at 01:01:57 there have been no new detections.\n\n"
    "The last check, on the large `messages` table and the remaining tables, is running in the background and will "
    "report when it finishes. I'll post the result to the self-heal card:\n\n"
    "- **If it's clean:** the one detection was a false alarm. The card's fix is to stop a single misread from "
    "switching off session storage until the next restart.\n"
    "- **If it finds damage:** the card runs session recovery on `messages` in a quiet window.")
# Its turn's tool log (msgs 818993, 819015).
LOG = [
    "-> terminal({\"background\": true, \"notify\": true, \"command\": \"cd ~/.hermes/cache/scratch; "
    "out=state_tablecheck.txt; for t in state_meta runtime_epoch; do sqlite3 -readonly ~/.hermes/state.db\"})",
    f"-> kanban_comment({{\"body\": \"Supervisor diagnosis, 02:45 UTC, read-only: First latch ...\", "
    f"\"task_id\": \"{CARD}\"}})",
]
ASK = "is the state.db problem fixed?"
NUDGED = "The card fixing the earlier session-database (`state.db`) errors is still waiting to start."
OUTCOME = "The card's fix is to stop a single misread from switching off session storage until the next restart."


def _board(path, created=CREATED, events=EVENTS, ahead=55, prio=0, running=7):
    c = sqlite3.connect(path)
    c.executescript(SCHEMA)
    c.execute("insert into tasks (id, status, priority, created_at) values (?,?,?,?)", (CARD, "ready", prio, created))
    for kind, at in events:
        c.execute("insert into task_events (task_id, kind, created_at) values (?,?,?)", (CARD, kind, at))
    for i in range(ahead):  # older ready p0 cards: each ranks ahead
        c.execute("insert into tasks (id, status, priority, created_at) values (?,?,?,?)",
                  (f"t_aa{i:014x}", "ready", 0, created - 3600 - i))
    for i in range(3):  # not ahead: newer same-priority, older lower-priority, older but claimed
        c.execute("insert into tasks (id, status, priority, created_at) values (?,?,?,?)",
                  (f"t_bb{i:014x}", "ready", 0, created + 60 + i))
    c.execute("insert into tasks (id, status, priority, created_at) values (?,?,?,?)",
              ("t_cc00000000000001", "ready", -1, created - 99999))
    c.execute("insert into tasks (id, status, priority, created_at, claim_lock) values (?,?,?,?,?)",
              ("t_cc00000000000002", "ready", 0, created - 99999, "hub:1"))
    for i in range(running):
        c.execute("insert into tasks (id, status, priority, created_at) values (?,?,?,?)",
                  (f"t_dd{i:014x}", "running", 0, created - 500))
    c.commit()
    c.close()
    return str(path)


def _judge_low(_s, _p):  # the strong judge's 02:54 verdict on the same hand-off: started (p 0.1)
    return json.dumps({"handoff": 0, "confirm": 0, "unverified": 0, "unwatched": 0, "report_only": 0.1,
                       "evidence": "the self-heal card"})


def _check(reply, log, audit=None):
    return rc.check(reply, ASK, log, _judge_low, threshold=0.5, audit=audit, card_status=lambda _c: None, now=T)


def _env(monkeypatch, db, slots=7):
    monkeypatch.setenv("COACH_BOARD_DBS", db)
    monkeypatch.setenv("COACH_KANBAN_MAX_IN_PROGRESS", str(slots))


# ── the incident: "the self-heal card", no id; 55 older p0 cards ahead; this turn only commented ───────────────
def test_incident_board_line_shows_queue_position(tmp_path, monkeypatch):
    db = _board(tmp_path / "kanban.db")
    _env(monkeypatch, db)
    lines = rc.named_card_facts(REPLY, LOG, now=T, dbs=[db])
    assert len(lines) == 1 and f"task={CARD}" in lines[0], lines
    assert "prio=0 ahead=55 slots=7/7" in lines[0], lines[0]
    assert "stall_notes=11" in lines[0], lines[0]


def test_incident_reply_is_held(tmp_path, monkeypatch):
    _env(monkeypatch, _board(tmp_path / "kanban.db"))
    seen = []
    hit = _check(REPLY, LOG, audit=seen.append)
    assert hit and hit["rule"] == "report_only" and hit.get("reason") == "stalled_queue_owner", hit
    assert CARD in hit["nudge"] and "55" in hit["nudge"] and f"hermes kanban edit {CARD} --priority" in hit["nudge"]
    row = next(r for r in seen if r.get("event") == "report_only_scored")
    assert row["owner_card"] == CARD and row["owner_ahead"] == 55, row


# ── a young card (inside the 30-min grace) with its first comment: queue depth decides ─────────────────────────
YOUNG = [("created", int(T - 300)), ("commented", int(T - 9))]
NAMED = REPLY.replace("post the result to the self-heal card", f"post the result to the self-heal card `{CARD}`")


def test_young_card_deep_queue_is_held(tmp_path, monkeypatch):
    _env(monkeypatch, _board(tmp_path / "kanban.db", created=int(T - 300), events=YOUNG, ahead=55))
    seen = []
    hit = _check(NAMED, LOG, audit=seen.append)
    assert hit and hit.get("reason") == "stalled_queue_owner", hit
    assert "55" in hit["nudge"], hit["nudge"]
    row = next(r for r in seen if r.get("event") == "report_only_scored")
    assert row["owner_ahead"] == 55, row


def test_young_card_short_queue_passes(tmp_path, monkeypatch):
    # agent-9de19e15a9's behaviour: a fresh ready card with capacity close by is a started owner
    db = _board(tmp_path / "kanban.db", created=int(T - 300), events=YOUNG, ahead=2)
    _env(monkeypatch, db)
    assert "prio=0 ahead=2 slots=7/7" in rc.named_card_facts(NAMED, LOG, now=T, dbs=[db])[0]
    seen = []
    assert _check(NAMED, LOG, audit=seen.append) is None
    row = next(r for r in seen if r.get("event") == "report_only_scored")
    assert row["owner_ahead"] == 2, row


def test_priority_bump_this_turn_passes(tmp_path, monkeypatch):
    ev = YOUNG + [("reprioritized", int(T - 3))]
    _env(monkeypatch, _board(tmp_path / "kanban.db", created=int(T - 300), events=ev, ahead=55))
    log = LOG + [f"-> terminal(hermes kanban edit {CARD} --priority 5)"]
    seen = []
    assert _check(NAMED, log, audit=seen.append) is None
    assert any("owner_ahead" in r for r in seen if r.get("event") == "report_only_scored"), seen


def test_unknown_slots_fail_open(tmp_path, monkeypatch):
    # no max_in_progress known: show the position, but never hold on queue depth alone
    db = _board(tmp_path / "kanban.db", created=int(T - 300), events=YOUNG, ahead=55)
    monkeypatch.setenv("COACH_BOARD_DBS", db)
    monkeypatch.setenv("COACH_KANBAN_MAX_IN_PROGRESS", "0")
    assert "ahead=55" in rc.named_card_facts(NAMED, LOG, now=T, dbs=[db])[0]
    assert _check(NAMED, LOG) is None


def test_reply_without_card_words_reads_no_touched_card(tmp_path, monkeypatch):
    db = _board(tmp_path / "kanban.db")
    _env(monkeypatch, db)
    assert rc.named_card_facts("All 12 tables are clean; nothing else is pending.", LOG, now=T, dbs=[db]) == []


def test_rubric_names_queue_depth():
    assert "ahead=" in rc.BOARD_OWNER and "slots=" in rc.BOARD_OWNER


# ── the outcome that failed on another sentence about the same card is the same item ─────────────────────────
def test_same_card_outcome_is_same_item():
    nudge_ids = rc.evidence_ids(REPLY.replace("I'll post", NUDGED + " I'll post"), NUDGED, tool_log=LOG)
    out_ids = rc.evidence_ids(REPLY, OUTCOME, tool_log=LOG)
    assert CARD in nudge_ids and CARD in out_ids, (nudge_ids, out_ids)
    assert signals.same_item(OUTCOME, NUDGED, out_ids, nudge_ids)
