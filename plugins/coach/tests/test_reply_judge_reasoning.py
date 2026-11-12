"""The reply-check judge runs with thinking off (chat latency round 3, t_01a1d000efb592cc).

pre_verify waits on this call before the turn ends. Without a reasoning_config, adaptive-thinking Claude models think by
default: on hermes-hub that took the judge from p50 2.79 s to 5.05 s on the same prompt, and the report_only thresholds
were tuned with thinking disabled (eval/report_only_eval.py --thinking disabled is the default).
"""
import importlib.util
import sys
import types
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent


def _rc():
    spec = importlib.util.spec_from_file_location("rc_judge_reasoning", HERE / "reply_check.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _fake_aux(monkeypatch, calls):
    def call_llm(**kw):
        calls.append(kw)
        msg = types.SimpleNamespace(content=' {"handoff": 0} ')
        return types.SimpleNamespace(choices=[types.SimpleNamespace(message=msg)])
    agent = types.ModuleType("agent")
    aux = types.ModuleType("agent.auxiliary_client")
    aux.call_llm = call_llm
    agent.auxiliary_client = aux
    monkeypatch.setitem(sys.modules, "agent", agent)
    monkeypatch.setitem(sys.modules, "agent.auxiliary_client", aux)


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
