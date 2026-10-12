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


core = load("ts_core_test", ROOT / "core.py")


def make_db(tmp_path, user_msgs, extra_session=None):
    p = tmp_path / "state.db"
    con = sqlite3.connect(p)
    con.execute("create table sessions (id text, title text, cwd text, git_branch text, git_repo_root text, model text,"
                " profile_name text, parent_session_id text, end_reason text)")
    con.execute("create table messages (id integer primary key, session_id text, role text, content text,"
                " tool_calls text, display_kind text)")
    con.execute("insert into sessions values ('s1','Babybear voice','/code/bb','main','/code/bb','m','default',null,null)")
    for u in user_msgs:
        con.execute("insert into messages (session_id, role, content) values ('s1','user',?)", (u,))
        con.execute("insert into messages (session_id, role, content) values ('s1','assistant','ok done')", ())
    calls = json.dumps([{"function": {"name": "patch", "arguments": json.dumps({"path": "/code/bb/pod.py"})}}])
    con.execute("insert into messages (session_id, role, content, tool_calls) values ('s1','assistant','',?)", (calls,))
    con.execute("insert into messages (session_id, role, content, display_kind) values ('s1','user','resume','auto_continue')")
    con.commit()
    con.close()
    return p


class FakeLLM:
    def __init__(self, reply):
        self.reply, self.calls = reply, []

    def __call__(self, messages, max_tokens):
        self.calls.append(messages)
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


PRIOR = ["fix the wake word on the macbook mic", "it still misses half the time", "try the bigger model"]
NEW = "check my email for the 2026 tax return and download it"


def test_shift_detected_above_threshold(tmp_path):
    db = make_db(tmp_path, PRIOR + [NEW])  # just-sent message already persisted
    llm = FakeLLM('{"new_topic": true, "confidence": 0.92, "label": "Tax return download", "reason": "unrelated"}')
    out = core.classify("s1", NEW, llm, db)
    assert out["shift"] is True and out["label"] == "Tax return download"
    prompt = llm.calls[0][1]["content"]
    assert NEW in prompt and "fix the wake word" in prompt
    assert prompt.count(NEW) == 1, "just-sent message must not also appear as prior context"
    assert "resume" not in prompt.split("NEWEST")[0], "auto_continue rows are not user context"


def test_low_confidence_is_not_a_shift(tmp_path):
    db = make_db(tmp_path, PRIOR)
    out = core.classify("s1", NEW, FakeLLM('{"new_topic": true, "confidence": 0.6}'), db)
    assert out["shift"] is False


def test_same_topic(tmp_path):
    db = make_db(tmp_path, PRIOR)
    out = core.classify("s1", "ok now also tune the vad threshold", FakeLLM('{"new_topic": false, "confidence": 0.9}'), db)
    assert out["shift"] is False


def test_skip_rules_make_no_llm_call(tmp_path):
    db = make_db(tmp_path, PRIOR)
    llm = FakeLLM("{}")
    assert core.classify("s1", "/undo", llm, db)["skipped"] == "slash"
    assert core.classify("s1", "yes do it", llm, db)["skipped"] == "short"
    (tmp_path / "y").mkdir()
    young = make_db(tmp_path / "y", PRIOR[:2])
    assert core.classify("s1", NEW, llm, young)["skipped"] == "young_session"
    assert core.classify("nope", NEW, llm, db)["skipped"] == "unknown_session"
    assert llm.calls == []


def test_llm_failure_fails_open(tmp_path):
    db = make_db(tmp_path, PRIOR)
    out = core.classify("s1", NEW, FakeLLM(RuntimeError("boom")), db)
    assert out["shift"] is False and out["skipped"].startswith("llm_error")


def test_brief_and_seeded_message_via_api(tmp_path):
    db = make_db(tmp_path, PRIOR + [NEW])
    llm = FakeLLM("**Was working on** wake word")
    api = load("ts_api_test", ROOT / "dashboard/plugin_api.py")
    app = FastAPI()
    app.include_router(api.create_router(llm=llm, db_path=db), prefix="/api/plugins/topic-shift")
    c = TestClient(app)
    out = c.post("/api/plugins/topic-shift/brief", json={"session_id": "s1", "text": NEW}).json()
    prompt = llm.calls[0][1]["content"]
    assert "/code/bb/pod.py" in prompt, "files touched are passed to the brief"
    assert prompt.split("NEW REQUEST")[0].count(NEW) == 0, "new message excluded from transcript"
    seeded = out["seeded"]
    assert seeded.startswith('Context carried over from @session:default/s1 ("Babybear voice")')
    assert "**Was working on** wake word" in seeded and seeded.rstrip().endswith(NEW)
    chk = c.post("/api/plugins/topic-shift/check", json={"session_id": "s1", "text": "hi"}).json()
    assert chk == {"shift": False, "skipped": "short"}
