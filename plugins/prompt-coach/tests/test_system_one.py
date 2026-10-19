"""System One (Jev) stage-1 path in prompt-coach. No network."""
from test_prompt_coach import TASK, FakeLLM, core, make_db

CLASSIFY_PASS = '{"task_request": true, "followup": false, "gain": 0.6, "lens": "altitude", "why": "x"}'
REWRITE = ('{"lens": "altitude", "critique": "aim higher", "rewrite": "Build a reusable QBR pipeline from q3.csv",'
           ' "confidence": 0.9}')


def test_on_low_jev_no_llm_calls(tmp_path):
    db = make_db(tmp_path, [TASK])
    llm = FakeLLM(CLASSIFY_PASS, REWRITE)
    out = core.coach("s1", TASK, llm, db, log=tmp_path / "log.jsonl", jev=lambda st: 0.1, mode="on")
    assert out["coach"] is False and out["skipped"] == "not_worth_it" and llm.calls == []


def test_on_high_jev_runs_both_stages(tmp_path):
    db = make_db(tmp_path, [TASK])
    llm, seen = FakeLLM(CLASSIFY_PASS, REWRITE), []
    out = core.coach("s1", TASK, llm, db, log=tmp_path / "log.jsonl", jev=lambda st: seen.append(st) or 0.8, mode="on")
    assert out["coach"] is True and [s for s, _ in llm.calls] == ["classify", "rewrite"]
    assert seen[0]["newest_message"] == TASK and seen[0]["session_title"] == "QBR prep"


def test_shadow_runs_both_llm_decides(tmp_path):
    db = make_db(tmp_path, [TASK])
    llm = FakeLLM(CLASSIFY_PASS, REWRITE)
    out = core.coach("s1", TASK, llm, db, log=tmp_path / "log.jsonl", jev=lambda st: 0.01, mode="shadow")
    assert out["coach"] is True and [s for s, _ in llm.calls] == ["classify", "rewrite"]


def test_jev_unavailable_falls_back(tmp_path):
    db = make_db(tmp_path, [TASK])
    llm = FakeLLM(CLASSIFY_PASS, REWRITE)
    out = core.coach("s1", TASK, llm, db, log=tmp_path / "log.jsonl", jev=lambda st: None, mode="on")
    assert out["coach"] is True and [s for s, _ in llm.calls] == ["classify", "rewrite"]


def test_on_low_jev_is_logged_as_check(tmp_path):
    db = make_db(tmp_path, [TASK])
    core.coach("s1", TASK, FakeLLM(CLASSIFY_PASS, REWRITE), db, log=tmp_path / "log.jsonl", jev=lambda st: 0.1, mode="on")
    rec = __import__("json").loads((tmp_path / "log.jsonl").read_text().splitlines()[-1])
    assert rec["event"] == "check" and rec["stage1"] == {"judge": "jev", "p": 0.1} and rec["coach"] is False


def test_default_mode_on_env_overrides(monkeypatch):
    monkeypatch.delenv("SYSTEM_ONE_MODE", raising=False)
    assert core.jev_mode() == "on"
    monkeypatch.setenv("SYSTEM_ONE_MODE_PROMPT_COACH", "shadow")
    assert core.jev_mode() == "shadow"
