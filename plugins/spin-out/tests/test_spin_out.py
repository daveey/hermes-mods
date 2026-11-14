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
    # human-dependent items are never left behind: a "skipped" entry is promoted to a task
    assert [t["title"] for t in out["tasks"]] == ["Fix sync job on mini", "Token refresh tests", "Buy Pro plan"]
    assert out["skipped"] == []
    assert out["tasks"][2]["needs_parent"] and "needs your decision" in out["tasks"][2]["prompt"]
    assert "skipped" not in llm.calls[0][0]["content"].split("Reply with ONLY JSON")[1]
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
    assert len(r["tasks"]) == 3 and r["profile"] == "default"   # incl. the promoted "skipped" item
    assert all("@session:default/s1" in t["message"] for t in r["tasks"])


def test_auto_skips_without_cues_and_caches(tmp_path):
    db = make_db(tmp_path)
    con = sqlite3.connect(db)
    con.execute("insert into messages (session_id, role, content) values ('s1','assistant','All shipped and verified.')")
    con.commit(); con.close()
    llm = FakeLLM(PLAN)
    out = core.plan("s1", llm, db, auto=True)
    assert out["tasks"] == [] and "auto_skip" in out and llm.calls == []   # no model call
    assert core.has_followups(FINAL)                                      # "Not done yet" is a cue
    assert len(core.plan("s1", llm, db, auto=False)["tasks"]) == 3        # manual ⇧⌘F always plans


def test_router_auto_cache(tmp_path):
    llm = FakeLLM(PLAN)
    app = FastAPI()
    app.include_router(api.create_router(llm=llm, db_path=make_db(tmp_path)))
    c = TestClient(app)
    a = c.post("/plan", json={"session_id": "s1", "auto": True}).json()
    b = c.post("/plan", json={"session_id": "s1", "auto": True}).json()
    assert len(a["tasks"]) == 3 and b.get("cached") is True and len(llm.calls) == 1


class FakeKanban:
    def __init__(self):
        self.created, self.keys = [], {}

    def init_db(self, board=None):
        pass

    def get_current_board(self):
        return "babybear"

    def create_task(self, con, **kw):
        key = kw["idempotency_key"]
        if key not in self.keys:
            self.keys[key] = f"t_{len(self.keys) + 1}"
            self.created.append(kw)
        return self.keys[key]

    def connect(self, board=None):
        return sqlite3.connect(":memory:")


def test_kanban_cards(tmp_path):
    kb = FakeKanban()
    tasks = [{"title": "Fix sync job", "message": "Fix it @session:default/s1"}, {"title": "Tests", "message": "Add"}]
    out = core.kanban_cards(tasks, "s1", "work", str(tmp_path), kb=(kb, kb))
    assert out["board"] == "babybear" and [c["task_id"] for c in out["cards"]] == ["t_1", "t_2"]
    kw = kb.created[0]
    assert kw["assignee"] == "work" and kw["session_id"] == "s1" and kw["body"].startswith("Fix it")
    assert kw["workspace_kind"] == "dir" and kw["workspace_path"] == str(tmp_path)
    again = core.kanban_cards(tasks[:1], "s1", "work", "/no/such/dir", kb=(kb, kb))
    assert again["cards"][0]["task_id"] == "t_1" and len(kb.created) == 2   # idempotent


def test_router_kanban(tmp_path):
    kb = FakeKanban()
    app = FastAPI()
    app.include_router(api.create_router(llm=FakeLLM(PLAN), db_path=make_db(tmp_path), kb=(kb, kb)))
    r = TestClient(app).post("/kanban", json={"session_id": "s1", "profile": "default",
                                              "tasks": [{"title": "A", "message": "do A"}, {"title": "B", "message": " "}]}).json()
    assert [c["title"] for c in r["cards"]] == ["A"] and "workspace_kind" not in kb.created[0]


def test_spun_ledger_note_once_and_planner_skips(tmp_path):
    db, led = make_db(tmp_path, compressed_parent=True), tmp_path / "spun.json"
    # recorded on the compressed ancestor: the live continuation still sees it
    assert core.record_spun("s0", [{"title": "Fix sync job on mini", "ref": "@session:default/c1"}], led) == 1
    assert core.record_spun("s0", [{"title": "Fix sync job on mini", "ref": "@session:default/c1"}], led) == 0
    items = core.take_undelivered("s1", db, led)
    note = core.spun_note(items)
    assert "Fix sync job on mini → @session:default/c1" in note and "do not offer" in note
    assert core.take_undelivered("s1", db, led) == []            # delivered once
    llm = FakeLLM(PLAN)
    out = core.plan("s1", llm, db, ledger=led)
    assert "Fix sync job on mini" not in [t["title"] for t in out["tasks"]]
    assert "ALREADY SPUN OUT" in llm.calls[0][1]["content"]


def test_router_spun_and_kanban_record(tmp_path):
    kb, led = FakeKanban(), tmp_path / "spun.json"
    app = FastAPI()
    app.include_router(api.create_router(llm=FakeLLM(PLAN), db_path=make_db(tmp_path), kb=(kb, kb), ledger=led))
    c = TestClient(app)
    assert c.get("/ping").json()["api"] == 3
    assert c.post("/spun", json={"session_id": "s1", "items": [{"title": "A", "ref": "@session:default/c9"}]}).json() == {"added": 1}
    c.post("/kanban", json={"session_id": "s1", "tasks": [{"title": "B", "message": "do B"}]})
    refs = [r["ref"] for r in core.spun_items("s1", None, led)]
    assert refs == ["@session:default/c9", "Kanban card t_1 on board babybear"]


def test_agent_hook_injects_note(tmp_path, monkeypatch):
    import sys as _sys
    led = tmp_path / "spun.json"
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    (tmp_path / "spin-out").mkdir()
    plugin = load("so_plugin_test", ROOT / "__init__.py")
    _sys.modules.pop("_spin_out_core", None)
    hooks = {}
    plugin.register(type("Ctx", (), {"register_hook": lambda self, n, f: hooks.setdefault(n, f)})())
    c = plugin._core()
    monkeypatch.setattr(c, "hermes_home", lambda: tmp_path)
    c.record_spun("sX", [{"title": "A", "ref": "@session:default/c9"}])
    assert hooks["pre_llm_call"](session_id="") is None
    assert "@session:default/c9" in hooks["pre_llm_call"](session_id="sX")["context"]
    assert hooks["pre_llm_call"](session_id="sX") is None


# ── a spun-out child takes over the parent's cards (kanban-abb8d6e8a7) ───────────────────────────────
# Hub chat 20261113_195327_45059d was spun out with "Drive kanban card t_... to completion"; the parent owned the
# card (task_chat_links 'created'), the child got no link, so its task list was empty while it waited on that card.

BRIEF = ("Drive kanban card `t_01a1ce6d3d682365` (ring speed) to completion. Done cards like t_00000000000000aa "
         "and t_00000000000000bb are context only.\n\n---\nSpun out of parent session "
         "@session:default/20261111_091916_d2d36a (\"Add Pebble ring webhook ingestion\"). Read the parent transcript.")


def make_kanban(tmp_path, name="kanban.db"):
    p = tmp_path / name
    p.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(p)
    con.execute("create table tasks (id text, status text)")
    con.execute("create table task_chat_links (task_id text, chat_gateway text, chat_profile text, chat_session text,"
                " kind text, released integer)")
    con.execute("create table kanban_notify_subs (task_id text, platform text, chat_id text)")
    con.executemany("insert into tasks values (?,?)", [("t_01a1ce6d3d682365", "scheduled"),
                                                       ("t_00000000000000aa", "done"),
                                                       ("t_00000000000000bb", "running")])
    con.executemany("insert into task_chat_links values (?,?,?,?,?,?)", [
        ("t_01a1ce6d3d682365", "hub", None, "20261111_091916_d2d36a", "created", 0),
        ("t_00000000000000aa", "hub", None, "20261111_091916_d2d36a", "created", 0),   # done: nothing to drive
        ("t_00000000000000bb", "hub", None, "someone_else", "created", 0),             # not the parent's
    ])
    con.commit()
    con.close()
    return p


def test_spun_child_links_parent_cards_named_in_brief(tmp_path):
    make_kanban(tmp_path)
    ran = []

    def next_step(task_id, board, env):
        return {"next_step": f"hermes-hub adopt-task macbook default/{board}/{task_id} --origin-session "
                             f"{env['HERMES_SESSION_ID']} --id adopt:{task_id}", "complete": True}

    def runner(argv):
        ran.append(argv)
        return 0, "adopt:x handoff pending: queued request"

    out = core.link_spun_cards("20261113_195327_45059d", BRIEF, home=tmp_path, next_step=next_step, runner=runner)
    assert [r["task_id"] for r in out] == ["t_01a1ce6d3d682365"]
    assert ran == [["hermes-hub", "adopt-task", "macbook", "default/default/t_01a1ce6d3d682365", "--origin-session",
                    "20261113_195327_45059d", "--id", "adopt:t_01a1ce6d3d682365"]]
    assert out[0]["ok"] and out[0]["board"] == "default"
    note = core.linked_note(out)
    assert "t_01a1ce6d3d682365" in note and "linked" in note.lower()


def test_spun_child_link_failure_names_the_command(tmp_path):
    make_kanban(tmp_path / "kanban" / "boards" / "bb", "kanban.db")
    out = core.link_spun_cards("child1", BRIEF, home=tmp_path,
                               next_step=lambda t, b, env: {"next_step": f"hermes kanban --board {b} notify-subscribe "
                                                                         f"{t} --platform tui --chat-id child1",
                                                            "complete": True},
                               runner=lambda argv: (1, "boom"))
    assert len(out) == 1 and not out[0]["ok"] and out[0]["board"] == "bb"
    note = core.linked_note(out)
    assert "notify-subscribe t_01a1ce6d3d682365" in note and "boom" in note


def test_non_spun_or_later_turn_links_nothing(tmp_path):
    make_kanban(tmp_path)
    boom = lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not run"))
    assert core.link_spun_cards("c", "Drive kanban card t_01a1ce6d3d682365", home=tmp_path,
                                next_step=boom, runner=boom) == []


def test_agent_hook_links_cards_on_first_turn_only(tmp_path, monkeypatch):
    import sys as _sys
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    plugin = load("so_plugin_link_test", ROOT / "__init__.py")
    _sys.modules.pop("_spin_out_core", None)
    c = plugin._core()
    monkeypatch.setattr(c, "hermes_home", lambda: tmp_path)
    calls = []
    monkeypatch.setattr(c, "link_spun_cards", lambda sid, msg, **k: calls.append((sid, msg)) or
                        [{"task_id": "t_01a1ce6d3d682365", "board": "default", "ok": True, "step": "x", "output": ""}])
    ctx = plugin.pre_llm_call(session_id="child", user_message=BRIEF, is_first_turn=True)
    assert calls == [("child", BRIEF)] and "t_01a1ce6d3d682365" in ctx["context"]
    assert plugin.pre_llm_call(session_id="child", user_message=BRIEF, is_first_turn=False) is None
    assert len(calls) == 1


def _adopt_step(task_id, board, env):
    return {"next_step": f"hermes-hub adopt-task macbook default/{board}/{task_id}", "complete": True}


def test_spun_child_skips_released_parent_links(tmp_path):
    # A released chat link no longer makes the card the parent's: the child must not take it over.
    p = make_kanban(tmp_path)
    con = sqlite3.connect(p)
    con.execute("insert into tasks values ('t_00000000000000cc','running')")
    con.execute("insert into task_chat_links values ('t_00000000000000cc','hub',null,'20261111_091916_d2d36a',"
                "'created',1)")
    con.commit(); con.close()
    brief = "Also drive t_00000000000000cc. " + BRIEF
    ran = []
    out = core.link_spun_cards("child", brief, home=tmp_path, next_step=_adopt_step,
                               runner=lambda argv: ran.append(argv) or (0, ""))
    assert [r["task_id"] for r in out] == ["t_01a1ce6d3d682365"]
    assert all("t_00000000000000cc" not in " ".join(a) for a in ran)


def test_spun_child_incomplete_next_step_is_not_run(tmp_path):
    make_kanban(tmp_path)
    ran = []
    out = core.link_spun_cards("child", BRIEF, home=tmp_path,
                               next_step=lambda t, b, env: {"next_step": f"hermes-hub adopt-task x {t}",
                                                            "complete": False},
                               runner=lambda argv: ran.append(argv) or (0, "ok"))
    assert ran == []
    assert len(out) == 1 and not out[0]["ok"] and out[0]["output"] == "no complete link command"
    assert out[0]["step"] == "hermes-hub adopt-task x t_01a1ce6d3d682365"


def test_spun_child_links_a_card_on_two_boards_once(tmp_path):
    # The same card id linked to the parent on two board dbs is taken over once (first board wins).
    make_kanban(tmp_path)
    make_kanban(tmp_path / "kanban" / "boards" / "bb", "kanban.db")
    ran = []
    out = core.link_spun_cards("child", BRIEF, home=tmp_path, next_step=_adopt_step,
                               runner=lambda argv: ran.append(argv) or (0, ""))
    assert [(r["task_id"], r["board"]) for r in out] == [("t_01a1ce6d3d682365", "default")]
    assert len(ran) == 1


def test_spun_child_ignores_card_ids_after_the_mark(tmp_path):
    # Only the brief (text before the spin-out pointer) hands cards over; ids after the mark are not the brief's.
    make_kanban(tmp_path)
    brief = ("Look into the ring speed regression.\n\n---\nSpun out of parent session "
             "@session:default/20261111_091916_d2d36a (\"t_01a1ce6d3d682365 ring speed\"). Read the parent transcript.")
    boom = lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not run"))
    assert core.link_spun_cards("child", brief, home=tmp_path, next_step=boom, runner=boom) == []


def test_spun_child_link_exception_is_reported_not_swallowed(tmp_path):
    make_kanban(tmp_path)

    def next_step(task_id, board, env):
        raise RuntimeError("core too old")

    out = core.link_spun_cards("child", BRIEF, home=tmp_path, next_step=next_step,
                               runner=lambda argv: (0, ""))
    assert out == [{"task_id": "t_01a1ce6d3d682365", "board": "default", "ok": False, "step": "",
                    "output": "RuntimeError: core too old"}]
    assert "core too old" in core.linked_note(out)
