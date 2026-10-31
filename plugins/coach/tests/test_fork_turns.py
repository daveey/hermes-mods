"""Fork turns (background review, side question) are not replies to the user (agent-0fe2ee028b).

A background review fork shares its parent's session_id. Its private output ("Nothing to save. There is one
lesson worth keeping...") was judged by pre_verify as the reply, logged as a reply_nudge, and parked as the
session's pending nudge; the next real turn then closed it as reply_nudge_undelivered and self-heal filed it.
A call carrying `turn_origin` must log nothing and leave the session's loop state untouched.
"""
import importlib.util
import json
import types
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[1]
REPLY = "I patched the parser.\n\nWant me to run the full suite now?"  # synthetic: prefilter `confirm` candidate
VERDICT = {"handoff": 0.0, "confirm": 0.95, "unverified": 0.0, "unwatched": 0.0, "report_only": 0.0,
           "evidence": "asks permission for a reversible step"}

pytest.importorskip("hermes_cli.plugins")


@pytest.fixture
def coach(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    for k, v in {"COACH_REPLY_RULES": "confirm", "COACH_SYSTEM_ONE": "off", "COACH_REPORT_ONLY_CONFIRM": "0",
                 "COACH_DIFF_REVIEW": "0"}.items():
        monkeypatch.setenv(k, v)
    import agent.auxiliary_client as aux

    calls = []

    def model(task=None, messages=None, timeout=None, **_):
        calls.append(task)
        msg = types.SimpleNamespace(content=json.dumps(VERDICT))
        return types.SimpleNamespace(choices=[types.SimpleNamespace(message=msg)])

    monkeypatch.setattr(aux, "call_llm", model)
    spec = importlib.util.spec_from_file_location("coach_init_fork_turns", HERE / "__init__.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    store = mod._load("core").Store(root=tmp_path / "coach")
    c, hooks = mod.build(store=store, checkout=None)
    log_path = tmp_path / "coach" / "log.jsonl"

    def events(name=None):
        rows = [json.loads(ln) for ln in log_path.read_text().splitlines()] if log_path.exists() else []
        return [r for r in rows if name is None or r.get("event") == name]
    return c, hooks, events, calls


def test_fork_turn_is_not_judged_and_keeps_the_pending_nudge(coach):
    c, hooks, events, calls = coach
    hooks["pre_llm_call"](session_id="s1", user_message="fix the parser")
    # The parent's real reply is nudged: a pending nudge is parked for its continuation.
    assert hooks["pre_verify"](session_id="s1", platform="cli", attempt=0, final_response=REPLY,
                               changed_paths=["parser.py"])
    before = json.dumps(c.loop_state("s1"), sort_keys=True, default=str)
    assert "pending_nudge" in c.loop_state("s1")
    n_events, n_calls = len(events()), len(calls)

    # The review fork ends on the same session with its own private output.
    assert hooks["pre_verify"](session_id="s1", platform="cli", attempt=0, final_response=REPLY,
                               changed_paths=[], turn_origin="background_review") is None
    assert len(calls) == n_calls, "a fork turn must not be judged"
    assert len(events()) == n_events, events()[n_events:]
    assert events("reply_nudge_undelivered") == [] and events("reply_nudge_delivered") == []
    assert json.dumps(c.loop_state("s1"), sort_keys=True, default=str) == before


def test_fork_turn_alone_logs_no_nudge(coach):
    c, hooks, events, calls = coach
    hooks["pre_llm_call"](session_id="s2", user_message="fix the parser")
    assert hooks["pre_verify"](session_id="s2", platform="cli", attempt=0, final_response=REPLY,
                               changed_paths=["parser.py"], turn_origin="background_review") is None
    assert events("reply_nudge") == [] and calls == []
    assert "pending_nudge" not in c.loop_state("s2")


def test_envelope_gate_skips_fork_turn(coach):
    _c, hooks, events, _calls = coach
    gate = hooks["pre_final_gate:compute_envelope"]
    ask = "Should I rent a single A100 GPU for about $6 total to run the eval?"
    assert gate(session_id="s3", platform="cli", final_response=ask, attempt=0,
                turn_origin="background_review") is None
    assert events() == []
    # Control: the same text on a user turn is held, so the fork assertion above is not vacuous.
    held = gate(session_id="s3", platform="cli", final_response=ask, attempt=0)
    assert isinstance(held, dict) and held.get("hold") is True, held
    assert [e["rule"] for e in events("reply_nudge")] == ["compute_envelope"]
