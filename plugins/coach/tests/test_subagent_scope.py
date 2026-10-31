"""report_only / status_only do not judge a subagent's report to its parent (self-heal agent-ee1b1162bc).

A delegate_task child finished its brief ("fix the UI, run targeted tests, do not commit, report changed paths and
results, avoid unrelated paths"). Its report named a browser test that was red before its change and said another
worker owned some unrelated edits. That is the handoff the brief asked for: the reader is the parent agent, which
acted on it. The coach held the report as report_only twice (unverified_owner, then the revision), and the failed
outcome filed a self-heal defect. The fix: subagent sessions (platform/state.db source 'subagent') skip report_only
and status_only in the reply check, and a nudge outcome from a subagent session never becomes a self-heal signal.
The other rules (handoff, credit_ask, compute_envelope, unverified, ...) still judge subagents.
"""
import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]

ASK = ("Fix the decision UI in the user-steps panel. Run the targeted tests. Do not commit. Report the changed paths "
       "and the results. Avoid unrelated paths.")
# The child's report, shape kept: the deterministic unverified_owner phrase sits in its ending.
REPORT = ("Changed `desktop-plugins/user-steps/panel.js` and its test. Targeted tests: 14 passed.\n\n"
          "The browser test `panel.browser.test.mjs` was already red before my change (same failure on the base); "
          "I haven't filed it with any owner. The session-mentions edits in the tree are not mine: "
          "another worker owns that.")


def _state_db(home: Path, sid: str, source: str):
    home.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(home / "state.db")
    c.execute("create table if not exists sessions (id text primary key, source text not null, "
              "parent_session_id text, started_at real)")
    c.execute("create table if not exists messages (id integer primary key, session_id text, role text, "
              "content text, tool_calls text, timestamp real, active integer default 1, compacted integer default 0)")
    c.execute("insert or replace into sessions (id, source, parent_session_id, started_at) values (?,?,?,?)",
              (sid, source, "parent-1" if source == "subagent" else None, 1.0))
    c.commit()
    c.close()


def _hooks(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.delenv("COACH_REPLY_RULES", raising=False)
    spec = importlib.util.spec_from_file_location(f"coach_init_sub_{tmp_path.name}", HERE / "__init__.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    core = mod._load("core")
    rcl = mod._load("reply_check")
    monkeypatch.setattr(rcl, "_hermes_home", lambda: home)
    monkeypatch.setattr(rcl, "aux_judge", lambda s, p: json.dumps(
        {"handoff": 0, "confirm": 0, "unverified": 0, "unwatched": 0, "report_only": 0.97, "status_only": 0,
         "evidence": "names problems and leaves them"}))
    monkeypatch.setattr(rcl, "aux_confirm", lambda s, p: json.dumps({"p": 0.97, "evidence": "left it"}))
    store = core.Store(root=tmp_path / "coach")
    _, hooks = mod.build(store=store, checkout=None)
    return hooks, home, tmp_path / "coach" / "log.jsonl"


def _events(logf):
    return [json.loads(l) for l in logf.read_text().splitlines()] if logf.exists() else []


def test_subagent_report_by_state_db_source_gets_no_report_only_nudge(tmp_path, monkeypatch):
    hooks, home, logf = _hooks(tmp_path, monkeypatch)
    sid = "20261030_033213_66f5c0"
    _state_db(home, sid, "subagent")
    hooks["pre_llm_call"](session_id=sid, user_message=ASK, platform="cli")
    out = hooks["pre_verify"](session_id=sid, platform="cli", attempt=0, final_response=REPORT)
    assert out is None
    assert not any(r["event"] == "reply_nudge" and r.get("rule") in ("report_only", "status_only")
                   for r in _events(logf))


def test_subagent_report_by_platform_gets_no_report_only_nudge(tmp_path, monkeypatch):
    hooks, home, logf = _hooks(tmp_path, monkeypatch)
    sid = "child-no-db-row"
    hooks["pre_llm_call"](session_id=sid, user_message=ASK, platform="subagent")
    out = hooks["pre_verify"](session_id=sid, platform="subagent", attempt=0, final_response=REPORT)
    assert out is None
    assert not any(r["event"] == "reply_nudge" for r in _events(logf))


def test_same_report_in_a_user_session_still_nudges(tmp_path, monkeypatch):
    hooks, home, logf = _hooks(tmp_path, monkeypatch)
    sid = "20261030_040000_user01"
    _state_db(home, sid, "cli")
    hooks["pre_llm_call"](session_id=sid, user_message=ASK, platform="cli")
    out = hooks["pre_verify"](session_id=sid, platform="cli", attempt=0, final_response=REPORT)
    assert out and out["action"] == "continue"
    nudges = [r for r in _events(logf) if r["event"] == "reply_nudge"]
    assert nudges and nudges[0]["rule"] == "report_only" and nudges[0].get("reason") == "unverified_owner"


def test_subagent_still_judged_by_the_other_rules(tmp_path, monkeypatch):
    hooks, home, logf = _hooks(tmp_path, monkeypatch)
    sid = "child-credit"
    _state_db(home, sid, "subagent")
    rcl = sys.modules.get("reply_check")
    reply = ("Blocked: the Anthropic API says \"credit balance is too low\". Please add credits to the account "
             "so I can finish.")
    hooks["pre_llm_call"](session_id=sid, user_message=ASK, platform="subagent")
    out = hooks["pre_verify"](session_id=sid, platform="subagent", attempt=0, final_response=reply)
    assert out and out["action"] == "continue"
    assert any(r["event"] == "reply_nudge" and r["rule"] == "credit_ask" for r in _events(logf))


def test_is_subagent_reads_source_and_fails_open(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("rc_sub_is", HERE / "reply_check.py")
    rc = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rc)
    home = tmp_path / "h"
    monkeypatch.setattr(rc, "_hermes_home", lambda: home)
    assert rc.is_subagent("s1") is False  # no state.db
    _state_db(home, "s1", "subagent")
    _state_db(home, "s2", "desktop")
    assert rc.is_subagent("s1") is True
    assert rc.is_subagent("s2") is False
    assert rc.is_subagent("missing") is False
    assert rc.is_subagent("") is False


# --- signals: a nudge outcome from a subagent session never files a self-heal defect ---------------------------

def _signals():
    spec = importlib.util.spec_from_file_location("coach_signals_sub_test", HERE / "signals.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["coach_signals_sub_test"] = mod
    spec.loader.exec_module(mod)
    return mod


NOW = 1792660000.0


def _coach_home(tmp_path, sid, source, outcome=True):
    h = tmp_path / "home"
    (h / "coach").mkdir(parents=True)
    _state_db(h, sid, source)
    nudge = {"ts": NOW, "event": "reply_nudge", "session_id": sid, "rule": "report_only", "p": 1.0,
             "evidence": "another worker owns that", "reason": "unverified_owner", "draft_id": "d1", "outcome": True}
    lines = [json.dumps(nudge)]
    if outcome:
        lines.append(json.dumps({"ts": NOW + 30, "event": "reply_nudge_outcome", "rule": "report_only",
                                 "session_id": sid, "draft_id": "d1", "p": 0.9, "resolved": False, "judged": True,
                                 "evidence": "another worker owns that", "reply": "x"}))
    (h / "coach" / "log.jsonl").write_text("\n".join(lines) + "\n")
    return h


def _coach_sigs(sigs):
    return [s for s in sigs if s["kind"] == "coach" and "report_only" in s.get("symptom", "")]


def test_failed_report_only_outcome_of_a_subagent_files_no_signal(tmp_path):
    sg = _signals()
    h = _coach_home(tmp_path, "20261030_033213_66f5c0", "subagent")
    assert _coach_sigs(sg.collect_coach(h, {}, NOW + 60)) == []
    logged = [json.loads(l) for l in (h / "coach" / "log.jsonl").read_text().splitlines()]
    assert any(r.get("event") == "nudge_outcome_subagent" and r["session_id"] == "20261030_033213_66f5c0"
               for r in logged)


def test_nudge_record_platform_subagent_is_enough_without_a_db_row(tmp_path):
    sg = _signals()
    assert sg.subagent_session(tmp_path, {"session_id": "x", "platform": "subagent"}) is True
    assert sg.subagent_session(tmp_path, {"session_id": "x", "platform": "cli"}) is False


def test_no_outcome_report_only_nudge_of_a_subagent_files_no_signal(tmp_path):
    sg = _signals()
    h = _coach_home(tmp_path, "child-2", "subagent", outcome=False)
    cur = {}
    sg.collect_coach(h, cur, NOW + 60)
    assert _coach_sigs(sg.collect_coach(h, cur, NOW + sg.HANDOFF_GRACE_S + 120)) == []


def test_failed_report_only_outcome_of_a_user_session_still_files(tmp_path):
    sg = _signals()
    h = _coach_home(tmp_path, "20261030_040000_user01", "desktop")
    got = _coach_sigs(sg.collect_coach(h, {}, NOW + 60))
    assert len(got) == 1 and "still failed the coach report_only check" in got[0]["symptom"]
