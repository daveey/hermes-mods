"""pre_verify through the host's bounded hook dispatch (core PluginManager), with a synthetic slow model.

Reproduces the shape of the MacBook incident (host "Hook 'pre_verify' callback timed out after 30s", then
"skipped after previous timeout" on the next check; a reply_nudge logged after the host had given up) with
scaled-down timings and synthetic text only: the coach's reply check must fit the host's deadline, an abandoned
check must never log a nudge as sent, and a nudge returned in time is marked delivered once the agent continues.
"""
import importlib.util
import json
import logging
import threading
import time
import types
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[1]
REPLY = "I patched the parser.\n\nWant me to run the full suite now?"  # synthetic: prefilter `confirm` candidate
REVISED = "I ran the full suite: 12 passed."
VERDICT = {"handoff": 0.0, "confirm": 0.95, "unverified": 0.0, "unwatched": 0.0, "report_only": 0.0,
           "evidence": "asks permission for a reversible step"}

plugins = pytest.importorskip("hermes_cli.plugins")
HAS_CALL_BUDGETS = hasattr(plugins, "current_hook_call")


class SlowModel:
    """Stands in for agent.auxiliary_client.call_llm: answers after `latency` seconds. A model that honours its
    timeout raises TimeoutError at it (like the real client); `honours_timeout=False` simulates a call that hangs
    past it."""

    def __init__(self, latency: float, honours_timeout: bool = True):
        self.latency, self.honours_timeout = latency, honours_timeout
        self.calls: list = []
        self.finished = threading.Event()

    def __call__(self, task=None, messages=None, timeout=None, **_):
        self.calls.append({"task": task, "timeout": timeout})
        try:
            if self.honours_timeout and timeout is not None and self.latency > timeout:
                time.sleep(timeout)
                raise TimeoutError(f"synthetic model timed out after {timeout}s")
            time.sleep(self.latency)
            msg = types.SimpleNamespace(content=json.dumps(VERDICT))
            return types.SimpleNamespace(choices=[types.SimpleNamespace(message=msg)])
        finally:
            self.finished.set()


@pytest.fixture
def host(tmp_path, monkeypatch):
    """A real PluginManager running only the coach's pre_verify, under a scaled host timeout."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    for k, v in {"COACH_REPLY_RULES": "confirm", "COACH_SYSTEM_ONE": "off", "COACH_REPORT_ONLY_CONFIRM": "0",
                 "COACH_DIFF_REVIEW": "0", "COACH_PRE_VERIFY_MARGIN_S": "0.05",
                 "COACH_PRE_VERIFY_MIN_CALL_S": "0.05"}.items():
        monkeypatch.setenv(k, v)
    import agent.auxiliary_client as aux

    def make(global_timeout: float, budget: float, slack: float, model: SlowModel):
        monkeypatch.setenv("COACH_PRE_VERIFY_BUDGET_S", str(budget))
        monkeypatch.setenv("COACH_PRE_VERIFY_SLACK_S", str(slack))
        monkeypatch.setattr(aux, "call_llm", model)
        monkeypatch.setattr("hermes_cli.plugins._resolve_hook_callback_timeout", lambda: global_timeout)
        spec = importlib.util.spec_from_file_location(f"coach_init_deadline_{id(model)}", HERE / "__init__.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        store = mod._load("core").Store(root=tmp_path / "coach")
        _coach, hooks = mod.build(store=store, checkout=None)
        mgr = plugins.PluginManager()
        mgr._discovered = True
        mgr._hooks["pre_verify"] = [hooks["pre_verify"]]
        monkeypatch.setattr(plugins, "_plugin_manager", mgr)
        log_path = tmp_path / "coach" / "log.jsonl"

        def events(name=None):
            rows = [json.loads(ln) for ln in log_path.read_text().splitlines()] if log_path.exists() else []
            return [r for r in rows if name is None or r.get("event") == name]
        return hooks, mgr, events
    return make


def _agent(session_id: str):
    return types.SimpleNamespace(session_id=session_id, platform="cli", model="m", _resolved_is_coding=True,
                                 _turn_file_mutation_paths={"parser.py"})


def _wait_for(pred, secs=5.0):
    end = time.monotonic() + secs
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.02)
    return False


def test_slow_model_fits_the_host_deadline_and_the_next_turn_is_not_skipped(host, caplog):
    """RED before: the judge kept its 60s timeout, the host abandoned the callback at its deadline, and the next
    turn's check was skipped ("skipped after previous timeout or while still running")."""
    model = SlowModel(latency=3.0)
    hooks, mgr, events = host(global_timeout=0.6, budget=0.4, slack=0.1, model=model)
    with caplog.at_level(logging.WARNING, logger="hermes_cli.plugins"):
        hooks["pre_llm_call"](session_id="s1", user_message="fix the parser")
        t0 = time.monotonic()
        assert mgr.invoke_hook("pre_verify", session_id="s1", attempt=0, final_response=REPLY,
                               changed_paths=["parser.py"]) == []
        assert time.monotonic() - t0 < 0.6
        hooks["pre_llm_call"](session_id="s1", user_message="and the lexer")
        mgr.invoke_hook("pre_verify", session_id="s1", attempt=0, final_response=REPLY, changed_paths=["lexer.py"])
    assert len(model.calls) == 2, "the next turn's check must run, not be skipped"
    assert all(c["timeout"] <= 0.4 for c in model.calls), model.calls
    assert "timed out" not in caplog.text and "skipped" not in caplog.text
    time.sleep(0.3)
    assert events("reply_nudge") == []


def test_abandoned_check_never_claims_a_nudge(host, caplog):
    """RED before: the abandoned worker logged reply_nudge after the host had discarded its result."""
    model = SlowModel(latency=0.9, honours_timeout=False)  # a model call that overruns its timeout
    hooks, mgr, events = host(global_timeout=0.4, budget=0.3, slack=0.05, model=model)
    hooks["pre_llm_call"](session_id="s2", user_message="fix the parser")
    with caplog.at_level(logging.WARNING, logger="hermes_cli.plugins"):
        assert mgr.invoke_hook("pre_verify", session_id="s2", attempt=0, final_response=REPLY,
                               changed_paths=["parser.py"]) == []
        assert model.finished.wait(5.0)
        assert _wait_for(lambda: events("reply_nudge_abandoned"))
    assert events("reply_nudge") == []
    rec = events("reply_nudge_abandoned")[0]
    assert rec["rule"] == "confirm" and rec["session_id"] == "s2" and rec["elapsed_s"] >= 0.4
    assert "timed out" in caplog.text


def test_normal_nudge_returns_within_the_deadline_and_the_continuation_is_observed(host):
    """The agent loop's own stop gate gets the nudge in time, and the continuation marks it delivered."""
    from agent.turn_stop_gates import _pre_verify_nudge
    model = SlowModel(latency=0.05)
    hooks, _mgr, events = host(global_timeout=0.6, budget=0.4, slack=0.1, model=model)
    agent = _agent("s3")
    hooks["pre_llm_call"](session_id="s3", user_message="fix the parser")
    t0 = time.monotonic()
    msg = _pre_verify_nudge(agent, REPLY, 0)
    assert msg and "proceed on reversible work" in msg
    assert time.monotonic() - t0 < 0.6
    nudge = events("reply_nudge")
    assert len(nudge) == 1 and nudge[0]["rule"] == "confirm" and nudge[0]["elapsed_s"] < 0.6
    assert events("reply_nudge_delivered") == []
    assert _pre_verify_nudge(agent, REVISED, 1) is None  # the continuation's stop
    delivered = events("reply_nudge_delivered")
    assert len(delivered) == 1 and delivered[0]["rule"] == "confirm" and delivered[0]["session_id"] == "s3"


def test_nudge_without_a_continuation_is_undelivered(host):
    model = SlowModel(latency=0.05)
    hooks, mgr, events = host(global_timeout=0.6, budget=0.4, slack=0.1, model=model)
    hooks["pre_llm_call"](session_id="s4", user_message="fix the parser")
    out = mgr.invoke_hook("pre_verify", session_id="s4", attempt=0, final_response=REPLY, changed_paths=["p.py"])
    assert out and out[0]["action"] == "continue"
    hooks["pre_llm_call"](session_id="s4", user_message="next thing")  # the continuation never came
    mgr.invoke_hook("pre_verify", session_id="s4", attempt=0, final_response=REVISED, changed_paths=["p.py"])
    assert [r["rule"] for r in events("reply_nudge_undelivered")] == ["confirm"]
    assert events("reply_nudge_delivered") == []


@pytest.mark.skipif(not HAS_CALL_BUDGETS, reason="core without per-callback hook budgets")
def test_declared_budget_lets_a_slower_check_finish_past_the_global_timeout(host, caplog):
    """On a core that honours `hermes_hook_timeout`, the coach's explicit budget (not the 30s global) bounds it."""
    model = SlowModel(latency=0.5)
    hooks, mgr, events = host(global_timeout=0.25, budget=1.0, slack=0.2, model=model)
    hooks["pre_llm_call"](session_id="s5", user_message="fix the parser")
    with caplog.at_level(logging.WARNING, logger="hermes_cli.plugins"):
        out = mgr.invoke_hook("pre_verify", session_id="s5", attempt=0, final_response=REPLY, changed_paths=["p.py"])
    assert out and out[0]["action"] == "continue"
    assert "timed out" not in caplog.text
    assert events("reply_nudge")[0]["host_timeout_s"] == pytest.approx(1.2)


def test_budget_caps_calls_and_refuses_a_call_too_short_to_finish():
    spec = importlib.util.spec_from_file_location("coach_rc_budget", HERE / "reply_check.py")
    rc = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rc)
    now = [100.0]
    clock = lambda: now[0]  # noqa: E731
    host = types.SimpleNamespace(timeout=30.0, deadline=130.0, abandoned=threading.Event())
    b = rc.Budget(75.0, host=host, clock=clock)
    b.margin, b.min_call = 2.0, 5.0
    assert b.call_timeout(60) == 28.0  # the host's 30s deadline, not the 60s cap or the 75s budget
    now[0] = 124.0
    with pytest.raises(rc.BudgetExhausted):
        b.call_timeout(60)
    assert not b.abandoned()
    host.abandoned.set()
    assert b.abandoned() and b.remaining() == 0.0
    old_core = rc.Budget(75.0, host_timeout_s=30.0, clock=clock)  # core without current_hook_call
    old_core.margin, old_core.min_call = 2.0, 5.0
    assert old_core.call_timeout(90) == 28.0
    assert rc.call_timeout(90) == 90.0  # outside pre_verify: unchanged
