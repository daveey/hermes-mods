"""report_only vs a done notice whose owner is "the fix card", a dependent of the card the notice names
(self-heal agent-93c5dbebae, round 2).

MacBook 2026-11-12 11:49 PT. A done notice for review card t_01a1d1434255a96b (PR #1700) arrived; the reply said
"the fix card wakes at 12:01 PT, lands it through the ship path, then checks the result on the MacBook." True: owner
t_01a1d11934a8d0a9 had checkpointed with kanban_continue and its `continued` event carried wake_at 12:01:34. Round 1
(88e5f5fe) put that wake on the owner's BOARD line, but on the real input the owner never got a BOARD line: the ask
names only the review card, the reply names no id, the turn read the owner with a heredoc python script spelling the
db `'kan' + 'ban.db'` (no `kanban.db` in the call, the id past TURN_LOG_ARG_CAP), and the owner's title never names
#1700. The board already knew the answer: task_links(parent=review card, child=owner).

Now (1) a reply ending that refers to "the card" / names a PR after a notice gets the notice card's open task_links
children too, and (2) a terminal / execute_code call whose argument or result looks like a board read (`ban.db`,
`task_events`, a `t_… ('scheduled', …)` row) counts as one even without the literal `kanban.db`.
fixtures/owner_from_notice_turn.json holds the real ask, draft, revised reply and the turn's three calls with their
results (MacBook state.db msgs 893016-893027; login, repo slug and host name scrubbed).
"""
import importlib.util
import json
import os
import sqlite3
from collections import deque
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
REAL = json.loads((Path(__file__).resolve().parent / "fixtures" / "owner_from_notice_turn.json").read_text())


def _mod(name):
    spec = importlib.util.spec_from_file_location(f"t_ownernotice_{name}", HERE / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


rules = _mod("agent_rules")
rc = _mod("reply_check")

REVIEW = "t_01a1d1434255a96b"
OWNER = "t_01a1d11934a8d0a9"
OTHER = "t_01a1d0ffff000002"
PARKED_AT = 1794512794           # 11:46:34 PT, kanban_continue(resume_after_seconds=900)
WAKE_AT = PARKED_AT + 900        # 12:01:34 PT
T = WAKE_AT - 12 * 60            # the coach scored the reply 12 min before the wake
ASK, REPLY, DRAFT = REAL["ask"], REAL["reply"], REAL["draft"]

SCHEMA = """
create table tasks (id text primary key, title text, body text, status text, created_at integer, started_at integer,
                    completed_at integer, current_run_id integer);
create table task_events (id integer primary key autoincrement, task_id text, run_id integer, kind text,
                          payload text, created_at integer);
create table task_runs (id integer primary key autoincrement, task_id text, status text, outcome text,
                        started_at integer, ended_at integer);
create table task_links (parent_id text, child_id text, primary key (parent_id, child_id));
"""


def _board(tmp_path, owner_status="scheduled", link=True, name="kanban.db"):
    path = tmp_path / name
    c = sqlite3.connect(path)
    c.executescript(SCHEMA)
    c.execute("insert into tasks (id, title, status, created_at, completed_at) values (?,?,?,?,?)",
              (REVIEW, "Independent exact-head review: core PR #1700 @b8e04185dd (remote notice observed while fresh "
                       "is delivered late, not settled stale)", "done", PARKED_AT - 1200, T - 300))
    c.execute("insert into tasks (id, title, body, status, created_at) values (?,?,?,?,?)",
              (OWNER, "Remote done notice starved past 6 h is settled stale and silently dropped: t_01a1c86d86cc1db7 "
                      "never reached chat 20261026_235722_bef6c6", "Deliver a late-observed remote notice.",
               owner_status, PARKED_AT - 3600))
    c.execute("insert into task_events (task_id, kind, created_at) values (?,?,?)", (REVIEW, "completed", T - 300))
    c.execute("insert into task_events (task_id, kind, created_at) values (?,?,?)", (OWNER, "claimed", PARKED_AT - 900))
    c.execute("insert into task_events (task_id, kind, payload, created_at) values (?,?,?,?)",
              (OWNER, "continued", json.dumps({"continuation": 1, "status": "scheduled", "resume_status": "ready",
                                               "wake_at": WAKE_AT}), PARKED_AT))
    if owner_status == "done":
        c.execute("insert into task_events (task_id, kind, created_at) values (?,?,?)", (OWNER, "completed", T - 60))
    c.execute("insert into task_runs (task_id, status, outcome, started_at) values (?,?,?,?)",
              (OWNER, "done" if owner_status == "done" else "continued", "continued", PARKED_AT - 900))
    if link:
        c.execute("insert into task_links (parent_id, child_id) values (?,?)", (REVIEW, OWNER))
    c.commit()
    c.close()
    return str(path)


def _real_log():
    """The turn's log exactly as the coach writes it: each real (tool, args, result) through AgentCoach._log_call."""
    coach = rules.AgentCoach(signals=None)
    s = {"turn_log": deque(maxlen=200), "turn_rich": deque(maxlen=200)}
    for c in REAL["calls"]:
        coach._log_call(s, c["tool"], c["args"], c["result"], None)
    return list(s["turn_log"])


def _capped_log():
    """Only the three `->` lines (capped at TURN_LOG_ARG_CAP): what the turn shows when no call result is kept."""
    return [f"-> {c['tool']}({c['args']['command'][:rules.TURN_LOG_ARG_CAP]})" for c in REAL["calls"]]


def _owner_line(lines):
    return next((l for l in lines if f"task={OWNER}" in l), None)


# ── the real precondition holds in the fixture ─────────────────────────────────────────────────────────────────────
def test_fixture_is_the_real_failing_input():
    assert OWNER not in ASK and OWNER not in REPLY and OWNER not in DRAFT
    assert REVIEW in ASK and "the fix card wakes at 12:01 PT" in REPLY
    cmd = REAL["calls"][1]["args"]["command"]
    assert "kanban.db" not in cmd and "/kan' + 'ban.db" in cmd
    assert cmd.index(OWNER) > rules.TURN_LOG_ARG_CAP
    assert REAL["calls"][1]["result"].startswith("{\"output\": \"t_01a1d11934a8d0a9 ('scheduled', 700, None)")


# ── 1. the real turn: the owner reaches the judges with its timed wake ─────────────────────────────────────────────
def test_real_turn_owner_wake_reaches_the_judges(tmp_path, monkeypatch):
    monkeypatch.setenv("COACH_BOARD_DBS", _board(tmp_path))
    log = _real_log()
    for reply in (REPLY, DRAFT):
        lines = rc.named_card_facts(reply, log, now=T, user_ask=ASK)
        line = _owner_line(lines)
        assert line and "wake=in(12m kind=continue)" in line, (lines, log)


def test_report_only_scored_logs_owner_wake_pending_on_real_turn(tmp_path, monkeypatch):
    monkeypatch.setenv("COACH_BOARD_DBS", _board(tmp_path))
    monkeypatch.setattr(rc.time, "time", lambda: T)
    log = _real_log()
    events = []

    def llm(system, prompt):
        return json.dumps({"report_only": 0.9, "unverified": 0,
                           "evidence": "the fix card wakes at 12:01 PT, lands it through the ship path"})

    rc.check(REPLY, ASK, log, llm, threshold=0.8, only=["report_only"], confirm=lambda: (0.2, "owner wakes at 12:01"),
             confirm_floor=0.5, audit=events.append, thresholds={"report_only": 0.5})
    scored = next(e for e in events if e["event"] == "report_only_scored")
    assert scored["owner_wake_pending"] is True, scored
    assert scored["owner_source"] == "board", scored


# ── 2. each mechanism alone resolves the owner ─────────────────────────────────────────────────────────────────────
def test_notice_card_open_child_resolves_the_owner_without_any_read(tmp_path, monkeypatch):
    monkeypatch.setenv("COACH_BOARD_DBS", _board(tmp_path))
    ids, fallback = rc.tail_card_ids(REPLY, _capped_log(), ASK)
    assert ids[:1] == [REVIEW] and OWNER in ids and fallback, ids
    line = _owner_line(rc.named_card_facts(REPLY, _capped_log(), now=T, user_ask=ASK))
    assert line and "wake=in(12m kind=continue)" in line, line


def test_board_read_by_result_shape_counts_without_kanban_db_literal(tmp_path, monkeypatch):
    monkeypatch.setenv("COACH_BOARD_DBS", _board(tmp_path, link=False))
    log = _real_log()
    assert OWNER in rc.turn_card_ids(log), log
    line = _owner_line(rc.named_card_facts(REPLY, log, now=T, user_ask=ASK))
    assert line and "wake=in(12m kind=continue)" in line, line


def test_card_read_line_marks_a_board_read_by_task_events_or_row_shape():
    c = REAL["calls"][1]
    line = rules.card_read_line(c["args"]["command"], c["result"])
    assert line and OWNER in line and rules.BOARD_READ_MARK in line, line
    # a script naming no db spelling at all, whose result prints a board row
    line = rules.card_read_line("python3 read.py", f"{OWNER} ('scheduled', 700, None)\n")
    assert line and OWNER in line, line
    # a plain id mention with no board shape stays a plain id line (no board marker)
    line = rules.card_read_line("x" * 150 + f" gh pr comment 1 --body 'see {OWNER}'", "ok")
    assert line and "(" not in line, line


# ── 3. negatives ───────────────────────────────────────────────────────────────────────────────────────────────────
def test_done_child_is_not_an_owner(tmp_path, monkeypatch):
    monkeypatch.setenv("COACH_BOARD_DBS", _board(tmp_path, owner_status="done"))
    ids, _ = rc.tail_card_ids(REPLY, _capped_log(), ASK)
    assert OWNER not in ids, ids


def test_no_links_and_no_board_read_adds_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("COACH_BOARD_DBS", _board(tmp_path, link=False))
    ids, _ = rc.tail_card_ids(REPLY, _capped_log(), ASK)
    assert OWNER not in ids, ids


def test_reply_naming_another_card_gets_no_expansion(tmp_path, monkeypatch):
    monkeypatch.setenv("COACH_BOARD_DBS", _board(tmp_path))
    reply = REPLY.replace("the fix card wakes", f"the fix card {OTHER} wakes")
    ids, fallback = rc.tail_card_ids(reply, _capped_log(), ASK)
    assert ids == [OTHER] and not fallback, ids


def test_board_read_failure_is_fail_open(tmp_path, monkeypatch):
    monkeypatch.setenv("COACH_BOARD_DBS", str(tmp_path / "missing.db"))
    ids, _ = rc.tail_card_ids(REPLY, _capped_log(), ASK)
    assert ids[:1] == [REVIEW] and OWNER not in ids, ids


# ── 4. mutant-gap cases (post-merge check t_01a1d6503cfbd016) ──────────────────────────────────────────────────────
def test_card_read_line_marks_a_board_read_by_argument_shape_alone():
    """The argument alone marks a board read when the result shows no board row: a failed call, or a count."""
    pad = "x" * rules.TURN_LOG_ARG_CAP
    heredoc = f"python3 - <<'EOF'\n# {pad}\ndb = H + '/kan' + 'ban.db'\nprint(read(db, '{OWNER}'))\nEOF"
    line = rules.card_read_line(heredoc, None, ok=False)
    assert line and OWNER in line and line.endswith(f"({rules.BOARD_READ_MARK})"), line
    count = f"sqlite3 \"$D\" \"select count(*) from task_events where task_id='{OWNER}'\"  # {pad}"
    count = f"# {pad}\n" + count
    line = rules.card_read_line(count, "3\n")
    assert line and OWNER in line and line.endswith(f"({rules.BOARD_READ_MARK})"), line


def test_open_child_survives_the_cap_when_the_turn_touched_many_cards(tmp_path, monkeypatch):
    """The notice card's open child ranks before the turn-touched guesses, so NAMED_CARD_MAX never drops it."""
    monkeypatch.setenv("COACH_BOARD_DBS", _board(tmp_path))
    others = [f"t_01a1d0ffff0001{i:02x}" for i in range(rc.NAMED_CARD_MAX)]
    log = []
    for cid in others:
        log += [f"-> kanban_show(task_id={cid})", "<- OK {}"]
    assert set(others) <= set(rc.turn_card_ids(log)), rc.turn_card_ids(log)
    ids, fallback = rc.tail_card_ids(REPLY, log, ASK)
    assert len(ids) == rc.NAMED_CARD_MAX and fallback, ids
    assert ids[:2] == [REVIEW, OWNER], ids


def test_per_board_read_failure_skips_that_board_only(tmp_path, monkeypatch):
    """A COACH_BOARD_DBS entry that exists but is not a board (not sqlite / no task_links) is skipped; the real
    board after it still yields the open child, and tail_card_ids does not raise."""
    garbage = tmp_path / "garbage.db"
    garbage.write_bytes(b"not a sqlite database\n" * 64)
    empty = tmp_path / "empty.db"
    c = sqlite3.connect(empty)
    c.execute("create table other (x)")
    c.commit()
    c.close()
    real = _board(tmp_path)
    for bad in (garbage, empty):
        monkeypatch.setenv("COACH_BOARD_DBS", f"{bad}{os.pathsep}{real}")
        assert rc.open_child_card_ids([REVIEW]) == [OWNER], bad
        ids, _ = rc.tail_card_ids(REPLY, _capped_log(), ASK)
        assert ids[:2] == [REVIEW, OWNER], (bad, ids)
