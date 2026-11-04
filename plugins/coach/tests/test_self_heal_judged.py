"""self-heal: every judged complaint turn leaves a row, and a silent judge opens a card (plugin-89461de0ed).

The hub's complaint detector filed nothing from 2026-10-28 to 11-04 while David kept chatting, and nothing showed
it: the judge ran in a background thread, `_ask` turned every failure into "no opinion", and the hook's exceptions
went to debug logging only. An outage looked exactly like "no complaints". These tests pin:
  (a) a judge that raises (or answers nothing) yields a `selfheal_judged` row with outcome error / no_opinion, plus a
      `selfheal_judge_error` row; an exception inside the hook body is a row too, not only a debug line;
  (b) the hub's liveness check files one defect when user turns happened but no judgment did (or every judgment
      failed), and stays quiet when judgments exist;
  (c) a real hit still logs `selfheal_detect`, now with its `selfheal_judged outcome=hit` row.
"""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


sh = load("coach_self_heal_judged_test", ROOT / "self_heal.py")
core = load("coach_core_test_judged", ROOT / "core.py")
plugin = load("coach_plugin_test_judged", ROOT / "__init__.py")

NOW = 1793590440.0  # 2026-11-01 22:54Z (Nov 1, 7:14 PM PT is 02:14Z on Nov 2; any fixed instant works)
SID = "20261101_224008_13f44a"
PREV = "The twitter test suite is passing on both machines now. I re-ran all 747 tests on the hub just now."
ASK = "cancel the card. figure out why it posted but didnt' tell me"


def events(store, name):
    p = Path(store.root) / "log.jsonl"
    return [r for r in (json.loads(x) for x in p.read_text().splitlines())
            if r.get("event") == name] if p.exists() else []


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("HERMES_KANBAN_TASK", raising=False)
    monkeypatch.setenv("COACH_SELFHEAL", "shadow")
    return core.Store(tmp_path / "home" / "coach")


def hooks(store, llm, confirm=None):
    _, h = plugin.build(store=store, checkout=None, selfheal_sync=True, selfheal_llm=llm, selfheal_confirm=confirm)
    return h


def turn(h, msg=ASK, platform="desktop"):
    h["pre_llm_call"](session_id=SID, user_message=msg, platform=platform,
                      conversation_history=[{"role": "assistant", "content": PREV}])


# ── (a) failures are rows, not silence ──
def test_a_judge_that_raises_logs_an_error_row(store):
    def boom(system, prompt):
        raise RuntimeError("no auxiliary route for coach_defect")
    turn(hooks(store, boom))
    (row,) = events(store, "selfheal_judged")
    assert row["outcome"] == "error" and row["session_id"] == SID and row["platform"] == "desktop"
    assert "no auxiliary route for coach_defect" in row["error"]
    (err,) = events(store, "selfheal_judge_error")
    assert err["stage"] == "s1" and "RuntimeError" in err["error"] and err["session_id"] == SID
    assert events(store, "selfheal_detect") == []


def test_a_judge_that_answers_nothing_is_no_opinion(store):
    turn(hooks(store, lambda s, p: ""))
    (row,) = events(store, "selfheal_judged")
    assert row["outcome"] == "no_opinion" and "empty" in row["error"]
    assert events(store, "selfheal_judge_error")[0]["stage"] == "s1"


def test_a_below_threshold_logs_score_and_stages(store):
    turn(hooks(store, lambda s, p: json.dumps({"p_defect": 0.1, "kind": "none", "component": "agent",
                                                  "symptom": "x"})))
    (row,) = events(store, "selfheal_judged")
    assert row["outcome"] == "below" and row["score"] == 0.0 and row["stages"]
    assert events(store, "selfheal_judge_error") == []


def test_a_prefilter_miss_is_a_row(store):
    turn(hooks(store, lambda s, p: pytest.fail("judge must not run")), msg="thanks")
    (row,) = events(store, "selfheal_judged")
    assert row["outcome"] == "prefiltered"


def test_a_exception_in_the_hook_body_is_a_row(store, monkeypatch):
    def broken(*a, **k):
        raise ValueError("handle exploded")
    live = plugin._load("self_heal")  # the hook's cached sibling module
    monkeypatch.setattr(live, "handle", broken)
    turn(hooks(store, lambda s, p: json.dumps({"p_defect": 0.95, "kind": "agent_fault", "component": "agent",
                                                  "symptom": "posted without telling the user"})))
    errs = [r for r in events(store, "selfheal_judged") if r["outcome"] == "error"]
    assert errs and "handle exploded" in errs[-1]["error"]


def test_a_kanban_env_on_a_user_platform_is_a_visible_skip(store, monkeypatch):
    """A leaked HERMES_KANBAN_TASK in a gateway process would mute every chat; that must show as a row."""
    monkeypatch.setenv("HERMES_KANBAN_TASK", "t_leaked")
    turn(hooks(store, lambda s, p: pytest.fail("judge must not run")))
    (row,) = events(store, "selfheal_judged")
    assert row["outcome"] == "skipped" and row["why"] == "kanban_env"


def test_a_kanban_platform_turn_logs_nothing(store):
    turn(hooks(store, lambda s, p: pytest.fail("judge must not run")), platform="kanban")
    assert events(store, "selfheal_judged") == []


# ── a backend still running an older coach __init__ (hot-reloaded self_heal.py only) ──
def test_a_older_init_still_gets_a_row_via_handle(store):
    def boom(system, prompt):
        raise RuntimeError("aux route down")
    hit = sh.detect_complaint(PREV, ASK, llm=boom, threshold=sh.threshold())  # old call shape: no trace
    sh.handle(hit, SID, store, evidence=ASK, platform="desktop", ask=ASK)
    (row,) = events(store, "selfheal_judged")
    assert row["outcome"] == "error" and "aux route down" in row["error"] and row["platform"] == "desktop"
    sh.handle(None, SID, store)  # the hand-off is consumed once
    assert len(events(store, "selfheal_judged")) == 1


# ── root cause: the judge thread must inherit the turn's context (profile secret scope) ──
def _wait_rows(store, n=1, timeout=10.0):
    import time as _t
    end = _t.time() + timeout
    while _t.time() < end:
        rows = events(store, "selfheal_judged")
        if len(rows) >= n:
            return rows
        _t.sleep(0.05)
    return events(store, "selfheal_judged")


def test_root_background_judge_sees_the_turns_context(store):
    """The hub's multiplexed backend binds each profile's secrets in a ContextVar; a bare threading.Thread starts
    with an empty context, so every aux judge call raised UnscopedSecretError (live probe 2026-11-04)."""
    import contextvars
    scope = contextvars.ContextVar("scope_under_test", default=None)

    def llm(system, prompt):
        if scope.get() is None:
            raise RuntimeError("UnscopedSecretError: could not read this profile's ANTHROPIC_TOKEN")
        return json.dumps({"p_defect": 0.96, "kind": "agent_fault", "component": "agent",
                           "symptom": "posted without telling the user"})
    _, h = plugin.build(store=store, checkout=None, selfheal_sync=False, selfheal_llm=llm, selfheal_confirm=None)
    tok = scope.set({"ANTHROPIC_TOKEN": "x"})
    try:
        turn(h)
    finally:
        scope.reset(tok)
    (row,) = _wait_rows(store)
    assert row["outcome"] == "hit", row


def test_root_background_judge_reads_core_secret_scope(store):
    secret_scope = pytest.importorskip("agent.secret_scope")

    def llm(system, prompt):
        assert secret_scope.get_secret("ANTHROPIC_TOKEN") == "tok-under-test"
        return json.dumps({"p_defect": 0.96, "kind": "agent_fault", "component": "agent",
                           "symptom": "posted without telling the user"})
    _, h = plugin.build(store=store, checkout=None, selfheal_sync=False, selfheal_llm=llm, selfheal_confirm=None)
    mux = secret_scope.set_multiplex_context(True)
    tok = secret_scope.set_secret_scope({"ANTHROPIC_TOKEN": "tok-under-test"})
    try:
        turn(h)
    finally:
        secret_scope.reset_secret_scope(tok)
        secret_scope.reset_multiplex_context(mux)
    (row,) = _wait_rows(store)
    assert row["outcome"] == "hit", row


# ── (c) a real hit ──
def test_c_real_hit_logs_detect_and_a_hit_row(store):
    llm = lambda s, p: json.dumps({"p_defect": 0.96, "kind": "agent_fault", "component": "agent",
                                   "symptom": "posted without telling the user"})
    turn(hooks(store, llm, confirm=None))
    (row,) = events(store, "selfheal_judged")
    assert row["outcome"] == "hit" and row["score"] >= 0.6 and row["fingerprint"]
    (det,) = events(store, "selfheal_detect")
    assert det["source"] == "complaint" and det["fingerprint"] == row["fingerprint"]


# ── (b) liveness ──
def judged(store, outcome, at, n=1):
    for _ in range(n):
        store.clock = lambda at=at: at
        store.log({"event": "selfheal_judged", "outcome": outcome, "session_id": SID})


class Filer:
    def __init__(self):
        self.calls = []

    def __call__(self, d, store=None, m=None, **_):
        self.calls.append(d)
        return {"action": "filed", "card": "t_00000001", "fingerprint": d["fingerprint"]}


def test_b_liveness_fires_on_zero_judgments_with_live_turns(store):
    f = Filer()
    out = sh.judge_liveness(store, now=NOW, turns_fn=lambda since: 412, filer=f)
    (d,) = f.calls
    assert d["component"] == "plugin" and d["source"] == "signal" and "412" in d["quote"]
    assert out["action"] == "filed"
    (row,) = events(store, "selfheal_liveness")
    assert row["turns"] == 412 and row["judged"] == 0 and row["alarm"] == "no_judgments"


def test_b_liveness_fires_when_every_judge_call_failed(store):
    judged(store, "error", NOW - 3600, n=3)
    judged(store, "no_opinion", NOW - 1800, n=2)
    judged(store, "prefiltered", NOW - 600, n=40)
    f = Filer()
    sh.judge_liveness(store, now=NOW, turns_fn=lambda since: 100, filer=f)
    assert len(f.calls) == 1
    assert events(store, "selfheal_liveness")[-1]["alarm"] == "judge_failing"


def test_b_liveness_quiet_when_judgments_exist(store):
    judged(store, "prefiltered", NOW - 600, n=30)
    judged(store, "below", NOW - 300)
    f = Filer()
    out = sh.judge_liveness(store, now=NOW, turns_fn=lambda since: 412, filer=f)
    assert f.calls == [] and out["action"] == "ok"


def test_b_liveness_quiet_with_few_turns_or_unknown_turns(store):
    f = Filer()
    assert sh.judge_liveness(store, now=NOW, turns_fn=lambda since: 20, filer=f)["action"] == "ok"
    assert sh.judge_liveness(store, now=NOW, turns_fn=lambda since: None, filer=f)["action"] == "unknown"
    assert f.calls == []


def test_b_old_judgments_do_not_count(store):
    judged(store, "below", NOW - 2 * 86400, n=5)
    f = Filer()
    sh.judge_liveness(store, now=NOW, turns_fn=lambda since: 50, filer=f)
    assert len(f.calls) == 1


def test_b_one_incident_one_fingerprint(store):
    f = Filer()
    sh.judge_liveness(store, now=NOW, turns_fn=lambda since: 50, filer=f)
    sh.judge_liveness(store, now=NOW + 3600, turns_fn=lambda since: 60, filer=f)
    assert len({d["fingerprint"] for d in f.calls}) == 1


def test_b_cron_runs_liveness_hourly(store, tmp_path):
    calls = []
    lf = lambda store, now=None: calls.append(now) or {"action": "ok"}
    sh.cron(tmp_path, store, now=NOW, backstop_fn=lambda *a: [], liveness_fn=lf)
    sh.cron(tmp_path, store, now=NOW + 600, backstop_fn=lambda *a: [], liveness_fn=lf)
    sh.cron(tmp_path, store, now=NOW + 3600, backstop_fn=lambda *a: [], liveness_fn=lf)
    assert calls == [NOW, NOW + 3600]


def test_b_user_turns_since_counts_user_facing_sessions_only(tmp_path):
    import sqlite3
    db = sqlite3.connect(tmp_path / "state.db")
    db.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY, source TEXT, parent_session_id TEXT, started_at REAL, "
               "last_activity_at REAL)")
    db.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT, role TEXT, content TEXT, "
               "timestamp REAL)")
    for sid, src, parent in (("d1", "desktop", None), ("k1", "kanban", None), ("s1", "desktop", "d1"),
                             ("c1", "cron", None), ("t1", "telegram", None)):
        db.execute("INSERT INTO sessions VALUES (?,?,?,?,?)", (sid, src, parent, NOW - 100, NOW - 10))
        for i in range(3):
            db.execute("INSERT INTO messages (session_id, role, content, timestamp) VALUES (?,?,?,?)",
                       (sid, "user", "hi", NOW - 50 + i))
    db.execute("INSERT INTO messages (session_id, role, content, timestamp) VALUES ('d1','user','old',?)",
               (NOW - 3 * 86400,))
    db.commit()
    db.close()
    assert sh.user_turns_since(NOW - 86400, home=tmp_path) == 6
