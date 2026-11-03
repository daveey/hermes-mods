import importlib.util
import json
import sys
from pathlib import Path

import pytest

P = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("skill_outcomes_plugin", P / "__init__.py")
plug = importlib.util.module_from_spec(spec)
spec.loader.exec_module(plug)
sys.modules.setdefault("skill_outcomes_plugin", plug)
core = plug.core

SKILL_DONE = "# Five whys\n\n1. Look first.\n\n## Done means\n\n- [ ] Each why cites evidence?\n"
SKILL_PLAIN = "# Other\n\nNo checks here.\n"


def view(name, content, success=True):
    return json.dumps({"success": success, "name": name, "content": content})


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    h = tmp_path / "h"
    h.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(h))
    plug._loads.clear()
    plug._live_turn.clear()
    plug._last_sweep[0] = 0.0
    monkeypatch.setattr(plug, "SWEEP_EVERY_S", 10 ** 9)  # tests flush explicitly
    plug._last_sweep[0] = 10 ** 12
    return h


def lines(h):
    p = h / "logs" / "skill-outcomes.jsonl"
    return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []


def turn(sid, reply, *skills):
    for name, content in skills:
        plug.post_tool_call(tool_name="skill_view", args={"name": name}, result=view(name, content), session_id=sid)
    plug.post_llm_call(session_id=sid, assistant_response=reply, turn_id="t1")


# ---------------------------------------------------------------- skill / session / host
def test_skill_view_records_one_use_per_turn_and_skill(home):
    sid = "s1"
    plug.post_tool_call(tool_name="skill_view", args={"name": "five-whys"}, result=view("five-whys", SKILL_DONE),
                        session_id=sid)
    plug.post_tool_call(tool_name="skill_view", args={"name": "five-whys"}, result=view("five-whys", SKILL_DONE),
                        session_id=sid)
    # reference reads, failed views and other tools are not uses
    plug.post_tool_call(tool_name="skill_view", args={"name": "x", "file_path": "references/a.md"},
                        result=view("x", "ref"), session_id=sid)
    plug.post_tool_call(tool_name="skill_view", args={"name": "y"}, result=view("y", "", success=False), session_id=sid)
    plug.post_tool_call(tool_name="terminal", args={"command": "ls"}, result="{}", session_id=sid)
    plug.post_llm_call(session_id=sid, assistant_response="done", turn_id="t1")
    core.flush(home, force=True)
    got = lines(home)
    assert [r["skill"] for r in got] == ["five-whys"]
    r = got[0]
    assert r["session"] == "s1" and r["host"] == core.host_name() and isinstance(r["ts"], float)
    assert set(r) >= {"ts", "host", "session", "skill", "checks_first_pass", "fix_rounds", "user_correction"}


# ---------------------------------------------------------------- the tick-off ask
def test_transform_asks_for_tickoff_only_for_done_means_skills():
    out = plug.transform_tool_result(tool_name="skill_view", args={"name": "five-whys"},
                                     result=view("five-whys", SKILL_DONE), session_id="s")
    assert core.MARK in out and "Done means (five-whys):" in out
    assert plug.transform_tool_result(tool_name="skill_view", args={"name": "o"}, result=view("o", SKILL_PLAIN)) is None
    assert plug.transform_tool_result(tool_name="skill_view", args={"name": "five-whys", "file_path": "r.md"},
                                      result=view("five-whys", SKILL_DONE)) is None
    assert plug.transform_tool_result(tool_name="skill_view", args={"name": "five-whys"}, result=out) is None
    # post_tool_call still parses a result the transform already extended
    plug.post_tool_call(tool_name="skill_view", args={"name": "five-whys"}, result=out, session_id="s9")
    assert plug._loads["s9"]["five-whys"]["has_done_means"] is True


# ---------------------------------------------------------------- checks_first_pass / fix_rounds
def test_tickoff_fills_checks_and_fix_rounds(home):
    turn("s1", "Report.\n\nDone means (five-whys): 4/5 first pass, 1 fix round; still failing: none",
         ("five-whys", SKILL_DONE))
    turn("s2", "Done means (`five-whys`): 3/3 first pass, 0 fix rounds", ("five-whys", SKILL_DONE))
    core.flush(home, force=True)
    a, b = lines(home)
    assert (a["checks_first_pass"], a["fix_rounds"], a["checks"], a["has_done_means"]) == (False, 1, "4/5", True)
    assert (b["checks_first_pass"], b["fix_rounds"], b["checks"]) == (True, 0, "3/3")
    # SKILL_DONE lists 1 check, so totals of 5 and 3 are mismatches
    assert a["tickoff_mismatch"] is True and b["tickoff_mismatch"] is True


def test_tickoff_total_matching_skill_list_is_not_a_mismatch(home):
    turn("s1", "Done means (five-whys): 1/1 first pass, 0 fix rounds", ("five-whys", SKILL_DONE))
    core.flush(home, force=True)
    (a,) = lines(home)
    assert a["tickoff_mismatch"] is False and a["checks_first_pass"] is True


def test_no_tickoff_or_no_done_means_leaves_checks_null(home):
    turn("s1", "All done, no tick-off line.", ("five-whys", SKILL_DONE), ("other", SKILL_PLAIN))
    core.flush(home, force=True)
    by = {r["skill"]: r for r in lines(home)}
    assert by["five-whys"]["checks_first_pass"] is None and by["five-whys"]["fix_rounds"] is None
    assert by["other"]["checks_first_pass"] is None and by["other"]["has_done_means"] is False


# ---------------------------------------------------------------- user_correction
def test_correction_by_coach_keyword_regex(home):
    turn("s1", "Done means (five-whys): 5/5 first pass, 0 fix rounds", ("five-whys", SKILL_DONE))
    t = core.time.time()
    plug.pre_llm_call(session_id="s1", user_message="no, I told you to use the hub not the mac")
    assert core.flush(home, now=t + 60) == []  # still inside the grace window
    core.flush(home, now=t + core.GRACE_S + 1)
    (r,) = lines(home)
    assert r["user_correction"] is True and r["correction_source"] == "keyword"


def test_correction_by_coach_judge_event(home):
    turn("s1", "fixed", ("five-whys", SKILL_DONE))
    t = core.time.time()
    msg = "the sidebar session still shows red after your change"
    plug.pre_llm_call(session_id="s1", user_message=msg)
    (home / "coach").mkdir()
    (home / "coach" / "log.jsonl").write_text("\n".join(json.dumps(e) for e in [
        {"ts": t + 30, "event": "selfheal_detect", "source": "complaint", "session_id": "other", "evidence": msg},
        {"ts": t + 40, "event": "reply_checked", "session_id": "s1"},
        {"ts": t + 9000, "event": "selfheal_detect", "source": "backstop", "session_id": "s1",
         "evidence": "session still shows red after your change"},
    ]) + "\n")
    assert not core.keyword_correction(msg)
    core.flush(home, now=t + core.GRACE_S + 1)
    (r,) = lines(home)
    assert r["user_correction"] is True and r["correction_source"] == "coach"


def test_plain_next_message_is_not_a_correction(home):
    turn("s1", "done", ("five-whys", SKILL_DONE))
    t = core.time.time()
    plug.pre_llm_call(session_id="s1", user_message="great, now also update the README")
    core.flush(home, now=t + core.GRACE_S + 1)
    (r,) = lines(home)
    assert r["user_correction"] is False and r["correction_source"] == "next_message"


def test_no_next_message_finalizes_false_after_idle(home):
    turn("s1", "done", ("five-whys", SKILL_DONE))
    t = core.time.time()
    # synthetic turns (kanban notices, runtime notes) are not the user's reply
    plug.pre_llm_call(session_id="s1", user_message="[kanban] card t_1 finished")
    plug.pre_llm_call(session_id="s1", user_message="# Kanban task t_01: do X")
    assert core.flush(home, now=t + core.IDLE_S - 60) == []
    core.flush(home, now=t + core.IDLE_S + 1)
    (r,) = lines(home)
    assert r["user_correction"] is False and r["correction_source"] == "none"


# ---------------------------------------------------------------- CLI + rollup
def test_cli_flush_and_rollup(home, capsys):
    skill5 = SKILL_DONE + "".join(f"- [ ] Check {i}?\n" for i in range(2, 6))  # a 5-item list
    turn("s1", "Done means (five-whys): 4/5 first pass, 2 fix rounds", ("five-whys", skill5))
    turn("s2", "Done means (five-whys): 5/5 first pass, 0 fix rounds", ("five-whys", skill5))
    turn("s3", "nothing", ("other", SKILL_PLAIN))
    t = core.time.time()
    plug.pre_llm_call(session_id="s2", user_message="that's wrong, redo it")
    assert core.main(["flush", "--home", str(home), "--now", str(t + max(core.IDLE_S, core.GRACE_S) + 1)]) == 0
    assert "flushed 3" in capsys.readouterr().out
    rows = core.read_lines(core.home_logs(home) * 2)  # duplicates across sources are dropped
    assert len(rows) == 3
    tab = {d["skill"]: d for d in core.rollup(rows, t - 100, t + 100)}
    # correction rate is over uses a real reply closed: s1 got none, s2 got "that's wrong, redo it"
    assert tab["five-whys"] == {"skill": "five-whys", "uses": 2, "ticked": 2, "replied": 1, "first_pass_rate": 0.5,
                                "mean_fix_rounds": 1.0, "correction_rate": 1.0}
    assert tab["other"]["ticked"] == 0 and tab["other"]["first_pass_rate"] is None
    assert tab["other"]["replied"] == 0 and tab["other"]["correction_rate"] is None
    md = "\n".join(core.rollup_lines(rows, t - 100, t + 100))
    assert "| five-whys | 2 | 2 | 50% | 1.00 | 1 | 100% |" in md
    assert "No skill uses" in "\n".join(core.rollup_lines([], 0, 1))
    # a mismatched tick-off is excluded and counted (here forced on the 5/5 row)
    assert not any(r.get("tickoff_mismatch") for r in rows)
    for r in rows:
        r["tickoff_mismatch"] = r["skill"] == "five-whys" and r["checks"] == "5/5"
    assert core.rollup(rows, t - 100, t + 100)[0]["ticked"] == 1
    assert "1 tick-off(s) gave a total" in "\n".join(core.rollup_lines(rows, t - 100, t + 100))


# ---------------------------------------------------------------- pre_final_gate tick-off hold
def test_final_gate_holds_once_until_tickoff_present(home):
    plug.post_tool_call(tool_name="skill_view", args={"name": "five-whys"}, result=view("five-whys", SKILL_DONE),
                        session_id="s1")
    plug.post_tool_call(tool_name="skill_view", args={"name": "other"}, result=view("other", SKILL_PLAIN),
                        session_id="s1")
    held = plug.pre_final_gate(session_id="s1", final_response="Root cause found.", attempt=0)
    assert held["hold"] is True and "Done means (five-whys)" in held["nudge"] and "other" not in held["nudge"]
    # the re-send is never held again, with or without the line
    assert plug.pre_final_gate(session_id="s1", final_response="Root cause found.", attempt=1) is None
    ok = "Root cause found.\n\nDone means (five-whys): 1/1 first pass, 0 fix rounds"
    assert plug.pre_final_gate(session_id="s1", final_response=ok, attempt=0) is None
    # a turn with no Done-means skill, or another session, is not held
    assert plug.pre_final_gate(session_id="s2", final_response="hi", attempt=0) is None
    plug.post_llm_call(session_id="s1", assistant_response=ok, turn_id="t1")
    assert plug.pre_final_gate(session_id="s1", final_response="next turn", attempt=0) is None
    assert plug.pre_final_gate.needs_buffer is True


# ---------------------------------------------------------------- correction rules v2 (review round 1)
def test_machine_notices_do_not_close_the_window(home):
    turn("s1", "done", ("five-whys", SKILL_DONE))
    t = core.time.time()
    for notice in ("✔ [default] @default Kanban t_01a1a003734a7026 done — fixture: no-op",
                   "⏸ [default] @default Kanban t_01 blocked — needs input",
                   "[ASYNC DELEGATION BATCH COMPLETE — deleg_f78a18fe] 2 tasks finished",
                   "[CONTEXT COMPACTION — REFERENCE ONLY] Earlier turns were compacted",
                   "Canonical user decision. Act only on the frozen revision below."):
        assert not core.is_real_user_message(notice), notice
        plug.pre_llm_call(session_id="s1", user_message=notice)
    human = "no, workers should scale up automatically, not need the mbp"
    plug.pre_llm_call(session_id="s1", user_message=human)
    core.flush(home, now=t + core.GRACE_S + 1)
    (r,) = lines(home)
    assert r["user_correction"] is True and r["correction_source"] == "keyword" and r["rules"] == core.RULES


def test_coach_event_on_the_turn_starting_message_is_not_a_correction(home):
    start = "why would you wait on me for this? just do it"
    t = core.time.time()
    plug.pre_llm_call(session_id="s1", user_message=start)  # starts the turn; no open record yet
    turn("s1", "done", ("five-whys", SKILL_DONE))
    plug.pre_llm_call(session_id="s1", user_message="make the change")
    (home / "coach").mkdir()
    (home / "coach" / "log.jsonl").write_text(json.dumps(
        {"ts": t + 120, "event": "selfheal_detect", "source": "backstop", "session_id": "s1",
         "evidence": start}) + "\n")
    core.flush(home, now=t + core.GRACE_S + 10)
    (r,) = lines(home)
    assert r["user_correction"] is False and r["correction_source"] == "next_message"


def test_late_coach_backstop_inside_grace_counts(home):
    turn("s1", "fixed", ("five-whys", SKILL_DONE))
    t = core.time.time()
    msg = "the sidebar session still shows red after your change"
    plug.pre_llm_call(session_id="s1", user_message=msg)
    (home / "coach").mkdir()
    (home / "coach" / "log.jsonl").write_text(json.dumps(
        {"ts": t + 50 * 60, "event": "selfheal_detect", "source": "backstop", "session_id": "s1",
         "evidence": msg}) + "\n")
    assert core.flush(home, now=t + 16 * 60) == []  # the old 15-min grace would have finalized it here
    core.flush(home, now=t + core.GRACE_S + 1)
    (r,) = lines(home)
    assert r["user_correction"] is True and r["correction_source"] == "coach"


def test_pending_record_closed_by_a_notice_is_reopened_and_old_lines_skip_correction_rate(home):
    turn("s1", "done", ("five-whys", SKILL_DONE))
    t = core.time.time()
    with core._locked(home) as box:  # a record the old rules closed on a Kanban notice
        box["data"][0].update(next_user="✔ [default] @default Kanban t_1 done — x", next_user_ts=t)
    core.flush(home, now=t + core.IDLE_S + 1)
    (r,) = lines(home)
    assert r["user_correction"] is None and r["correction_source"] == "reopened"
    old = {"ts": t, "host": "h", "session": "s0", "skill": "five-whys", "user_correction": True,
           "checks_first_pass": None}
    tab = core.rollup([old], t - 1, t + 1)
    assert tab[0]["correction_rate"] is None
    assert "predate the correction-rule fix" in "\n".join(core.rollup_lines([old], t - 1, t + 1))


def test_steer_envelope_counts_and_display_kind_turns_do_not(home):
    turn("s1", "done", ("five-whys", SKILL_DONE))
    t = core.time.time()
    # a Hermes-written turn with no known prefix, marked only by display_kind on the stored message
    plug.pre_llm_call(session_id="s1", user_message="Background process finished.",
                      conversation_history=[{"role": "user", "content": "x", "display_kind": "process_complete"}])
    # the user's own mid-turn steer, wrapped in the out-of-band envelope coach's SYNTHETIC_RE would reject
    plug.pre_llm_call(session_id="s1", user_message="[OUT-OF-BAND USER MESSAGE — from the user]\n"
                      "that's wrong, the hub is the target\n[/OUT-OF-BAND USER MESSAGE]",
                      conversation_history=[{"role": "user", "content": "x", "display_kind": "steer"}])
    core.flush(home, now=t + core.GRACE_S + 1)
    (r,) = lines(home)
    assert r["user_correction"] is True and r["correction_source"] == "keyword"


# ---------------------------------------------------------------- reload + state.db fallback (rules 3)
def _state_db(h, session, rows):
    import sqlite3
    con = sqlite3.connect(h / "state.db")
    con.execute("CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY, session_id TEXT, role TEXT, "
                "content TEXT, timestamp REAL, display_kind TEXT)")
    con.executemany("INSERT INTO messages (session_id, role, content, timestamp, display_kind) VALUES (?,?,?,?,?)",
                    [(session, "user", text, ts, kind) for ts, text, kind in rows])
    con.commit()
    con.close()


def test_plugin_reload_loads_the_current_core(tmp_path):
    # a gateway reload re-executes __init__.py; it must not keep an older outcomes.py from sys.modules
    import shutil
    d = tmp_path / "skill-outcomes"
    shutil.copytree(P, d, ignore=shutil.ignore_patterns("tests", "__pycache__"))

    def load():
        sp = importlib.util.spec_from_file_location("so_reload_probe", d / "__init__.py")
        m = importlib.util.module_from_spec(sp)
        sp.loader.exec_module(m)
        return m

    assert not hasattr(load().core, "RELOAD_PROBE")
    (d / "outcomes.py").write_text((d / "outcomes.py").read_text() + "\nRELOAD_PROBE = 1\n")
    assert load().core.RELOAD_PROBE == 1


def test_reply_missed_by_the_hook_is_read_from_state_db(home):
    turn("s1", "done", ("five-whys", SKILL_DONE))
    t = core.time.time()
    # no pre_llm_call reached the plugin; the transcript holds two Hermes notices, then the user's correction
    _state_db(home, "s1", [(t + 5, "[ASYNC DELEGATION BATCH COMPLETE — deleg_1] x", "async_delegation_complete"),
                           (t + 6, "Background process finished.", "process_complete"),
                           (t + 9, "no, that's the wrong host", None)])
    assert core.flush(home, now=t + core.IDLE_S + 1) == []  # the reply starts the 3 h grace, not the idle clock
    core.flush(home, now=t + 9 + core.GRACE_S + 1)
    (r,) = lines(home)
    assert r["user_correction"] is True and r["correction_source"] == "keyword"
    assert r["reply_source"] == "state_db" and r["rules"] == core.RULES


def test_state_db_with_only_notices_finalizes_none_after_idle(home):
    turn("s1", "done", ("five-whys", SKILL_DONE))
    t = core.time.time()
    _state_db(home, "s1", [(t + 5, "✔ [default] @default Kanban t_1 done — x", "kanban_notice"),
                           (t - 50, "an earlier message from before the turn ended", None)])
    core.flush(home, now=t + core.IDLE_S + 1)
    (r,) = lines(home)
    assert r["user_correction"] is False and r["correction_source"] == "none"


def test_lines_before_rules_3_are_left_out_of_correction_rate():
    t = 1000.0
    old = {"ts": t, "host": "h", "session": "s0", "skill": "x", "user_correction": False, "rules": 2,
           "checks_first_pass": None}
    assert core.rollup([old], t - 1, t + 1)[0]["correction_rate"] is None


def test_subagent_prompts_and_compaction_notes_are_not_replies(home):
    import sqlite3
    turn("s1", "done", ("five-whys", SKILL_DONE))
    turn("sub", "done", ("five-whys", SKILL_DONE))
    t = core.time.time()
    plug.pre_llm_call(session_id="sub", user_message="no, review the other PR", parent_session_id="s1")
    plug.pre_llm_call(session_id="s1", user_message="[Your active task list was preserved across context "
                      "compression]\n- [>] 3. Verify access")
    plug.pre_llm_call(session_id="s1", user_message="New note on your kanban task t_1: wrong host")
    _state_db(home, "sub", [(t + 5, "no, that's the wrong PR", None)])
    con = sqlite3.connect(home / "state.db")
    con.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY, source TEXT)")
    con.execute("INSERT INTO sessions VALUES ('sub', 'subagent')")
    con.commit()
    con.close()
    core.flush(home, now=t + core.IDLE_S + 1)
    rs = {r["session"]: r for r in lines(home)}
    assert rs["s1"]["correction_source"] == "none" and rs["sub"]["correction_source"] == "none"


def test_state_db_reply_in_a_delegated_child_session_is_not_a_user_reply(home):
    # delegate_task children are stored with source=desktop but carry parent_session_id; their later user turns
    # are written by the parent agent, so they never close the correction window (the hook path skips them too)
    import sqlite3
    turn("child", "done", ("five-whys", SKILL_DONE))
    t = core.time.time()
    _state_db(home, "child", [(t + 5, "no, that's the wrong PR", None)])
    con = sqlite3.connect(home / "state.db")
    con.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY, source TEXT, parent_session_id TEXT)")
    con.execute("INSERT INTO sessions VALUES ('child', 'desktop', 'top')")
    con.commit()
    con.close()
    core.flush(home, now=t + core.IDLE_S + 1)
    (r,) = lines(home)
    assert r["correction_source"] == "none" and r.get("reply_source") is None


# ---------------------------------------------------------------- forks and interrupted turns (rules 4)
def _tool_rows(h, session, rows):
    import sqlite3
    con = sqlite3.connect(h / "state.db")
    con.execute("CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY, session_id TEXT, role TEXT, "
                "content TEXT, timestamp REAL, display_kind TEXT, tool_name TEXT)")
    con.executemany("INSERT INTO messages (session_id, role, content, timestamp, tool_name) VALUES (?,?,?,?,?)",
                    [(session, "tool", content, ts, name) for ts, name, content in rows])
    con.commit()
    con.close()


def test_background_review_fork_skill_view_is_not_a_use(home):
    # live turn t1 starts; after it ends, a fork (same session_id, own turn_id, no pre/post_llm_call) views a skill
    plug.pre_llm_call(session_id="s1", user_message="fix the login", turn_id="t1")
    plug.post_llm_call(session_id="s1", assistant_response="done", turn_id="t1")
    fork = dict(tool_name="skill_view", args={"name": "five-whys"}, result=view("five-whys", SKILL_DONE),
                session_id="s1", turn_id="fork-review-1")
    assert plug.transform_tool_result(**fork) is None  # no tick-off ask inside the fork
    plug.post_tool_call(**fork)
    # the parent's next turn loads nothing; the fork's view must not be filed under it
    plug.pre_llm_call(session_id="s1", user_message="why is this red", turn_id="t2")
    plug.post_llm_call(session_id="s1", assistant_response="ok", turn_id="t2")
    core.flush(home, force=True)
    assert lines(home) == []


def test_flush_drops_a_use_the_transcript_never_saw(home):
    # a view recorded before the plugin knew the live turn (e.g. right after a gateway restart): state.db decides
    turn("s1", "done", ("five-whys", SKILL_DONE), ("token-broker", SKILL_PLAIN))
    t = core.time.time()
    _tool_rows(home, "s1", [(t - 1, "skill_view", view("token-broker", SKILL_PLAIN)),
                            (t - 1, "terminal", '{"output": ""}')])
    core.flush(home, force=True)
    assert [r["skill"] for r in lines(home)] == ["token-broker"]


def test_interrupted_turn_uses_close_at_the_next_message(home):
    # turn t1 loads a skill and is interrupted (no post_llm_call); the next message ("whats up?") is its reply,
    # not the message after turn t2
    plug.pre_llm_call(session_id="s1", user_message="add a login", turn_id="t1")
    plug.post_tool_call(tool_name="skill_view", args={"name": "five-whys"}, result=view("five-whys", SKILL_DONE),
                        session_id="s1", turn_id="t1")
    plug.pre_llm_call(session_id="s1", user_message="whats up?", turn_id="t2")
    plug.post_llm_call(session_id="s1", assistant_response="working on it", turn_id="t2")
    plug.pre_llm_call(session_id="s1", user_message="no, that's the wrong host", turn_id="t3")
    core.flush(home, now=core.time.time() + core.GRACE_S + 1)
    (r,) = lines(home)
    assert r["turn_id"] == "t1" and r["interrupted"] is True
    assert r["user_correction"] is False and r["correction_source"] == "next_message"
    assert r["rules"] == 4


def test_lines_before_rules_4_are_left_out_of_correction_rate():
    t = 1000.0
    old = {"ts": t, "host": "h", "session": "s0", "skill": "x", "user_correction": True, "rules": 3,
           "checks_first_pass": None}
    assert core.rollup([old], t - 1, t + 1)[0]["correction_rate"] is None
