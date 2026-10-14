import importlib.util
import json
import sqlite3
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


core = load("pc_core_test", ROOT / "core.py")

TASK = "make me a slide deck for the quarterly business review from the numbers in q3.csv"


def make_db(tmp_path, user_msgs):
    p = tmp_path / "state.db"
    con = sqlite3.connect(p)
    con.execute("create table sessions (id text, title text, cwd text)")
    con.execute("create table messages (id integer primary key, session_id text, role text, content text, display_kind text)")
    con.execute("insert into sessions values ('s1','QBR prep','/work/acme')")
    for u in user_msgs:
        con.execute("insert into messages (session_id, role, content) values ('s1','user',?)", (u,))
        con.execute("insert into messages (session_id, role, content) values ('s1','assistant','sure, working on it')")
    con.commit()
    con.close()
    return p


class FakeLLM:
    def __init__(self, classify, rewrite=None):
        self.replies = {"classify": classify, "rewrite": rewrite}
        self.calls = []

    def __call__(self, stage, messages, max_tokens):
        self.calls.append((stage, messages))
        r = self.replies[stage]
        if isinstance(r, Exception):
            raise r
        return r


YES = '{"task_request": true, "followup": false, "gain": 0.9, "lens": "altitude", "why": "asks for slides not decisions"}'
RW = json.dumps({"lens": "altitude", "critique": "You asked for slides; the goal is decisions.",
                 "rewrite": "Using q3.csv and everything else you can reach, tell me what changed this quarter, why, and the three most urgent decisions.",
                 "confidence": 0.85})


def test_coaches_task_request(tmp_path):
    db = make_db(tmp_path, ["hi", TASK])
    llm = FakeLLM(YES, RW)
    out = core.coach("s1", TASK, llm, db, tmp_path / "log.jsonl")
    assert out["coach"] is True and out["lens"] == "altitude" and "decisions" in out["rewrite"]
    assert [c[0] for c in llm.calls] == ["classify", "rewrite"]
    ctx = llm.calls[0][1][1]["content"]
    # the just-sent message is the NEWEST, not part of the history
    assert "Earlier user messages in this session: 1" in ctx and ctx.count(TASK) == 1
    log = [json.loads(l) for l in (tmp_path / "log.jsonl").read_text().splitlines()]
    assert log[0]["event"] == "check" and log[0]["coach"] is True


def test_first_message_without_session(tmp_path):
    llm = FakeLLM(YES, RW)
    out = core.coach("", TASK, llm, tmp_path / "missing.db", tmp_path / "log.jsonl")
    assert out["coach"] is True
    assert "(new session)" in llm.calls[0][1][1]["content"]


def test_followup_skips_rewrite(tmp_path):
    llm = FakeLLM('{"task_request": true, "followup": true, "gain": 0.9}', RW)
    out = core.coach("", TASK, llm, None, tmp_path / "log.jsonl")
    assert out["coach"] is False and out["skipped"] == "not_worth_it"
    assert [c[0] for c in llm.calls] == ["classify"]


def test_low_gain_and_low_confidence(tmp_path):
    low = '{"task_request": true, "followup": false, "gain": 0.2}'
    assert core.coach("", TASK, FakeLLM(low, RW), None, tmp_path / "l")["skipped"] == "not_worth_it"
    rw = json.loads(RW) | {"confidence": 0.4}
    out = core.coach("", TASK, FakeLLM(YES, json.dumps(rw)), None, tmp_path / "l")
    assert out["coach"] is False and out["skipped"] == "low_confidence"


def test_identical_rewrite_not_shown(tmp_path):
    rw = json.loads(RW) | {"rewrite": TASK}
    assert core.coach("", TASK, FakeLLM(YES, json.dumps(rw)), None, tmp_path / "l")["coach"] is False


def test_skips_without_llm_call(tmp_path):
    llm = FakeLLM(YES, RW)
    for t in ["", "/new", "ok do it", "[prompt-coach] " + TASK, "Context carried over from @session:default/x: " + TASK]:
        assert core.coach("", t, llm, None, tmp_path / "l")["coach"] is False
    assert llm.calls == []


def test_llm_error_fails_open(tmp_path):
    out = core.coach("", TASK, FakeLLM(RuntimeError("boom")), None, tmp_path / "l")
    assert out["coach"] is False and out["skipped"].startswith("llm_error")
    out = core.coach("", TASK, FakeLLM(YES, RuntimeError("boom")), None, tmp_path / "l")
    assert out["coach"] is False and out["skipped"].startswith("llm_error")


def test_garbage_json_and_bad_lens(tmp_path):
    assert core.coach("", TASK, FakeLLM("no json here"), None, tmp_path / "l")["coach"] is False
    rw = json.loads(RW) | {"lens": "vibes"}
    assert core.coach("", TASK, FakeLLM(YES, "```json\n" + json.dumps(rw) + "\n```"), None, tmp_path / "l")["lens"] == "altitude"


def test_injected_memory_context_stripped(tmp_path):
    db = make_db(tmp_path, ["fix it <memory-context>SECRET recall blob</memory-context>", TASK])
    llm = FakeLLM(YES, RW)
    core.coach("s1", TASK, llm, db, tmp_path / "l")
    assert "SECRET" not in llm.calls[0][1][1]["content"] and "fix it" in llm.calls[0][1][1]["content"]


def test_seeded_message():
    s = core.seeded_message("abc", "work", "Do the big thing.", "do the small thing")
    assert s.startswith("Do the big thing.") and "@session:work/abc" in s and "do the small thing" in s
    assert core.skip_reason(s) == "seeded"  # never coach our own launched rewrite
    assert "another session" in core.seeded_message("", "", "X", "y")


def test_stats_and_choices(tmp_path):
    log = tmp_path / "log.jsonl"
    out = core.coach("", TASK, FakeLLM(YES, RW), None, log)
    assert core.record_choice(out["id"], "new_session", True, log)["ok"]
    assert not core.record_choice(out["id"], "bogus", log=log)["ok"]
    st = core.stats(log)
    assert st["checked"] == 1 and st["shown"] == 1 and st["by_lens"] == {"altitude": 1}
    assert st["choices"] == {"new_session": 1} and st["examples"][0]["choice"] == "new_session"


def test_router(tmp_path):
    api = load("pc_api_test", ROOT / "dashboard" / "plugin_api.py")
    app = FastAPI()
    app.include_router(api.create_router(llm=FakeLLM(YES, RW), db_path=tmp_path / "x.db", log_path=tmp_path / "log.jsonl"))
    c = TestClient(app)
    r = c.post("/check", json={"text": TASK}).json()
    assert r["coach"] is True
    assert c.post("/choice", json={"id": r["id"], "choice": "keep"}).json()["ok"]
    assert "@session:default/s9" in c.post("/seed", json={"session_id": "s9", "rewrite": "R", "original": "o"}).json()["seeded"]
    assert c.get("/stats").json()["choices"] == {"keep": 1}
