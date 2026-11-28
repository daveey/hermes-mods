"""Generic HA service allowlist: classification only, never calls HA."""
import importlib.util
import json
import os
from pathlib import Path

import pytest

SOURCE = Path(os.environ.get("HA_GATE_SOURCE", Path(__file__).parents[1] / "__init__.py"))
spec = importlib.util.spec_from_file_location("ha_generic_services", SOURCE)
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)

DENIED = ["stop", "restart", "reload_all", "reload_config_entry", "reload_core_config",
          "reload_custom_templates", "check_config", "set_location", "save_persistent_states",
          "unknown_future_service"]
ALLOWED = ["turn_on", "turn_off", "toggle", "update_entity"]


def shapes(call):
    entry = {"name": "ha_call_service", "arguments": call}
    return [
        ("ha_call_service", call),
        ("ha_call_service", json.dumps(call)),
        ("tool_call", entry),
        ("tool_call", {"calls": [entry]}),
        ("tool_call", {"calls": entry}),
        ("mcp__tool_call", {"calls": json.dumps([entry])}),
        ("tool_call", {"calls": [dict(entry, arguments=json.dumps(call))]}),
        ("tool_call", {"calls": [{"arguments": entry}]}),
        ("tool_call", {"calls": [{"arguments": dict(call, name="ha_call_service")}]}),
    ]


@pytest.fixture(autouse=True)
def no_gate(monkeypatch):
    monkeypatch.setattr(gate, "gate_config", lambda: None)


def check(call, blocked):
    for tool, args in shapes(call):
        assert bool(gate.classify(tool, args, config={})) == blocked, (tool, args)
        result = gate._pre_tool_call(tool, args)
        assert bool(result) == blocked, (tool, args)
        if blocked:
            assert result["action"] == "block"


@pytest.mark.parametrize("service", DENIED)
@pytest.mark.parametrize("target", [None, "light.x"])
@pytest.mark.parametrize("upper", [False, True])
def test_other_generic_services_block(service, target, upper):
    call = {"domain": "homeassistant", "service": service}
    if upper:
        call = {key: value.upper() for key, value in call.items()}
    if target is not None:
        call["entity_id"] = target
    check(call, True)


@pytest.mark.parametrize("service", ALLOWED)
@pytest.mark.parametrize("selector", ["entity_id", "data"])
def test_allowed_generic_services(service, selector):
    call = {"domain": "homeassistant", "service": service}
    call[selector] = "light.x" if selector == "entity_id" else {"entity_id": "light.x"}
    check(call, False)


def test_update_entity_lock_blocked():
    check({"domain": "homeassistant", "service": "update_entity", "entity_id": "lock.front"}, True)
