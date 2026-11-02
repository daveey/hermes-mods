"""report_only vs an owner the turn checked and found running (self-heal agent-9516de5af8).

A relay said a fix was in progress on a Kanban card. The agent ran `hermes kanban show --json <card>`, saw the card
`running` with fresh heartbeats, and replied that the fix was underway with an active worker. Coach held the reply
as report_only (p 0.9) and the rewrite failed too ("second lookup has no result"): agent_rules.observe logged each
call's arguments and only FAILED results (`<- ERROR`), so the judges saw the failed first lookup and nothing from the
one that worked. The rubrics already exempt "an owner the tool log shows running", but nothing showed it.

observe now adds one `   <- OK ...` line after a successful owner check (task status, latest run, newest event and
its age), the judges' prompts carry it, and the rubrics say how to read it.
"""
import importlib.util
import json
import time
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]


def _mod(name):
    spec = importlib.util.spec_from_file_location(f"t_ownerok_{name}", HERE / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


rules = _mod("agent_rules")
rc = _mod("reply_check")

CARD = "t_0000000000000abc"
NOW = time.time()


def _show(status="running", run_status="running", events=None):
    """`hermes kanban show --json` / kanban_show shape: task, runs (oldest first), events (oldest first)."""
    events = events if events is not None else [
        {"kind": "created", "payload": {"status": "ready"}, "created_at": int(NOW - 3600), "run_id": None},
        {"kind": "claimed", "payload": {"lock": "hub:1"}, "created_at": int(NOW - 900), "run_id": 11},
        {"kind": "heartbeat", "payload": None, "created_at": int(NOW - 120), "run_id": 11},
    ]
    return {"task": {"id": CARD, "title": "fix the collapsed arm", "body": "x" * 4000, "status": status},
            "latest_summary": None, "parents": [], "children": [], "comments": [], "events": events,
            "runs": [{"id": 10, "status": "crashed", "started_at": int(NOW - 7200)},
                     {"id": 11, "status": run_status, "started_at": int(NOW - 900)}]}


ASK = ("[Message from another Hermes session \"Stage B campaign\" (@session:default@hub/20261028_000000_aaaaaa)]\n\n"
       f"Stage B final. The arm collapse fix is in progress on card {CARD} (1 GPU).")
REPLY = (f"The fix is already underway on **`{CARD}`**: I verified its active worker and fresh heartbeats. It owns "
         "root-cause tracing, regression tests and the repair. I'm keeping that owner rather than launching a duplicate.")


def _observe_lookup(coach, sid, result):
    coach.observe(sid, "terminal", {"command": f"hermes kanban show --json {CARD}"}, json.dumps(result))


def _turn(result_first_fails=True, show=None):
    coach = rules.AgentCoach(None)
    sid = "s1"
    if result_first_fails:  # the real turn: the first lookup hit the wrong board
        coach.observe(sid, "terminal", {"command": f"hermes kanban show {CARD}"},
                      json.dumps({"output": f"no such task: {CARD}", "exit_code": 1, "error": None}))
    _observe_lookup(coach, sid, {"output": json.dumps(show or _show(), indent=2), "exit_code": 0, "error": None})
    return coach, sid


def _ok_lines(lines):
    return [l for l in lines if l.lstrip().startswith("<- OK")]


# (a) observe writes the OK line for the real `hermes kanban show --json` shape (terminal) ...
def test_observe_logs_owner_status_after_successful_cli_show():
    coach, sid = _turn()
    _, log = coach.turn_context(sid)
    ok = _ok_lines(log)
    assert len(ok) == 1, log
    line = ok[0]
    assert CARD in line and "status=running" in line and "run=running" in line and "last=heartbeat" in line, line
    assert "ago" in line and len(line) <= 220, line
    assert log.index(line) == len(log) - 1  # right after the call it answers
    assert _ok_lines(coach.turn_rich(sid)) == ok


# ... and for the kanban_show tool and the deferred tool_call bridge.
def test_observe_logs_owner_status_for_kanban_show_tool_and_tool_call():
    coach = rules.AgentCoach(None)
    coach.observe("s", "kanban_show", {"task_id": CARD}, json.dumps({**_show(), "worker_context": "..."}))
    coach.observe("s", "tool_call", {"calls": [{"name": "kanban_show", "arguments": {"task_id": CARD}}]},
                  json.dumps({"results": [{"name": "kanban_show", "result": json.dumps(_show(status="blocked"))}]}))
    ok = _ok_lines(coach.turn_context("s")[1])
    assert len(ok) == 2 and "status=running" in ok[0] and "status=blocked" in ok[1], ok


def test_no_ok_line_for_failed_or_unrelated_calls():
    coach = rules.AgentCoach(None)
    coach.observe("s", "terminal", {"command": f"hermes kanban show {CARD}"},
                  json.dumps({"output": f"no such task: {CARD}", "exit_code": 1, "error": None}))
    coach.observe("s", "terminal", {"command": "ls -la"}, json.dumps({"output": "a b", "exit_code": 0}))
    coach.observe("s", "read_file", {"path": "/x"}, json.dumps({"content": "status: running"}))
    assert _ok_lines(coach.turn_context("s")[1]) == []


def test_ok_line_keeps_error_readers_unchanged():
    """`<- OK` lines never count as a failed call for the `<- ERROR` readers, and are not call lines."""
    coach, sid = _turn()
    _, log = coach.turn_context(sid)
    assert rc._tool_kind(_ok_lines(log)[0]) is None
    created = ["-> kanban_create(title='fix', assignee=default)", "   <- OK status=ready task=t_1"]
    assert rc.restart_deferral("works until the next restart", created) is None
    assert rc.card_closed(["-> kanban_complete({})", "   <- OK status=done"]) is True


# (b) both judges see the line: the second opinion's prompt (turn_rich) and the nudge outcome's judge (turn_log).
def test_confirm_prompt_and_nudge_outcome_show_owner_status():
    coach, sid = _turn()
    ok = _ok_lines(coach.turn_rich(sid))[0].strip()
    assert ok in rc.confirm_prompt(ASK, coach.turn_rich(sid), REPLY)
    seen = []

    def llm(system, prompt):
        seen.append(prompt)
        return json.dumps({"handoff": 0, "confirm": 0, "unverified": 0, "unwatched": 0, "report_only": 0.1,
                           "evidence": "checked owner is running"})
    ask, log = coach.turn_context(sid)
    res = rc.nudge_outcome("report_only", REPLY, ASK, log, llm, threshold=0.7)
    assert seen and ok in seen[-1], seen
    assert res["resolved"] is True


# (c) the rubrics tell the judges how to read the line.
def test_rubrics_name_the_checked_running_owner_case():
    for text in (rc.JUDGE_SYS, rc.CONFIRM_SYS):
        assert "<- OK" in text
        assert "24 h" in text


# (d) a checked owner that is idle (ready, nothing for days) is shown as such, so the rubric's exemption can't apply.
def test_idle_owner_line_shows_status_and_stale_activity():
    old = int(NOW - 3 * 86400)
    show = _show(status="ready", run_status="crashed",
                 events=[{"kind": "created", "payload": None, "created_at": old, "run_id": None}])
    coach, sid = _turn(result_first_fails=False, show=show)
    line = _ok_lines(coach.turn_context(sid)[1])[0]
    assert "status=ready" in line and "run=crashed" in line and "last=created" in line, line
    assert "3d ago" in line, line


def _iso(t):
    return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(t))


def _cross_task(now=None):
    """cross_task action=read shape (state.db msg 608418, trimmed): runs/events nest INSIDE `task`, runs carry
    outcome/summary and no timestamps, events carry an ISO `at`, task times are ISO strings. Ages are relative to
    the call (observe reads the clock itself; a module-load NOW drifts under a long suite)."""
    now = time.time() if now is None else now
    return {"ok": True, "gateway": "hub-ec2", "board": "runners", "ref": f"@task:runners/{CARD}",
            "task": {"id": CARD, "title": "K2 shared_lock rev2", "status": "ready", "assignee": "bb-tuner",
                     "created_at": "2026-10-29T17:49:16.000Z", "started_at": "2026-10-29T17:49:23.000Z",
                     "completed_at": None, "block_kind": "dependency", "body": "x" * 3000,
                     "latest_summary": "Await linked fresh pinned split6 reviewer for PR2612, READY readback verified.",
                     "parents": ["t_01a188c88da122d2"], "children": [], "link_tasks": [],
                     "runs": [{"id": 571, "profile": "bb-tuner", "outcome": "blocked", "summary": "await split5b"},
                              {"id": 576, "profile": "bb-tuner", "outcome": "reclaimed", "summary": "restart"},
                              {"id": 577, "profile": "bb-tuner", "outcome": "blocked", "summary": "await split6"}],
                     "comments": [{"author": "bb-tuner", "at": _iso(now - 3300), "body": "unit5b landed"}],
                     "events": [{"kind": "worker_result", "at": _iso(now - 3360)},
                                {"kind": "commented", "at": _iso(now - 2520)},
                                {"kind": "promoted", "at": _iso(now - 2520)}]}}


# agent-773e2f779b: a cross_task read of a ready-but-active card logged only `status=ready`, so report_only failed a
# true "implementation is underway" reply. The OK line must carry its latest run and newest event like kanban_show's.
def test_cross_task_real_format_shows_run_and_last_event():
    now = 1793569920.0  # 2026-11-01T21:52Z, the reply's time; the newest event was 21:10Z
    real = {**_cross_task(now)}
    real["task"]["events"][-1]["at"] = "2026-11-01T21:10:25.000Z"
    line = rules.owner_result_line("cross_task", {"action": "read", "target": f"@task:runners@hub-ec2/{CARD}"},
                                   json.dumps(real), now=now)
    assert "status=ready" in line and "run=blocked" in line, line
    assert "last=promoted @ 2026-11-01T21:10Z (41m ago)" in line, line


def test_observe_logs_cross_task_activity_direct_and_via_tool_call():
    coach = rules.AgentCoach(None)
    args = {"action": "read", "target": f"@task:runners@hub-ec2/{CARD}"}
    coach.observe("s", "cross_task", args, json.dumps(_cross_task()))
    # tool_call with one call stores the bare result; with a results wrapper the inner result is a JSON string
    coach.observe("s", "tool_call", {"calls": [{"name": "cross_task", "arguments": args}]}, json.dumps(_cross_task()))
    coach.observe("s", "tool_call", {"calls": [{"name": "cross_task", "arguments": args}]},
                  json.dumps({"results": [{"name": "cross_task", "result": json.dumps(_cross_task())}]}))
    ok = _ok_lines(coach.turn_context("s")[1])
    assert len(ok) == 3, ok
    for line in ok:
        assert "status=ready" in line and "run=blocked" in line and "last=promoted" in line and "42m ago" in line, line
        assert len(line) <= 220, line


def test_run_without_status_shows_latest_summary():
    res = _cross_task()
    res["task"]["runs"] = []
    line = rules.owner_result_line("cross_task", {"action": "read"}, json.dumps(res))
    assert "summary=Await linked fresh pinned" in line and "last=promoted" in line, line


def test_event_times_in_epoch_ms_and_iso_are_read():
    assert rules._ts(1793569920000) == 1793569920
    assert rules._ts("2026-11-01T21:10:25.000Z") == 1793567425
    assert rules._ts("2026-11-01T21:10:25+00:00") == rules._ts("2026-11-01T21:10:25Z")
    assert rules._ts("not a time") is None and rules._ts(None) is None


# Detection guard: every owner-read tool has a fixture in its real result format, and each one's OK line shows the
# newest event. Adding a tool to OWNER_READ_TOOLS without a fixture here fails, so a third format can't slip in unread.
OWNER_READ_FIXTURES = {
    "kanban_show": lambda: {**_show(), "worker_context": "..."},
    "cross_task": _cross_task,
}


def test_every_owner_read_tool_has_a_real_format_fixture_showing_last_event():
    assert set(rules.OWNER_READ_TOOLS) <= set(OWNER_READ_FIXTURES), (
        f"add a real-format fixture for {set(rules.OWNER_READ_TOOLS) - set(OWNER_READ_FIXTURES)}")
    for name in rules.OWNER_READ_TOOLS:
        result = json.dumps(OWNER_READ_FIXTURES[name]())
        for tool, args in ((name, {}), ("tool_call", {"calls": [{"name": name, "arguments": {}}]})):
            line = rules.owner_result_line(tool, args, result)
            assert line and "last=" in line and "run=" in line and "run=None" not in line, (tool, name, line)


def test_unparseable_success_falls_back_to_result_head():
    coach = rules.AgentCoach(None)
    coach.observe("s", "terminal", {"command": f"hermes kanban show {CARD}"},
                  json.dumps({"output": f"Task {CARD}: fix\n  status:    running\n  assignee:  default", "exit_code": 0}))
    line = _ok_lines(coach.turn_context("s")[1])[0]
    assert "status:    running" in line or "status=running" in line, line
    assert len(line) <= 220


# ───── self-heal agent-9de19e15a9: a hand-off to a just-filed, healthy owner card that is still queued ─────
# A Kanban worker closed its crash card and named two fix cards it had filed minutes earlier: "queued but not started
# yet". Both rubrics listed ready/todo with blocked/done as "stays high", with no age test, so the judge held a
# hand-off AGENTS.md counts as a started fix (a linked card; an owner is disqualified only after 24 h with no change or
# when it can't start). The fix: ready/todo with activity under 24 h and no failed last run counts as started.
OWNERS = ("t_01a198e5508740cb", "t_01a198e56e285557")
QUEUED_REPLY = (  # the revised reply the outcome judge held at p 0.99 (state.db message 607164), trimmed
    "The two fix cards are still open and queued, but they are far back in the queue and I couldn't move them up.\n\n"
    f"- **Status:** `{OWNERS[0]}` (find what wrote the small config and guard that write) and `{OWNERS[1]}` (stop "
    "counting host-side refusals as card crashes) are both `ready`. They were created about 20 minutes ago, and "
    "neither has started or failed.\n"
    "- **What I did instead:** left a comment on each card explaining why it is stuck and the exact command an "
    "operator chat can run to raise its priority.\n\n"
    "No new card: the work already has open owners, and a duplicate card would be refused.")


def _queued_show(card, age_s=18 * 60, run_status=None, status="ready"):
    now = time.time()  # not module-level NOW: a full suite run takes minutes, which would age the fixture
    ev = [{"kind": "created", "payload": {"status": status}, "created_at": int(now - age_s), "run_id": None}]
    runs = [{"id": 1, "status": run_status, "started_at": int(now - age_s + 60)}] if run_status else []
    return {"task": {"id": card, "title": "fix", "status": status}, "events": ev, "runs": runs}


def _queued_log(**kw):
    coach = rules.AgentCoach(None)
    for card in OWNERS:
        coach.observe("q", "kanban_show", {"task_id": card}, json.dumps(_queued_show(card, **kw)))
    return coach.turn_context("q")[1]


def test_fresh_ready_owner_found_for_the_incident_draft():
    log = _queued_log()
    assert any("status=ready" in l and "18m ago" in l for l in log), log
    assert sorted(rc.fresh_ready_owner(QUEUED_REPLY, log)) == sorted(OWNERS)


def test_stale_crashed_blocked_or_unnamed_owner_is_not_fresh():
    assert rc.fresh_ready_owner(QUEUED_REPLY, _queued_log(age_s=3 * 86400)) == []
    assert rc.fresh_ready_owner(QUEUED_REPLY, _queued_log(run_status="crashed")) == []
    assert rc.fresh_ready_owner(QUEUED_REPLY, _queued_log(status="blocked")) == []
    assert rc.fresh_ready_owner("The fix is owned by t_0000000000000fff.", _queued_log()) == []
    assert rc.fresh_ready_owner(QUEUED_REPLY, []) == []


def test_fresh_owner_parsed_with_summary_and_cross_task_shape():
    """agent_rules writes summary= between status and last= when no run state exists (cross_task results)."""
    line = (f"   <- OK task={OWNERS[0]} status=ready summary=filed by crash card t_01a198dc526bff6e "
            f"last=created @ 2026-11-01T20:55Z (18m ago) | task={OWNERS[1]} status=todo run=timed_out "
            f"last=timed_out @ 2026-11-01T20:56Z (5m ago)")
    assert rc.fresh_ready_owner(QUEUED_REPLY, [line]) == [OWNERS[0]]


def test_rubrics_count_a_fresh_queued_owner_as_started():
    for text in (rc.JUDGE_SYS, rc.CONFIRM_SYS):
        flat = " ".join(text.split())
        # ready/todo is no longer lumped with blocked/done as "stays high" regardless of age
        assert "ready/todo/blocked/done" not in flat, "ready/todo still unconditionally high"
        assert "ready or todo" in flat and "under 24 h" in flat, flat[:200]
        assert "failed or crashed" in flat
        assert "commented the stall on the owner card" in flat


def test_closed_card_nudge_names_the_existing_owner_route():
    for text in (rc.REPORT_ONLY_CARD_CLOSED, rc.CARD_CLOSED_TAIL):
        assert "open owner card already covers it" in text and "kanban_show" in text, text
        assert "don't create a duplicate" in text, text


def _scored(reply, log, confirm_p):
    rows = []
    verdict = json.dumps({"handoff": 0, "confirm": 0, "unverified": 0, "unwatched": 0, "report_only": 0.9,
                          "evidence": "queued but not started yet"})
    rc.check(reply, "work kanban task t_01a198dc526bff6e", log, lambda s, p: verdict,
             thresholds={"report_only": 0.7}, confirm=lambda: (confirm_p, "x"), confirm_floor=0.5,
             audit=rows.append, card_status=lambda c: "ready", only=["report_only"])
    return [r for r in rows if r.get("event") == "report_only_scored"]


def test_report_only_scored_audit_carries_owner_fresh_ready():
    fresh = _scored(QUEUED_REPLY, _queued_log(), 0.2)
    assert fresh and fresh[0].get("owner_fresh_ready") is True, fresh
    stale = _scored(QUEUED_REPLY, _queued_log(age_s=3 * 86400), 0.9)
    assert stale and stale[0].get("owner_fresh_ready") is False, stale
