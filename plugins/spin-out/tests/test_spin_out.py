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


core = load("so_core_test", ROOT / "core.py")
api = load("so_api_test", ROOT / "dashboard" / "plugin_api.py")

FINAL = ("Shipped the login fix. Not done yet: 1) the sync job still fails on the mini, "
         "2) add tests for token refresh. You need to decide whether to buy the Pro plan.")


def make_db(tmp_path, with_reply=True, compressed_parent=False):
    p = tmp_path / "state.db"
    con = sqlite3.connect(p)
    con.execute("create table sessions (id text, title text, cwd text, git_branch text, git_repo_root text, model text,"
                " profile_name text, parent_session_id text, end_reason text)")
    con.execute("create table messages (id integer primary key, session_id text, role text, content text,"
                " tool_calls text, display_kind text)")
    parent = "s0" if compressed_parent else None
    if compressed_parent:
        con.execute("insert into sessions values ('s0','App','/code/app','main','/code/app','m','default',null,'compression')")
        con.execute("insert into messages (session_id, role, content) values ('s0','user','EARLY CONTEXT login bug')")
    con.execute("insert into sessions values ('s1','App login','/code/app','main','/code/app','m','default',?,null)", (parent,))
    con.execute("insert into messages (session_id, role, content) values ('s1','user','fix login')")
    calls = json.dumps([{"function": {"name": "patch", "arguments": json.dumps({"path": "/code/app/auth.py"})}}])
    con.execute("insert into messages (session_id, role, content, tool_calls) values ('s1','assistant','',?)", (calls,))
    if with_reply:
        con.execute("insert into messages (session_id, role, content) values ('s1','assistant',?)", (FINAL,))
    con.execute("insert into messages (session_id, role, content, display_kind) values ('s1','user','HIDDEN','hidden')")
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


PLAN = json.dumps({
    "tasks": [
        {"title": "Fix sync job on mini", "prompt": "Fix the sync job in /code/app ...", "needs_parent": True},
        {"title": "Token refresh tests", "prompt": "Add tests for token refresh in /code/app/auth.py", "needs_parent": False},
        {"title": "bad", "prompt": "   "},
    ],
    "skipped": [{"item": "Buy Pro plan", "reason": "needs your decision"}],
})


def test_plan_parses_tasks_and_context(tmp_path):
    db = make_db(tmp_path, compressed_parent=True)
    llm = FakeLLM("Here you go:\n" + PLAN)
    out = core.plan("s1", llm, db)
    assert [t["title"] for t in out["tasks"]] == ["Fix sync job on mini", "Token refresh tests"]
    assert out["skipped"][0]["item"] == "Buy Pro plan"
    assert out["cwd"] == "/code/app" and out["profile"] == "default"
    sent = llm.calls[0][1]["content"]
    assert FINAL in sent                      # final reply kept whole
    assert "/code/app/auth.py" in sent        # touched files
    assert "EARLY CONTEXT" in sent            # compression ancestor included
    assert "HIDDEN" not in sent               # hidden rows excluded


def test_plan_errors(tmp_path):
    assert core.plan("nope", FakeLLM(PLAN), make_db(tmp_path))["error"] == "unknown session"
    (tmp_path / "x").mkdir()
    db2 = make_db(tmp_path / "x", with_reply=False)
    assert "no assistant reply" in core.plan("s1", FakeLLM(PLAN), db2)["error"]


def test_plan_llm_failure_and_garbage(tmp_path):
    db = make_db(tmp_path)
    assert "planner failed" in core.plan("s1", FakeLLM(RuntimeError("boom")), db)["error"]
    assert core.plan("s1", FakeLLM("not json"), db)["tasks"] == []


def test_child_message_points_to_parent():
    m = core.child_message({"prompt": "Do X", "needs_parent": True}, "s1", "work", "App login")
    assert m.startswith("Do X") and "@session:work/s1" in m and "Read the parent" in m
    m2 = core.child_message({"prompt": "Do Y", "needs_parent": False}, "s1", "", "")
    assert "@session:default/s1" in m2 and "only if you hit a gap" in m2


def test_router_plan(tmp_path):
    app = FastAPI()
    app.include_router(api.create_router(llm=FakeLLM(PLAN), db_path=make_db(tmp_path)))
    r = TestClient(app).post("/plan", json={"session_id": "s1"}).json()
    assert len(r["tasks"]) == 2 and r["profile"] == "default"
    assert all("@session:default/s1" in t["message"] for t in r["tasks"])
