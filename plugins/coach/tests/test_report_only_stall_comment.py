"""report_only vs a stall comment on a card that never starts (self-heal agent-a0262d55e5).

Hub 20261031_064851_d0f106 (state.db msg 742146, 2026-11-03T04:27:59Z): David asked "what are we waiting for". The
reply said the rollout waits on card t_01a19f3049f432d5, "ready but has not started. I've flagged it as the immediate
blocker." The turn's only action on that card was a kanban_comment (msg 741874). The card had sat ready and unclaimed
for 83 min (promoted 03:04:14Z) and this chat had already left the same stall comment at 03:47Z and 03:51Z. The screen
flagged the reply (0.96); the strong judge scored it 0.1 because both rubrics said "a queued owner waiting on priority
or capacity also counts as started when the turn commented the stall on the owner card", with no limit on repeats or
wait time, and the BOARD line's `last=` counted the comments, so the card looked fresh.

Now the BOARD and `<- OK` lines of a ready/todo card carry `ready_for=<age>` (since it last entered the queue,
comments ignored) and `stall_notes=<n>` (comments left on it since then, before this turn), the stall-comment
exemption holds only for the first comment within 30 min, and a deterministic gate (reason stalled_queue_owner)
holds a reply whose only action on a long-queued or already-commented card it names as the blocker is another comment.
"""
import importlib.util
import json
import sqlite3
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]


def _mod(name):
    spec = importlib.util.spec_from_file_location(f"t_stallc_{name}", HERE / f"{name}.py")
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


def _board(path, status, created, events):
    c = sqlite3.connect(path)
    c.executescript(SCHEMA)
    c.execute("insert into tasks (id, status, created_at) values (?,?,?)", (CARD, status, created))
    for kind, at in events:
        c.execute("insert into task_events (task_id, kind, created_at) values (?,?,?)", (CARD, kind, at))
    c.commit()
    c.close()
    return str(path)


CARD = "t_01a19f3049f432d5"
T = 1793680079.0  # 2026-11-03T04:27:59Z, the reply (msg 742146)
# The card's real events up to the reply (hub kanban.db task_events).
EVENTS = [("created", 1793672104), ("dependency_wait", 1793672104), ("creator_chats_unresolved", 1793672104),
          ("commented", 1793674891), ("commented", 1793675036), ("unlinked", 1793675054),
          ("promoted", 1793675054),  # 03:04:14Z: ready from here
          ("commented", 1793677670), ("commented", 1793677896),  # 03:47Z and 03:51Z: earlier stall comments
          ("commented", 1793680073)]  # 04:27:53Z: this turn's comment (msg 741874)
# The reply, verbatim.
REPLY = (
    "**The remaining blocker is the old Ask Eve sync process.** The two-worker test was attempted, but stopped "
    "because that process still removed the worker-placement field. The mini and pool settings were fully restored "
    "afterward.\n\nThe rollout is specifically waiting on the sync backport/handover card `t_01a19f3049f432d5`, "
    "which is **ready but has not started**. I\u2019ve flagged it as the immediate blocker.\n\nI also removed the "
    "incorrect dependency that kept the separate regression-test task waiting for the entire rollout. **We are not "
    "waiting for another decision from you.**")
# Its turn's tool log, rebuilt through agent_rules.owner_result_line from the turn's 6 tool calls.
LOG = [
    "-> kanban_show({\"task_id\": \"t_01a19a0230c8a574\"})",
    "   <- OK task=t_01a19a0230c8a574 status=todo last=commented @ 2026-11-03T04:27Z (0s ago)",
    "-> kanban_show({\"task_id\": \"t_01a19400313cfbda\"})",
    "-> terminal(python3 -c 'import json;d=json.load(open(\"/home/ubuntu/.hermes/cache/spillover/call_40e2.txt\"))')",
    "-> kanban_unlink({\"parent_id\": \"t_01a19400313cfbda\", \"child_id\": \"t_01a19a0230c8a574\"})",
    "-> kanban_comment({\"task_id\": \"t_01a19f3049f432d5\", \"body\": \"User asks what we are waiting for. Rollout "
    "exact dependency is now THIS ready card; mini attempt )",
]
ASK = "what are we waiting for"


def _judge_low(_s, _p):  # the strong judge's verdict on this reply: started (p 0.1)
    return json.dumps({"handoff": 0, "confirm": 0, "unverified": 0, "unwatched": 0, "report_only": 0.1,
                       "evidence": "flagged it"})


def _check(reply, log, audit=None):
    return rc.check(reply, ASK, log, _judge_low, threshold=0.5, audit=audit,
                    card_status=lambda _c: None, now=T)


# ── the incident: ready 83 min, two earlier stall comments, this turn only commented again ──────────────────────
def test_incident_board_line_shows_queue_age_and_earlier_notes(tmp_path):
    db = _board(tmp_path / "kanban.db", "ready", 1793672104, EVENTS)
    lines = rc.named_card_facts(REPLY, LOG, now=T, dbs=[db])
    assert len(lines) == 1, lines
    assert "ready_for=83m" in lines[0] and "stall_notes=2" in lines[0], lines[0]


def test_incident_reply_is_held(tmp_path, monkeypatch):
    monkeypatch.setenv("COACH_BOARD_DBS", _board(tmp_path / "kanban.db", "ready", 1793672104, EVENTS))
    seen = []
    hit = _check(REPLY, LOG, audit=seen.append)
    assert hit and hit["rule"] == "report_only" and hit.get("reason") == "stalled_queue_owner", hit
    assert CARD in hit["nudge"] and "83m" in hit["nudge"], hit["nudge"]
    assert "priority" in hit["nudge"] and "placement" in hit["nudge"], hit["nudge"]
    row = next(r for r in seen if r.get("event") == "report_only_scored")
    assert row["source"] == "stalled_queue_owner" and row["passing"] is True, row
    assert row["owner_ready_for_s"] == 5025 and row["owner_stall_notes"] == 2, row


# ── a card filed 5 min ago, first stall comment: still a started owner (agent-9de19e15a9) ─────────────────────
def test_fresh_card_first_stall_comment_passes(tmp_path, monkeypatch):
    fresh = [("created", int(T - 300)), ("commented", int(T - 6))]
    db = _board(tmp_path / "kanban.db", "ready", int(T - 300), fresh)
    monkeypatch.setenv("COACH_BOARD_DBS", db)
    lines = rc.named_card_facts(REPLY, LOG, now=T, dbs=[db])
    assert "ready_for=5m" in lines[0] and "stall_notes=0" in lines[0], lines
    seen = []
    assert _check(REPLY, LOG, audit=seen.append) is None
    row = next(r for r in seen if r.get("event") == "report_only_scored")
    assert row["owner_ready_for_s"] == 300 and row["owner_stall_notes"] == 0, row


def test_second_comment_on_a_young_card_is_held(tmp_path, monkeypatch):
    young = [("created", int(T - 900)), ("commented", int(T - 600)), ("commented", int(T - 6))]
    monkeypatch.setenv("COACH_BOARD_DBS", _board(tmp_path / "kanban.db", "ready", int(T - 900), young))
    hit = _check(REPLY, LOG)
    assert hit and hit.get("reason") == "stalled_queue_owner", hit


# ── remedies this turn: a priority bump, a re-placement, or the work done here ─────────────────────────────────
def test_priority_bump_this_turn_passes(tmp_path, monkeypatch):
    bumped = EVENTS + [("reprioritized", int(T - 3))]
    monkeypatch.setenv("COACH_BOARD_DBS", _board(tmp_path / "kanban.db", "ready", 1793672104, bumped))
    log = LOG + [f"-> terminal(hermes kanban edit {CARD} --priority 10)"]
    assert _check(REPLY, log) is None


def test_priority_bump_seen_only_on_the_board_passes(tmp_path, monkeypatch):
    bumped = EVENTS + [("reprioritized", int(T - 3))]
    monkeypatch.setenv("COACH_BOARD_DBS", _board(tmp_path / "kanban.db", "ready", 1793672104, bumped))
    assert _check(REPLY, LOG) is None


def test_replacement_this_turn_passes(tmp_path, monkeypatch):
    monkeypatch.setenv("COACH_BOARD_DBS", _board(tmp_path / "kanban.db", "ready", 1793672104, EVENTS))
    log = LOG + [f"-> terminal(hermes kanban move {CARD} --runs-on linux)"]
    assert _check(REPLY, log) is None


def test_work_done_here_passes(tmp_path, monkeypatch):
    # the chat did the backport itself and completed the card: not a queued owner any more
    done = EVENTS + [("claimed", int(T - 200)), ("completed", int(T - 10))]
    monkeypatch.setenv("COACH_BOARD_DBS", _board(tmp_path / "kanban.db", "done", 1793672104, done))
    reply = ("I did the sync backport here and the handover is done; card `t_01a19f3049f432d5` is complete. "
             "The rollout is running its two-worker test now.")
    assert _check(reply, LOG) is None


def test_no_comment_this_turn_is_left_to_the_judges(tmp_path, monkeypatch):
    # the gate only limits the stall-comment exemption; a bare relay is BOARD_OWNER / unverified_owner territory
    monkeypatch.setenv("COACH_BOARD_DBS", _board(tmp_path / "kanban.db", "ready", 1793672104, EVENTS[:-1]))
    log = [l for l in LOG if "kanban_comment" not in l]
    hit = _check(REPLY, log)
    assert not (hit and hit.get("reason") == "stalled_queue_owner"), hit


# ── the `<- OK` line from a kanban_show read carries the same facts ─────────────────────────────────────────────
def test_ok_line_from_kanban_show_carries_queue_age():
    res = {"task": {"id": CARD, "status": "ready"}, "runs": [],
           "events": [{"kind": k, "created_at": at} for k, at in EVENTS[:-1]]}
    line = rules.owner_result_line("kanban_show", {"task_id": CARD}, json.dumps(res))
    assert line and f"task={CARD} status=ready" in line, line
    # owner_result_line ages against the wall clock; the counts are fixed
    assert "ready_for=" in line and "stall_notes=2" in line, line


# ── the rubrics: the exemption is the first comment only, within 30 min ─────────────────────────────────────────
def test_rubrics_limit_the_stall_comment_exemption():
    for text in (rc.CONFIRM_SYS, rc.JUDGE_SYS):
        assert "ready_for=" in text and "stall_notes=" in text, text[:200]
        assert "30 min" in text
        assert "also counts as started when the turn commented the stall on the owner card (kanban_comment" \
               not in text
    assert "ready_for=" in rc.BOARD_OWNER and "stall_notes=" in rc.BOARD_OWNER
    assert "ready and unclaimed for 30 min" in rc.STALLED_OWNER


# ── the live hook path: pre_verify holds the reply and logs the detection fields ────────────────────────────────
def test_live_pre_verify_holds_a_repeat_stall_comment(tmp_path, monkeypatch):
    import time as _t
    now = int(_t.time())
    ev = [("created", now - 9000), ("promoted", now - 5000), ("commented", now - 2400), ("commented", now - 2100),
          ("commented", now - 5)]
    monkeypatch.setenv("COACH_BOARD_DBS", _board(tmp_path / "kanban.db", "ready", now - 9000, ev))
    for k in ("COACH_REPLY_RULES", "COACH_DRAFTS"):
        monkeypatch.delenv(k, raising=False)
    spec = importlib.util.spec_from_file_location("coach_init_stallc", HERE / "__init__.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    core, rcl = mod._load("core"), mod._load("reply_check")
    monkeypatch.setattr(rcl, "aux_judge", _judge_low)
    monkeypatch.setattr(rcl, "aux_confirm", lambda s, p: json.dumps(
        {"outcome": "started", "agent_fixable": True, "quote": "flagged"}))
    store = core.Store(root=tmp_path / "coach")
    coach, hooks = mod.build(store=store, checkout=None)
    hooks["pre_llm_call"](session_id="sc", user_message=ASK)
    hooks["post_tool_call"](session_id="sc", tool_name="kanban_comment",
                            args={"task_id": CARD, "body": "User asks what we are waiting for."},
                            result=json.dumps({"ok": True, "task_id": CARD, "comment_id": 1}))
    out = hooks["pre_verify"](session_id="sc", attempt=0, final_response=REPLY)
    assert out and out.get("action") == "continue" and CARD in out["message"], out
    logged = [json.loads(l) for l in (store.root / "log.jsonl").read_text().splitlines()]
    row = next(r for r in logged if r["event"] == "report_only_scored")
    assert row["source"] == "stalled_queue_owner" and row["owner_stall_notes"] == 2, row
    assert 4990 <= row["owner_ready_for_s"] <= 5100, row
    nudge = next(r for r in logged if r["event"] == "reply_nudge")
    assert nudge["reason"] == "stalled_queue_owner", nudge
