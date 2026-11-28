"""No-gate HA-R1 and wrapper LOW regressions; no Home Assistant calls."""
import importlib.util
import json
import os
from pathlib import Path

import pytest

SOURCE = Path(os.environ.get("HA_GATE_SOURCE", Path(__file__).parents[1] / "__init__.py"))
spec = importlib.util.spec_from_file_location("ha_default_deny", SOURCE)
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)

REFUSALS = [
    {"domain": "zwave_js", "service": "set_value", "data": {"device_id": "abc", "command_class": 98, "property": "targetMode", "value": 255}},
    {"domain": "mqtt", "service": "publish", "data": {"topic": "zigbee2mqtt/front_door/set", "payload": '{"state":"UNLOCK"}'}},
    {"domain": "zha", "service": "issue_zigbee_cluster_command", "data": {"ieee": "00:11", "endpoint_id": 1, "cluster_id": 257, "command": 1, "command_type": "server"}},
    {"domain": "valve", "service": "open_valve", "entity_id": "valve.main"},
    {"domain": "acme", "service": "open_door"},
    {"domain": "lock", "service": "unknown"},
    {"domain": "cover", "service": "unknown"},
    {"domain": "alarm_control_panel", "service": "unknown"},
]
CONTROLS = [
    {"domain": "light", "service": "turn_on", "entity_id": "light.kitchen"},
    {"domain": "light", "service": "turn_on", "data": {"area_id": "kitchen"}},
    {"domain": "media_player", "service": "media_play"},
    {"domain": "climate", "service": "set_temperature", "data": {"temperature": 20}},
    {"domain": "lock", "service": "lock", "entity_id": "lock.front"},
    *[{"domain": "cover", "service": service, "entity_id": "cover.garage"}
      for service in ("close_cover", "stop_cover", "close_cover_tilt", "stop_cover_tilt")],
    *[{"domain": "alarm_control_panel", "service": service}
      for service in ("alarm_arm_home", "alarm_arm_away", "alarm_arm_night")],
    {"domain": "homeassistant", "service": "turn_on", "entity_id": "light.kitchen"},
]
UNLOCK = {"domain": "lock", "service": "unlock"}

@pytest.mark.parametrize("call", REFUSALS, ids=lambda c: c["domain"])
def test_default_deny(call):
    assert gate.classify("ha_call_service", call, config={})

@pytest.mark.parametrize("call", CONTROLS, ids=lambda c: c["domain"] + "." + c["service"])
def test_no_gate_controls(call):
    assert gate.classify("ha_call_service", call, config={}) is None


def wrappers(call):
    return [
        {"calls": [{"name": "ha_call_service", "arguments": json.dumps(call)}]},
        {"name": "ha_call_service", "arguments": call},
        {"calls": json.dumps([{"name": "ha_call_service", "arguments": call}])},
    ]

@pytest.mark.parametrize("tool", ["tool_call", "mcp__tool_call"])
@pytest.mark.parametrize("args", wrappers(UNLOCK), ids=["string_arguments", "legacy", "string_calls"])
def test_wrapper_low(tool, args):
    assert gate.classify(tool, args, config={})

@pytest.mark.parametrize("args", wrappers(CONTROLS[0]))
def test_safe_wrappers(args):
    assert gate.classify("tool_call", args, config={}) is None

@pytest.mark.parametrize("arguments", ["not-json", "[]", "null", {}, None, 42])
def test_named_unparseable_wrapper_blocks(arguments):
    assert gate.classify("tool_call", {"name": "ha_call_service", "arguments": arguments}, config={})

@pytest.mark.parametrize("calls", ['[{"name":"ha_call_service",', '"ha_call_service"'])
def test_unparseable_named_calls_blocks(calls):
    assert gate.classify("tool_call", {"calls": calls}, config={})

@pytest.mark.parametrize("domain", ["light", "media_player", "climate"])
@pytest.mark.parametrize("target", ["lock.front", "cover.garage", "alarm_control_panel.home", "script.open_door"])
def test_safe_domain_cannot_hide_critical_target(domain, target):
    assert gate.classify("ha_call_service", {"domain": domain, "service": "turn_on", "entity_id": target}, config={})
