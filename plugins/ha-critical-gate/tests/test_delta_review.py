"""Exact-head follow-up regressions for case, indirect targets and gate environment."""
import json
from types import SimpleNamespace

import pytest

from test_review_regressions import gate


@pytest.mark.parametrize("target", ["Cover.garage_door", "SCRIPT.unlock_front", "Switch.door_relay"])
@pytest.mark.parametrize("location", ["entity_id", "data", "json_data"])
@pytest.mark.parametrize("wrapper", [False, True])
@pytest.mark.parametrize("failed_gate", [False, True])
def test_mixed_case_targets_fail_closed(target, location, wrapper, failed_gate):
    call = {"domain": "homeassistant", "service": "toggle"}
    if location == "entity_id":
        call["entity_id"] = target
    else:
        data = {"entity_id": target}
        call["data"] = json.dumps(data) if location == "json_data" else data
    def fail(*args, **kwargs):
        raise OSError("offline")
    config = {"command": ["unused"]} if failed_gate else {}
    args = {"calls": [{"name": "ha_call_service", "arguments": call}]} if wrapper else call
    assert gate.classify("mcp__tool_call" if wrapper else "ha_call_service", args, config=config, run=fail)


@pytest.mark.parametrize("domain,service", [("HOMEASSISTANT", "TOGGLE"), ("Script", "turn_on"),
    ("LOCK", "UNLOCK"), ("Cover", "OPEN_COVER"), ("alarm_control_panel", "ALARM_DISARM")])
def test_mixed_case_service_selectors_fail_closed(domain, service):
    assert gate.decide({"domain": domain, "service": service, "entity_id": "cover.garage"}, config={})


@pytest.mark.parametrize("key", ["floor_id", "area_id", "device_id", "label_id", "future_selector"])
@pytest.mark.parametrize("failed_gate", [False, True])
@pytest.mark.parametrize("wrapper", [False, True])
def test_indirect_generic_targets_fail_closed(key, failed_gate, wrapper):
    call = {"domain": "homeassistant", "service": "toggle",
            "data": {"entity_id": "light.kitchen", key: "ground"}}
    def fail(*args, **kwargs):
        raise OSError("offline")
    args = {"calls": [{"name": "ha_call_service", "arguments": call}]} if wrapper else call
    assert gate.classify("tool_call" if wrapper else "ha_call_service", args,
                         config={"command": ["unused"]} if failed_gate else {}, run=fail)


def test_gate_uses_minimal_environment_and_trusted_cwd(monkeypatch):
    monkeypatch.setenv("LD_PRELOAD", "/agent/evil.so")
    monkeypatch.setenv("PYTHONPATH", "/agent/modules")
    monkeypatch.setenv("HOME", "/agent/home")
    seen = {}
    def run(*args, **kwargs):
        seen.update(kwargs)
        return SimpleNamespace(returncode=0, stdout='{"decision":"allow"}')
    gate.ask_gate({"domain": "light", "service": "turn_on"}, {"command": ["/trusted/gate"]}, run=run)
    assert seen.get("env") == {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"}
    assert seen.get("cwd") == "/"


def test_safe_explicit_generic_target_with_exclude_stays_available():
    assert gate.decide({"domain": "homeassistant", "service": "turn_on",
                        "data": {"entity_id": "Light.kitchen", "exclude": "switch.relay"}}, config={}) is None
