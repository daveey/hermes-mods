"""The reply-check judge runs with thinking off, on the model it was tuned on (chat latency round 3, t_01a1d000efb592cc).

pre_verify waits on this call before the turn ends. Without a reasoning_config, adaptive-thinking Claude models think by
default: on hermes-hub that took the judge from p50 2.79 s to 5.05 s on the same prompt, and the report_only thresholds
were tuned with thinking disabled (eval/report_only_eval.py --thinking disabled is the default).

`class:standard` is a weighted allocation that also served gpt-6.1-sol, which cannot turn reasoning off (5.0-7.5 s per
call against 2.1-2.8 s for Sonnet on the same prompt), so the screen pins JUDGE_MODEL (the eval's --model) and falls back
to the configured route when that pin is unavailable.
"""
import importlib.util
import sys
import types
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent.parent


def _rc():
    spec = importlib.util.spec_from_file_location("rc_judge_reasoning", HERE / "reply_check.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _fake_aux(monkeypatch, calls, fail_models=(), task_config=None):
    def call_llm(**kw):
        calls.append(kw)
        if kw.get("model") in fail_models:
            raise RuntimeError("pin rejected: no eligible route")
        msg = types.SimpleNamespace(content=' {"handoff": 0} ')
        return types.SimpleNamespace(choices=[types.SimpleNamespace(message=msg)])
    agent = types.ModuleType("agent")
    aux = types.ModuleType("agent.auxiliary_client")
    aux.call_llm = call_llm
    aux._get_auxiliary_task_config = lambda task: dict(task_config or {"model": "class:standard", "provider": "auto"})
    agent.auxiliary_client = aux
    monkeypatch.setitem(sys.modules, "agent", agent)
    monkeypatch.setitem(sys.modules, "agent.auxiliary_client", aux)


@pytest.fixture(autouse=True)
def _no_env_override(monkeypatch):
    monkeypatch.delenv("COACH_REPLY_JUDGE_MODEL", raising=False)


def test_reply_judge_disables_thinking(monkeypatch):
    rc = _rc()
    calls = []
    _fake_aux(monkeypatch, calls)
    assert rc.aux_judge("SYS", "PROMPT") == '{"handoff": 0}'
    (kw,) = calls
    assert kw["task"] == "coach_reply"
    assert kw["reasoning_config"] == {"enabled": False}
    assert kw["max_tokens"] == 2000 and kw["temperature"] == 0


def test_each_call_gets_its_own_reasoning_dict(monkeypatch):
    """call_llm may normalise the dict in place; the module default must stay disabled for the next turn."""
    rc = _rc()
    calls = []
    _fake_aux(monkeypatch, calls)
    rc.aux_judge("S", "P")
    calls[0]["reasoning_config"]["enabled"] = True
    rc.aux_judge("S", "P")
    assert calls[1]["reasoning_config"] == {"enabled": False}
    assert rc.JUDGE_REASONING == {"enabled": False}


def test_report_only_second_opinion_is_unchanged(monkeypatch):
    """Only the in-turn screen changes; the capped second opinion keeps its route default."""
    rc = _rc()
    calls = []
    _fake_aux(monkeypatch, calls)
    rc.aux_confirm("S", "P")
    assert calls[0]["task"] == "coach_report_only" and "reasoning_config" not in calls[0]
    assert "model" not in calls[0]


def test_class_route_pins_the_tuned_model(monkeypatch):
    """A class (or auto/unset) coach_reply route runs the screen on the eval's model, not the class allocation."""
    rc = _rc()
    assert rc.JUDGE_MODEL == "claude-sonnet-5-5"
    for cfg in ({"model": "class:standard"}, {"model": "auto"}, {"model": ""}):
        calls = []
        _fake_aux(monkeypatch, calls, task_config=cfg)
        rc.aux_judge("S", "P")
        (kw,) = calls
        assert kw["model"] == "claude-sonnet-5-5", cfg
        assert kw["reasoning_config"] == {"enabled": False}


def test_unavailable_pin_falls_back_to_the_route(monkeypatch):
    """No eligible Sonnet route (another host, a quota wall): the check still runs on the configured route."""
    rc = _rc()
    calls = []
    _fake_aux(monkeypatch, calls, fail_models=("claude-sonnet-5-5",))
    assert rc.aux_judge("S", "P") == '{"handoff": 0}'
    assert [c.get("model") for c in calls] == ["claude-sonnet-5-5", None]
    assert calls[1]["reasoning_config"] == {"enabled": False} and calls[1]["task"] == "coach_reply"


def test_exhausted_budget_is_not_retried(monkeypatch):
    """Out of pre_verify budget is no verdict; it must not start a second call on the route."""
    rc = _rc()
    calls = []
    _fake_aux(monkeypatch, calls)

    def exhausted(cap):
        raise rc.BudgetExhausted("0.5s left of the pre_verify budget")
    monkeypatch.setattr(rc, "call_timeout", exhausted)
    with pytest.raises(rc.BudgetExhausted):
        rc.aux_judge("S", "P")
    assert calls == []


def test_concrete_configured_model_is_honoured(monkeypatch):
    """An explicit auxiliary.coach_reply.model is the owner's choice; the pin never overrides it."""
    rc = _rc()
    calls = []
    _fake_aux(monkeypatch, calls, task_config={"model": "claude-haiku-5-5", "provider": "anthropic"})
    rc.aux_judge("S", "P")
    assert "model" not in calls[0]


def test_env_override(monkeypatch):
    rc = _rc()
    calls = []
    _fake_aux(monkeypatch, calls)
    monkeypatch.setenv("COACH_REPLY_JUDGE_MODEL", "")
    rc.aux_judge("S", "P")
    monkeypatch.setenv("COACH_REPLY_JUDGE_MODEL", "claude-opus-5-5")
    rc.aux_judge("S", "P")
    assert "model" not in calls[0] and calls[1]["model"] == "claude-opus-5-5"
