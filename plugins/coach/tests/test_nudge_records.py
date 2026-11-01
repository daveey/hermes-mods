"""Every reply_nudge ends with exactly one delivery record and exactly one outcome record, tied to it by nudge_id.

Delivery records: reply_nudge_delivered | reply_nudge_undelivered | reply_nudge_abandoned.
Outcome records: reply_nudge_outcome | nudge_outcome_lost | nudge_outcome_subagent | nudge_outcome_untracked |
nudge_outcome_missing.

Before this, about 4% of real nudges left neither (hub + MacBook coach logs, 10-29..10-31): the pending nudge lived
in one in-memory slot and was closed only by a later pre_verify, so these paths left it open forever:
- a context compaction inside the continuation restarts core's verify attempts, so the continuation's reply reached
  pre_verify as attempt 0 of the same turn and was taken for "no continuation yet";
- a notice (or any new user turn) superseded the continuation and that turn never reached pre_verify;
- the turn failed or the worker exited;
- the backend never checked the session again (process exit, plugin reload);
- a second nudge in the same session overwrote the first one's slot, and compute-envelope records carried the
  generic rule, so a delivery could not be tied to its nudge.
Synthetic strings only (public repo).
"""
import importlib.util
import json
import sqlite3
import sys
import types
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[1]
REPLY = "I patched the parser.\n\nWant me to run the full suite now?"  # synthetic: prefilter `confirm` candidate
REVISED = "I ran the full suite: 12 passed."
VERDICT = {"handoff": 0.0, "confirm": 0.95, "unverified": 0.0, "unwatched": 0.0, "report_only": 0.0,
           "evidence": "asks permission for a reversible step"}
DELIVERY = ("reply_nudge_delivered", "reply_nudge_undelivered", "reply_nudge_abandoned")
OUTCOME = ("reply_nudge_outcome", "nudge_outcome_lost", "nudge_outcome_subagent", "nudge_outcome_untracked",
           "nudge_outcome_missing")

pytest.importorskip("hermes_cli.plugins")


@pytest.fixture
def coach(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    for k, v in {"COACH_REPLY_RULES": "confirm", "COACH_SYSTEM_ONE": "off", "COACH_REPORT_ONLY_CONFIRM": "0",
                 "COACH_DIFF_REVIEW": "0"}.items():
        monkeypatch.setenv(k, v)
    import agent.auxiliary_client as aux

    def model(task=None, messages=None, timeout=None, **_):
        msg = types.SimpleNamespace(content=json.dumps(VERDICT))
        return types.SimpleNamespace(choices=[types.SimpleNamespace(message=msg)])

    monkeypatch.setattr(aux, "call_llm", model)
    spec = importlib.util.spec_from_file_location("coach_init_nudge_records", HERE / "__init__.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    store = mod._load("core").Store(root=tmp_path / "coach")
    c, hooks = mod.build(store=store, checkout=None)
    log_path = tmp_path / "coach" / "log.jsonl"

    def events(name=None):
        rows = [json.loads(ln) for ln in log_path.read_text().splitlines()] if log_path.exists() else []
        return [r for r in rows if name is None or r.get("event") == name]
    return c, hooks, events


def _records(events, nudge):
    """(delivery events, outcome events) logged for this nudge, matched by its nudge_id only."""
    nid = nudge.get("nudge_id")
    assert nid, f"reply_nudge carries no nudge_id: {nudge}"
    rows = [r for r in events() if r.get("nudge_id") == nid]
    return [r for r in rows if r["event"] in DELIVERY], [r for r in rows if r["event"] in OUTCOME]


def _nudge(hooks, sid, text=REPLY, attempt=0):
    out = hooks["pre_verify"](session_id=sid, platform="cli", attempt=attempt, final_response=text,
                              changed_paths=["parser.py"])
    assert out and out["action"] == "continue", out
    return out


def test_compaction_inside_the_continuation(coach):
    """Core restarts its verify attempts after a compaction: the continuation's reply arrives as attempt 0 of the
    same turn. It is the continuation (a different reply), so the nudge is delivered and its outcome judged."""
    _c, hooks, events = coach
    hooks["pre_llm_call"](session_id="s1", user_message="fix the parser")
    _nudge(hooks, "s1")
    # compaction lands here; the continuation's final reply reaches pre_verify as attempt 0
    assert hooks["pre_verify"](session_id="s1", platform="cli", attempt=0, final_response=REVISED,
                               changed_paths=["parser.py"]) is None
    hooks["post_llm_call"](session_id="s1", assistant_response=REVISED, platform="cli")
    [n] = events("reply_nudge")
    d, o = _records(events, n)
    assert [r["event"] for r in d] == ["reply_nudge_delivered"], d
    assert [r["event"] for r in o] == ["reply_nudge_outcome"], o
    assert d[0]["rule"] == o[0]["rule"] == "confirm" and o[0]["resolved"] is True


def test_same_draft_rechecked_is_not_a_continuation(coach):
    """Control for the test above: the same draft checked again at attempt 0 is not yet the continuation."""
    _c, hooks, events = coach
    hooks["pre_llm_call"](session_id="s1b", user_message="fix the parser")
    _nudge(hooks, "s1b")
    [n] = events("reply_nudge")
    hooks["pre_verify"](session_id="s1b", platform="cli", attempt=0, final_response=REPLY, changed_paths=["p.py"])
    first = [r for r in events("reply_nudge") if r["nudge_id"] == n["nudge_id"]]
    d, _o = _records(events, first[0])
    assert d == []


def test_notice_supersedes_the_continuation(coach):
    """A notice turn starts before the continuation is checked, and that turn never reaches pre_verify."""
    _c, hooks, events = coach
    hooks["pre_llm_call"](session_id="s2", user_message="fix the parser")
    _nudge(hooks, "s2")
    hooks["pre_llm_call"](session_id="s2", user_message="[Kanban notice] card t_x finished")
    hooks["post_llm_call"](session_id="s2", assistant_response="Noted.", platform="cli")
    [n] = events("reply_nudge")
    d, o = _records(events, n)
    assert [(r["event"], r.get("reason")) for r in d] == [("reply_nudge_undelivered", "next_turn")], d
    assert [(r["event"], r.get("reason")) for r in o] == [("nudge_outcome_lost", "turn_changed")], o


def test_failed_turn_after_the_nudge(coach):
    _c, hooks, events = coach
    hooks["pre_llm_call"](session_id="s3", user_message="fix the parser")
    _nudge(hooks, "s3")
    hooks["post_llm_call"](session_id="s3", platform="cli",
                           assistant_response="This turn did not complete. The provider returned an error.")
    [n] = events("reply_nudge")
    d, o = _records(events, n)
    assert [(r["event"], r.get("reason")) for r in d] == [("reply_nudge_undelivered", "turn_failed")], d
    assert [(r["event"], r.get("reason")) for r in o] == [("nudge_outcome_lost", "turn_failed")], o
    # the next turn's check adds nothing for the closed nudge
    hooks["pre_llm_call"](session_id="s3", user_message="try again")
    hooks["pre_verify"](session_id="s3", platform="cli", attempt=0, final_response=REVISED, changed_paths=["p.py"])
    d2, o2 = _records(events, n)
    assert len(d2) == 1 and len(o2) == 1


def test_normal_continuation_gets_one_record_each(coach):
    """Regression control: the ordinary path still yields delivered + outcome once, and turn end adds nothing."""
    _c, hooks, events = coach
    hooks["pre_llm_call"](session_id="s4", user_message="fix the parser")
    _nudge(hooks, "s4")
    hooks["pre_verify"](session_id="s4", platform="cli", attempt=1, final_response=REVISED, changed_paths=["p.py"])
    hooks["post_llm_call"](session_id="s4", assistant_response=REVISED, platform="cli")
    hooks["pre_llm_call"](session_id="s4", user_message="thanks")
    [n] = events("reply_nudge")
    d, o = _records(events, n)
    assert [r["event"] for r in d] == ["reply_nudge_delivered"] and [r["event"] for r in o] == ["reply_nudge_outcome"]


AUTH_TEXT = "I will rent 4 GPUs concurrently."  # compute_authority hit (synthetic)
ENVELOPE_TEXT = "Should I rent a single A100 GPU for about $6 total to run the eval?"  # compute_envelope hit


def test_two_nudges_in_one_session_with_a_compaction_between(tmp_path, monkeypatch):
    """A compute_authority nudge (reply gate), a compaction, then a compute_envelope nudge on the continuation's
    reply: each nudge gets its own delivery and outcome record, under its own id and its own rule."""
    sys.path.insert(0, str(HERE / "tests"))
    from test_compute_envelope import _hooks, _log
    hooks = _hooks(tmp_path, monkeypatch)
    hooks["pre_llm_call"](session_id="s5", user_message="run the eval")
    held = hooks["pre_final_gate:compute_envelope"](session_id="s5", platform="cli", final_response=AUTH_TEXT)
    assert held and held["hold"]
    # compaction inside the continuation; its reply reaches pre_verify as attempt 0 and is itself held
    out = hooks["pre_verify"](session_id="s5", platform="cli", attempt=0, final_response=ENVELOPE_TEXT)
    assert out and out["action"] == "continue"
    hooks["pre_verify"](session_id="s5", platform="cli", attempt=1, final_response="I will run the eval on CPU.")
    hooks["post_llm_call"](session_id="s5", assistant_response="I will run the eval on CPU.", platform="cli")
    log = _log(tmp_path)
    nudges = [r for r in log if r["event"] == "reply_nudge"]
    assert [n["rule"] for n in nudges] == ["compute_authority", "compute_envelope"]
    assert len({n.get("nudge_id") for n in nudges}) == 2

    def events(name=None):
        return [r for r in _log(tmp_path) if name is None or r.get("event") == name]
    for n in nudges:
        d, o = _records(events, n)
        assert len(d) == 1 and d[0]["event"] == "reply_nudge_delivered" and d[0]["rule"] == n["rule"], (n, d)
        assert len(o) == 1 and o[0]["event"] == "nudge_outcome_untracked" and o[0]["rule"] == n["rule"], (n, o)


# ── the backstop: a nudge whose backend never checks the session again (process exit, plugin reload) ──

def _signals():
    spec = importlib.util.spec_from_file_location("coach_signals_nudge_records", HERE / "signals.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["coach_signals_nudge_records"] = mod
    spec.loader.exec_module(mod)
    return mod


NOW = 1792660000.0
SID = "local-synthetic01"
BASE = {"ts": NOW, "event": "reply_nudge", "session_id": SID, "rule": "report_only", "p": 0.9,
        "evidence": "leaves verification pending", "draft_id": "d1", "nudge_id": "n_test0001", "outcome": True}


def _home(tmp_path, rows, messages=()):
    h = tmp_path / "home"
    (h / "coach").mkdir(parents=True)
    (h / "coach" / "log.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    c = sqlite3.connect(h / "state.db")
    c.execute("create table messages (id integer primary key, session_id text, role text, content text, "
              "tool_calls text, timestamp real, active integer default 1, compacted integer default 0)")
    for role, content, tool_calls, dt in messages:
        c.execute("insert into messages (session_id, role, content, tool_calls, timestamp) values (?,?,?,?,?)",
                  (SID, role, content, tool_calls, NOW + dt))
    c.commit()
    c.close()
    return h


def _for(h, nid):
    rows = [json.loads(ln) for ln in (h / "coach" / "log.jsonl").read_text().splitlines()]
    rows = [r for r in rows if r.get("nudge_id") == nid]
    return [r for r in rows if r["event"] in DELIVERY], [r for r in rows if r["event"] in OUTCOME]


def test_never_checked_again_is_closed_by_the_sweep(tmp_path):
    sg = _signals()
    h = _home(tmp_path, [BASE])
    cur: dict = {}
    sg.collect_coach(h, cur, NOW + sg.HANDOFF_GRACE_S + 1)  # files "no outcome" as before; the turn may still run
    d, o = _for(h, "n_test0001")
    assert d == [] and o == []
    sg.collect_coach(h, cur, NOW + sg.NUDGE_OPEN_S + 1)
    d, o = _for(h, "n_test0001")
    assert [(r["event"], r.get("reason")) for r in d] == [("reply_nudge_undelivered", "never_rechecked")], d
    assert [(r["event"], r.get("reason")) for r in o] == [("nudge_outcome_missing", "never_rechecked")], o
    sg.collect_coach(h, cur, NOW + sg.NUDGE_OPEN_S + 7200)
    assert tuple(map(len, _for(h, "n_test0001"))) == (1, 1)


def test_sweep_closes_a_lost_turn_at_the_grace(tmp_path):
    sg = _signals()
    h = _home(tmp_path, [BASE], [("assistant", "", '[{"name": "terminal"}]', 30),
                                 ("assistant", "Operation interrupted: waiting for model response (6.9s elapsed).",
                                  None, 226)])
    cur: dict = {}
    sg.collect_coach(h, cur, NOW + sg.HANDOFF_GRACE_S + 1)
    d, o = _for(h, "n_test0001")
    assert [(r["event"], r.get("reason")) for r in d] == [("reply_nudge_undelivered", "interrupted")], d
    assert [(r["event"], r.get("reason")) for r in o] == [("nudge_outcome_lost", "interrupted")], o
    sg.collect_coach(h, cur, NOW + sg.NUDGE_OPEN_S + 1)
    assert tuple(map(len, _for(h, "n_test0001"))) == (1, 1)


def test_sweep_leaves_a_closed_nudge_alone(tmp_path):
    sg = _signals()
    rows = [BASE, {"ts": NOW + 40, "event": "reply_nudge_delivered", "session_id": SID, "rule": "report_only",
                   "nudge_id": "n_test0001"},
            {"ts": NOW + 45, "event": "nudge_outcome_lost", "session_id": SID, "rule": "report_only",
             "reason": "turn_failed", "nudge_id": "n_test0001"}]
    h = _home(tmp_path, rows)
    cur: dict = {}
    sigs = sg.collect_coach(h, cur, NOW + sg.HANDOFF_GRACE_S + 1)
    assert [s for s in sigs if s["kind"] == "coach"] == []  # an in-process lost record is not an ignored nudge
    sg.collect_coach(h, cur, NOW + sg.NUDGE_OPEN_S + 1)
    assert tuple(map(len, _for(h, "n_test0001"))) == (1, 1)
