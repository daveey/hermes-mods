"""report_only vs an owner card parked on a timed wake (self-heal agent-93c5dbebae).

MacBook 2026-11-12 11:49 PT, a done-notice turn about PR #1700. The reply said "the fix card wakes at 12:01 PT, lands
it through the ship path, then checks the result on the MacBook." True: the card had checkpointed at 11:46:34 with
kanban_continue(resume_after_seconds=900), so its `continued` event carried wake_at 12:01:34 PT. The coach's board
read reported only wake_check probes (agent-61b50f34ae), so the owner line said `status=scheduled` with no wait fact,
BOARD_OWNER called it a dead park, and both the draft and its revision failed report_only at p=0.9.

Now a scheduled card's board facts carry its pending timed wake (`wake=in(12m kind=continue)`, or
`wake=overdue(2h ...)` once the dispatcher should long since have woken it), the rubric counts a wake due within 24 h
that the message names as a started owner, and `report_only_scored` logs `owner_wake_pending`.
"""
import importlib.util
import json
import sqlite3
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]


def _mod(name):
    spec = importlib.util.spec_from_file_location(f"t_timedwake_{name}", HERE / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


rules = _mod("agent_rules")
rc = _mod("reply_check")

OWNER = "t_01a1d11934a8d0a9"
PARKED_AT = 1794512794  # 11:46:34 PT, kanban_continue(resume_after_seconds=900)
WAKE_AT = PARKED_AT + 900  # 12:01:34 PT
T = WAKE_AT - 12 * 60  # the coach scored the reply 12 min before the wake

SCHEMA = """
create table tasks (id text primary key, title text, status text, created_at integer, started_at integer,
                    completed_at integer, current_run_id integer);
create table task_events (id integer primary key autoincrement, task_id text, run_id integer, kind text,
                          payload text, created_at integer);
create table task_runs (id integer primary key autoincrement, task_id text, status text, outcome text,
                        started_at integer, ended_at integer);
create table task_links (parent_id text, child_id text, primary key (parent_id, child_id));
"""

# The reply's tail, verbatim from the 11:49 turn.
DRAFT = ("PR #1700 is approved and its CI is green. It isn't merged yet: the fix card wakes at 12:01 PT, lands it "
         "through the ship path, then checks the result on the MacBook.")
ASK = f"\u2714 [default] @default Kanban t_01a1d0ffff000001 done \u2014 review of PR #1700 (owner {OWNER})"


def _board(tmp_path, status="scheduled", kind="continued", wake_at=WAKE_AT, name="kanban.db"):
    path = tmp_path / name
    c = sqlite3.connect(path)
    c.executescript(SCHEMA)
    c.execute("insert into tasks (id, status, created_at) values (?,?,?)", (OWNER, status, PARKED_AT - 3600))
    c.execute("insert into task_events (task_id, kind, created_at) values (?,?,?)", (OWNER, "claimed", PARKED_AT - 900))
    payload = {"continuation": 1}
    if wake_at is not None:
        payload["wake_at"] = wake_at
    c.execute("insert into task_events (task_id, kind, payload, created_at) values (?,?,?,?)",
              (OWNER, kind, json.dumps(payload), PARKED_AT))
    if status == "done":
        c.execute("insert into task_events (task_id, kind, created_at) values (?,?,?)", (OWNER, "completed", T - 60))
    c.execute("insert into task_runs (task_id, status, outcome, started_at) values (?,?,?,?)",
              (OWNER, "done" if status == "done" else "continued", "continued", PARKED_AT - 900))
    c.commit()
    c.close()
    return str(path)


def _line(db, now=T):
    facts = rules.board_card_facts(OWNER, now=now, dbs=[db])
    assert facts, db
    return facts, rules.board_fact_line(facts, now=now)


# ── 1. the board read carries the pending timed wake ───────────────────────────────────────────────────────────────
def test_continue_wake_due_in_12m_is_on_the_board_line(tmp_path):
    facts, line = _line(_board(tmp_path))
    assert facts["wake"] == {"at_s": WAKE_AT, "due_in_s": 720, "kind": "continue"}, facts
    assert "wake=in(12m kind=continue)" in line, line
    assert len(line) <= rules.OK_LINE_MAX, line


def test_clock_park_reads_kind_clock(tmp_path):
    _, line = _line(_board(tmp_path, kind="scheduled"))
    assert "wake=in(12m kind=clock)" in line, line


def test_wake_overdue_by_2h_reads_overdue(tmp_path):
    _, line = _line(_board(tmp_path), now=WAKE_AT + 7200)
    assert "wake=overdue(2h" in line and "wake=in(" not in line, line


def test_wake_just_due_reads_due_and_far_wake_reads_later(tmp_path):
    _, line = _line(_board(tmp_path), now=WAKE_AT + 180)
    assert "wake=due(3m ago kind=continue)" in line, line
    _, line = _line(_board(tmp_path, wake_at=T + 3 * 86400, name="far.db"))
    assert "wake=later(3d kind=continue)" in line, line


def test_scheduled_without_wake_payload_has_no_wake_fact(tmp_path):
    facts, line = _line(_board(tmp_path, wake_at=None))
    assert not facts.get("wake") and "wake=" not in line, line


def test_done_card_has_no_wake_fact(tmp_path):
    facts, line = _line(_board(tmp_path, status="done"))
    assert not facts.get("wake") and "wake=" not in line, line


def test_kanban_show_owner_read_carries_the_wake(tmp_path, monkeypatch):
    monkeypatch.setenv("COACH_BOARD_DBS", _board(tmp_path))
    show = {"task": {"id": OWNER, "status": "scheduled"},
            "runs": [{"id": 1, "status": "continued", "outcome": "continued", "started_at": PARKED_AT - 900}],
            "events": [{"kind": "continued", "created_at": PARKED_AT}]}
    line = rules.owner_result_line("kanban_show", {"task_id": OWNER}, json.dumps(show), now=T)
    assert line and "status=scheduled" in line and "wake=in(12m kind=continue)" in line, line


# ── 2. detection guard: report_only_scored.owner_wake_pending ──────────────────────────────────────────────────────
def _scored(db, monkeypatch, now=T):
    monkeypatch.setenv("COACH_BOARD_DBS", db)
    monkeypatch.setattr(rc.time, "time", lambda: now)
    events = []

    def llm(system, prompt):
        return json.dumps({"report_only": 0.9, "unverified": 0,
                           "evidence": "the fix card wakes at 12:01 PT, lands it through the ship path"})

    rc.check(DRAFT, ASK, ["-> terminal(gh pr view 1700 --json state)"], llm, threshold=0.8,
             only=["report_only"], confirm=lambda: (0.2, "owner wakes at 12:01"), confirm_floor=0.5,
             audit=events.append, thresholds={"report_only": 0.5})
    return next(e for e in events if e["event"] == "report_only_scored")


def test_report_only_scored_logs_owner_wake_pending(tmp_path, monkeypatch):
    assert _scored(_board(tmp_path), monkeypatch)["owner_wake_pending"] is True
    assert _scored(_board(tmp_path, name="late.db"), monkeypatch, now=WAKE_AT + 7200)["owner_wake_pending"] is False
    assert _scored(_board(tmp_path, wake_at=None, name="none.db"), monkeypatch)["owner_wake_pending"] is False
    # the reply says "the fix card"; the done notice it answers names the owner
    assert rc.owner_wake_pending(DRAFT, [f"   <- BOARD task={OWNER} status=scheduled wake=in(12m kind=continue)"],
                                 ASK) == [OWNER]
    assert rc.owner_wake_pending(DRAFT, [f"   <- BOARD task={OWNER} status=scheduled wake=due(3m ago kind=clock)"],
                                 ASK) == [OWNER]
    assert rc.owner_wake_pending(DRAFT, [f"   <- BOARD task={OWNER} status=scheduled wake=overdue(2h kind=clock)"],
                                 ASK) == []
    assert rc.owner_wake_pending("All done.", [f"   <- BOARD task={OWNER} status=scheduled wake=in(12m)"]) == []
    # a wake more than 24 h out is not a pending owner
    assert rc.owner_wake_pending(DRAFT, [f"   <- BOARD task={OWNER} status=scheduled wake=later(3d kind=clock)"],
                                 ASK) == []


def test_the_judges_see_the_wake_on_the_board_line(tmp_path, monkeypatch):
    monkeypatch.setenv("COACH_BOARD_DBS", _board(tmp_path))
    lines = rc.named_card_facts(DRAFT, [], now=T, user_ask=ASK)
    assert lines and "wake=in(12m kind=continue)" in lines[0], lines


# ── 3. the rubric says how to read a wake fact ─────────────────────────────────────────────────────────────────────
def test_rubrics_count_a_pending_timed_wake_as_started():
    for rubric in (rc.BOARD_OWNER, rc.JUDGE_SYS, rc.CONFIRM_SYS):
        assert "wake=in(" in rubric and "wake=overdue(" in rubric, rubric[:200]
