"""A pinned route's short subscription wall is a designed hold, not a defect (selfheal agent-13b91b3e1b).

Core policy (agent/model_routing_walls.py): a wall resetting within WALL_REROUTE_MIN_SECONDS (6 h) holds every pin,
since waiting beats a provider switch; the turn gets ``held_reply`` and gateway.run logs "Model resolution failed"
with the SubscriptionQuotaUnavailable traceback. Fingerprints are keyed on exception + frames, so that designed hold
reopened the fingerprint of two real defects (a far wall that never rerouted; the @-reference lookup with no routing
session) as "came back" (hub 2026-11-14 06:26:36Z, MacBook 05:52Z: walls 2.9-4.3 h out). A far wall and a wall
already past its reset at log time stay defects.
"""
import importlib.util
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


sg = load("coach_signals_held_wall_test", ROOT / "signals.py")

AT = "2026-11-14 06:26:36,292"
AT_EPOCH = sg._ts(AT[:19])  # host-local, like the collector (the hub logs UTC: 1794637596)


def record(reset: float, msg: str = "Model resolution failed for session agent:main:local:dm:local-be7f1d: "):
    """The hub's errors.log record of 2026-11-14 06:26:36Z, verbatim apart from the reset epoch."""
    exc = f"Subscription quota unavailable for anthropic/claude-opus-5-5; resets at {reset}"
    return [
        f"{AT} WARNING gateway.run: {msg}{exc}",
        "Traceback (most recent call last):",
        '  File "~/.hermes/hermes-agent/gateway/run_turn_runner.py", line 1455, in _run_sync_scoped',
        "    model, runtime_kwargs = runner._resolve_session_agent_runtime(",
        '  File "~/.hermes/hermes-agent/gateway/run_turn_prepare.py", line 60, in _resolve_policy_agent_runtime',
        "    runtime, fallback_entry = route_runtime(policy, frozen, model, route_launch_key(authority, policy, frozen),",
        '  File "~/.hermes/hermes-agent/gateway/session_worker_construct.py", line 248, in _route_runtime',
        "    return resolve_runtime_with_fallback(frozen, requested=provider,",
        '  File "~/.hermes/hermes-agent/hermes_cli/runtime_provider.py", line 1103, in resolve_runtime_provider',
        "    return resolve_managed_runtime(target_model, requested, config=managed,",
        '  File "~/.hermes/hermes-agent/agent/model_routing_managed.py", line 112, in resolve_managed_runtime',
        '    return resolve_class_runtime("class:" + level, "auto", config=restricted, session_id=session_id,',
        '  File "~/.hermes/hermes-agent/agent/model_routing_runtime.py", line 152, in resolve_class_runtime',
        "    raise min(unavailable, key=lambda error: error.resets_at) from None",
        f"agent.model_routing_unavailable.SubscriptionQuotaUnavailable: {exc}",
    ]


def test_hub_short_wall_hold_is_not_a_signal():
    # the real record: wall reset 1794647999.77 = 2.9 h after the UTC log line 1794637596
    assert sg.parse_errors(record(AT_EPOCH + (1794647999.772869 - 1794637596))) == []


@pytest.mark.parametrize("hours", [0.05, 1, 3.45, 4.33, 5.99])
def test_any_wall_under_the_reroute_threshold_holds(hours):
    assert sg.parse_errors(record(AT_EPOCH + hours * 3600)) == []


@pytest.mark.parametrize("msg", ["Agent error in session agent:aux:api_server:dm:api-462f: ",
                                 "@ context reference expansion failed: "])
def test_short_wall_hold_on_other_callers_is_not_a_signal(msg):
    assert sg.parse_errors(record(AT_EPOCH + 3 * 3600, msg)) == []


@pytest.mark.parametrize("hours", [6.0, 17.1, 107.3, 142.7])
def test_far_wall_refusal_stays_a_defect(hours):
    # generation 1/2 of this fingerprint: a far wall that refused instead of rerouting (or of the @-lookup)
    out = sg.parse_errors(record(AT_EPOCH + hours * 3600))
    assert len(out) == 1 and out[0]["symptom"].startswith("SubscriptionQuotaUnavailable in resolve_class_runtime")


@pytest.mark.parametrize("delta", [-1.0, -3600.0])
def test_wall_already_past_its_reset_stays_a_defect(delta):
    # a refusal after the reset is a stale wall: a real defect
    assert len(sg.parse_errors(record(AT_EPOCH + delta))) == 1


def test_unparseable_reset_stays_a_defect():
    lines = record(AT_EPOCH + 3600)
    lines[0] = lines[0].replace(f"resets at {AT_EPOCH + 3600}", "resets soon")
    lines[-1] = lines[-1].replace(f"resets at {AT_EPOCH + 3600}", "resets soon")
    assert len(sg.parse_errors(lines)) == 1


def test_other_exception_with_a_reset_text_stays_a_defect():
    lines = record(AT_EPOCH + 3600)
    lines[-1] = lines[-1].replace("agent.model_routing_unavailable.SubscriptionQuotaUnavailable",
                                  "agent.model_routing.ModelRoutingError")
    assert len(sg.parse_errors(lines)) == 1


def queued_row(at: float, reset: float, model="anthropic/claude-haiku-5-5"):
    """Shape of the two rows the hub queued before the fix (agent-8ba8f528c8 mini, agent-b0c73fab15 MacBook)."""
    return {"component": "agent", "kind": "errors", "source": "signal", "at": at, "host": "mini",
            "fingerprint": "agent-8ba8f528c8", "profile": "default", "quote": "", "session_id": "",
            "symptom": "SubscriptionQuotaUnavailable in resolve_class_runtime (model_routing_runtime.py): Agent error "
                       "in session agent:aux:api_server:dm:api-1467",
            "details": "(seen 4x this pass)\n    raise min(unavailable, key=lambda error: error.resets_at) from None\n"
                       "agent.model_routing_unavailable.SubscriptionQuotaUnavailable: Subscription quota unavailable "
                       f"for {model}; resets at {reset}"}


def test_queued_short_wall_row_is_withdrawn_by_recheck():
    # mini row: at 1794670255, reset 1794672000.16 (0.48 h)
    rc = sg.defect_recheck(queued_row(1794670255.0, 1794672000.1597881))
    assert rc and rc["kind"] == "held_wall" and sg.recheck_exempts(rc).startswith("held wall: ")


@pytest.mark.parametrize("left", [-60.0, 6 * 3600.0, 100 * 3600.0])
def test_queued_far_or_stale_wall_row_still_files(left):
    rc = sg.defect_recheck(queued_row(1794670255.0, 1794670255.0 + left))
    assert rc is None or sg.recheck_exempts(rc) is None


def test_queued_other_traceback_row_has_no_recheck():
    d = queued_row(1794670255.0, 1794672000.0)
    d["details"] = d["details"].replace("agent.model_routing_unavailable.SubscriptionQuotaUnavailable",
                                        "agent.model_routing.ModelRoutingError")
    assert sg.defect_recheck(d) is None


def test_threshold_matches_core_reroute_policy():
    """Drift guard: the hold window must be core's WALL_REROUTE_MIN_SECONDS (a longer core window would make
    designed holds file again; a shorter one would hide far walls that failed to reroute)."""
    import os
    try:  # scripts/test-all puts core on PYTHONPATH (HERMES_CORE_CHECKOUT); the public CI has no core
        from agent.model_routing_walls import WALL_REROUTE_MIN_SECONDS
    except ImportError:
        if os.environ.get("HERMES_CORE_CHECKOUT"):
            raise
        pytest.skip("needs Hermes core (HERMES_CORE_CHECKOUT unset)")
    assert WALL_REROUTE_MIN_SECONDS == sg.HELD_WALL_S
