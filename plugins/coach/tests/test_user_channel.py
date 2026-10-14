"""User channel: rule triggers, the gate (budget / mute / demotion), briefs, cards, log + stats — via the REST API."""
import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


core = load("coach_core_test_u", ROOT / "core.py")
api = load("coach_api_test", ROOT / "dashboard" / "plugin_api.py")


def make_db(tmp_path, n_user=3, n_assistant=3, sid="s1", compressed_parent=False):
    p = tmp_path / "state.db"
    con = sqlite3.connect(p)
    con.execute("create table if not exists sessions (id text, title text, cwd text, git_branch text, git_repo_root text,"
                " model text, profile_name text, parent_session_id text, end_reason text)")
    con.execute("create table if not exists messages (id integer primary key, session_id text, role text, content text,"
                " tool_calls text, display_kind text)")
    parent = None
    if compressed_parent:
        parent = sid + "_p"
        con.execute("insert into sessions values (?,'App','/code/app','main','/code/app','m','default',null,'compression')",
                    (parent,))
        for _ in range(200):
            con.execute("insert into messages (session_id, role, content) values (?,'assistant','x')", (parent,))
    con.execute("insert into sessions values (?,'App login','/code/app','main','/code/app','m','default',?,null)",
                (sid, parent))
    for i in range(n_user):
        con.execute("insert into messages (session_id, role, content) values (?,'user',?)", (sid, f"still not working {i}"))
    for i in range(n_assistant):
        con.execute("insert into messages (session_id, role, content) values (?,'assistant',?)", (sid, f"tried fix {i}"))
    con.commit()
    con.close()
    return p


class FakeLLM:
    def __init__(self):
        self.calls = []

    def __call__(self, messages, max_tokens):
        self.calls.append(messages)
        return json.dumps({"title": "Fix login", "brief": "## Goal\nLogin works.\n## Done when\npytest passes"})


class FakeKB:
    def __init__(self):
        self.created = []

    def init_db(self, board=None):
        pass

    def get_current_board(self):
        return "default"

    def create_task(self, con, **kw):
        self.created.append(kw)
        return f"t_{len(self.created)}"

    def connect(self, board=None):
        class C:
            def close(self):
                pass
        return C()


def client(tmp_path, db):
    kb = FakeKB()
    llm = FakeLLM()
    app = FastAPI()
    app.include_router(api.create_router(llm=llm, db_path=db, kb=(kb, kb), store_root=tmp_path / "coach"),
                       prefix="/api/plugins/coach")
    return TestClient(app), kb, llm, core.Store(tmp_path / "coach")


def post(c, path, **body):
    r = c.post(f"/api/plugins/coach{path}", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def log(tmp_path):
    return [json.loads(x) for x in (tmp_path / "coach" / "log.jsonl").read_text().splitlines()]


def test_still_broken_third_turn_offers_restart_clean(tmp_path):
    c, kb, llm, st = client(tmp_path, make_db(tmp_path))
    st.user_turn("s1", "still not working")
    st.user_turn("s1", "same error")
    assert post(c, "/state", session_id="s1")["nudge"] is None
    st.user_turn("s1", "nope, didn't work")
    n = post(c, "/state", session_id="s1")["nudge"]
    assert n["rule"] == "still_broken" and n["button"] == "Restart clean" and n["action"] == "restart"
    assert log(tmp_path)[-1] == {**log(tmp_path)[-1], "event": "shown", "rule": "still_broken"}
    r = post(c, "/restart", session_id="s1")
    assert r["title"] == "Fix login" and "@session:default/s1" in r["message"] and "## Done when" in r["message"]
    assert "MODE: RESTART" in llm.calls[0][0]["content"].upper()


def test_long_session_over_250_msgs_offers_spin_out_across_compression(tmp_path):
    db = make_db(tmp_path, n_user=30, n_assistant=30, compressed_parent=True)  # 200 + 60 msgs over the lineage
    c, *_ = client(tmp_path, db)
    out = post(c, "/state", session_id="s1")
    assert out["msgs"] == 260 and out["nudge"]["rule"] == "long_session" and out["nudge"]["action"] == "spin_out"


def test_tenth_subagent_or_long_job_offers_move_to_board(tmp_path):
    c, kb, _, st = client(tmp_path, make_db(tmp_path, n_user=1, n_assistant=1))
    st.add_subagents("s1", 9)
    assert post(c, "/state", session_id="s1")["nudge"] is None
    st.add_subagents("s1", 1)
    n = post(c, "/state", session_id="s1")["nudge"]
    assert n["rule"] == "move_to_board" and "10 subagents" in n["text"]
    out = post(c, "/board", session_id="s1")
    assert out["cards"] == [{"title": "Fix login", "task_id": "t_1"}]
    kw = kb.created[0]
    assert kw["created_by"] == "coach" and kw["session_id"] == "s1" and "@session:default/s1" in kw["body"]
    assert kw["idempotency_key"].startswith("coach-board:s1:")


def test_long_job_triggers_board_nudge(tmp_path):
    db = make_db(tmp_path, n_user=1, n_assistant=1, sid="s2")
    c, *_rest, st = client(tmp_path, db)
    st.long_job("s2", "terminal ran 12 min")
    n = post(c, "/state", session_id="s2")["nudge"]
    assert n["rule"] == "move_to_board" and "terminal ran 12 min" in n["text"]


def test_same_hermes_bug_in_two_sessions_offers_file_it_once(tmp_path):
    c, kb, _, st = client(tmp_path, make_db(tmp_path, n_user=1, n_assistant=1))
    sig = "cross_session: chat is open in a window"
    st.bug("s1", sig, "sample one")
    st.bug("s1", sig, "again, same session")
    assert post(c, "/state", session_id="s1")["nudge"] is None  # one session is not a pattern
    st.bug("other", sig, "sample two")
    n = post(c, "/state", session_id="s1")["nudge"]
    assert n["rule"] == "hermes_bug" and n["button"] == "File it once" and n["data"]["sig"] == sig
    out = post(c, "/file-bug", session_id="s1", sig=sig)
    assert out["cards"][0]["task_id"] == "t_1" and kb.created[0]["board"] == "hermes"
    assert "@session:default/other" in kb.created[0]["body"]
    assert core.bug_hits(st, ["s1"]) is None  # filed: never offered again


def test_old_bugs_outside_the_week_do_not_count(tmp_path):
    st = core.Store(tmp_path / "coach", clock=lambda: 1_000_000.0)
    st.bug("a", "sig", "")
    later = core.Store(tmp_path / "coach", clock=lambda: 1_000_000.0 + 8 * 86400)
    later.bug("b", "sig", "")
    assert core.bug_hits(later, ["b"]) is None


def test_screenshot_offer_and_budget_of_one_per_15_turns(tmp_path):
    c, _, _, st = client(tmp_path, make_db(tmp_path, n_user=5, n_assistant=5))
    first = post(c, "/offer", session_id="s1", rule="screenshot_midturn")
    assert first["nudge"]["rule"] == "screenshot_midturn" and first["nudge"]["button"] == "Add a line"
    st.add_subagents("s1", 12)
    assert post(c, "/state", session_id="s1")["nudge"] is None  # budget: 5 - 5 < 15
    db = make_db(tmp_path, n_user=15, n_assistant=0)  # 20 user turns now
    c2, *_ = client(tmp_path, db)
    assert post(c2, "/state", session_id="s1")["nudge"]["rule"] == "move_to_board"


def test_peer_panels_share_the_budget(tmp_path):
    c, _, _, st = client(tmp_path, make_db(tmp_path, n_user=4, n_assistant=0))
    post(c, "/event", session_id="s1", rule="topic-shift", event="shown", turn=4)
    st.add_subagents("s1", 10)
    assert post(c, "/state", session_id="s1")["nudge"] is None
    assert log(tmp_path)[-1]["peer"] is True


def test_dismissal_mutes_rule_for_the_session(tmp_path):
    c, _, _, st = client(tmp_path, make_db(tmp_path, n_user=1, n_assistant=0))
    n = post(c, "/offer", session_id="s1", rule="screenshot_midturn")["nudge"]
    post(c, "/event", session_id="s1", rule="screenshot_midturn", event="dismissed", id=n["id"])
    assert "screenshot_midturn" in post(c, "/state", session_id="s1")["muted"]
    st._update("s1", lambda s: s.pop("shown_turn"))  # free the budget: still muted
    assert post(c, "/offer", session_id="s1", rule="screenshot_midturn")["nudge"] is None
    assert log(tmp_path)[-1]["choice"] == "dismissed"


def test_three_dismissals_in_a_week_demote_rule_to_digest_only(tmp_path):
    c, _, _, st = client(tmp_path, make_db(tmp_path, n_user=1, n_assistant=0))
    for s in ("a", "b", "c"):
        st.record(s, "move_to_board", "dismissed")
    st.add_subagents("s1", 10)
    out = post(c, "/state", session_id="s1")
    assert out["nudge"] is None and out["demoted"] == ["move_to_board"]
    assert log(tmp_path)[-1]["event"] == "digest" and log(tmp_path)[-1]["rule"] == "move_to_board"
    post(c, "/state", session_id="s1")
    assert sum(1 for r in log(tmp_path) if r["event"] == "digest") == 1  # logged once per instance


def test_accept_is_logged_and_stats_report_precision(tmp_path):
    c, _, _, st = client(tmp_path, make_db(tmp_path, n_user=1, n_assistant=0))
    n = post(c, "/offer", session_id="s1", rule="screenshot_midturn")["nudge"]
    post(c, "/event", session_id="s1", rule="screenshot_midturn", event="accepted", id=n["id"])
    r = c.get("/api/plugins/coach/stats").json()
    assert r["rules"]["screenshot_midturn"] == {**r["rules"]["screenshot_midturn"], "shown": 1, "accepted": 1,
                                                "precision": 1.0}
    assert post(c, "/event", session_id="s1", rule="x", event="bogus")["ok"] is False


def test_each_instance_shows_once(tmp_path):
    c, _, _, st = client(tmp_path, make_db(tmp_path, n_user=1, n_assistant=0))
    st.add_subagents("s1", 10)
    assert post(c, "/state", session_id="s1")["nudge"]["rule"] == "move_to_board"
    st._update("s1", lambda s: s.pop("shown_turn"))
    assert post(c, "/state", session_id="s1")["nudge"] is None


def test_expired_show_neither_mutes_nor_counts_against_precision(tmp_path):
    c, _, _, st = client(tmp_path, make_db(tmp_path, n_user=1, n_assistant=0))
    n = post(c, "/offer", session_id="s1", rule="screenshot_midturn")["nudge"]
    post(c, "/event", session_id="s1", rule="screenshot_midturn", event="expired", id=n["id"])
    assert post(c, "/state", session_id="s1")["muted"] == []
    r = c.get("/api/plugins/coach/stats").json()["rules"]["screenshot_midturn"]
    assert r["expired"] == 1 and r["precision"] is None
