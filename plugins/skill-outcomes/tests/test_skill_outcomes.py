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
    turn("s1", "Done means (five-whys): 4/5 first pass, 2 fix rounds", ("five-whys", SKILL_DONE))
    turn("s2", "Done means (five-whys): 5/5 first pass, 0 fix rounds", ("five-whys", SKILL_DONE))
    turn("s3", "nothing", ("other", SKILL_PLAIN))
    t = core.time.time()
    plug.pre_llm_call(session_id="s2", user_message="that's wrong, redo it")
    assert core.main(["flush", "--home", str(home), "--now", str(t + core.IDLE_S + 1)]) == 0
    assert "flushed 3" in capsys.readouterr().out
    rows = core.read_lines(core.home_logs(home) * 2)  # duplicates across sources are dropped
    assert len(rows) == 3
    tab = {d["skill"]: d for d in core.rollup(rows, t - 100, t + 100)}
    assert tab["five-whys"] == {"skill": "five-whys", "uses": 2, "ticked": 2, "first_pass_rate": 0.5,
                                "mean_fix_rounds": 1.0, "correction_rate": 0.5}
    assert tab["other"]["ticked"] == 0 and tab["other"]["first_pass_rate"] is None
    md = "\n".join(core.rollup_lines(rows, t - 100, t + 100))
    assert "| five-whys | 2 | 2 | 50% | 1.00 | 50% |" in md
    assert "No skill uses" in "\n".join(core.rollup_lines([], 0, 1))
