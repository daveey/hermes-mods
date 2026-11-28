"""NB1/NB2 standalone no-gate regressions. No core imports or live HA calls."""
import importlib.util
import json
import os
from pathlib import Path

import pytest

SOURCE = Path(os.environ.get("HA_GATE_SOURCE", Path(__file__).parents[1] / "__init__.py"))
spec = importlib.util.spec_from_file_location("ha_followup", SOURCE)
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


@pytest.fixture(autouse=True)
def no_gate(monkeypatch):
    monkeypatch.setattr(gate, "gate_config", lambda: None)


def result(api, tool, args):
    if api == "classify":
        return gate.classify(tool, args, config={})
    return gate._pre_tool_call(tool, args)


APIS = ["classify", "hook"]
UNSAFE_TARGETS = [
    ("toggle", "valve.main"), ("turn_on", "siren.x"),
    ("turn_on", "input_boolean.door_trigger"), ("turn_on", "acme.door"),
    ("update_entity", "sensor.x"), ("lock", "lock.front"),
    ("close_cover", "cover.garage"), ("alarm_arm_home", "alarm_control_panel.home"),
]


@pytest.mark.parametrize("api", APIS)
@pytest.mark.parametrize("service,target", UNSAFE_TARGETS)
@pytest.mark.parametrize("selector", ["entity_id", "data"])
def test_generic_non_safe_target(api, service, target, selector):
    args = {"domain": "homeassistant", "service": service, "entity_id": target}
    if selector == "data":
        args["data"] = {"entity_id": args.pop("entity_id")}
    assert result(api, "ha_call_service", args)


@pytest.mark.parametrize("api", APIS)
@pytest.mark.parametrize("service,target", [
    ("turn_on", "light.x"), ("turn_off", "media_player.x"), ("turn_on", "climate.x"),
])
def test_generic_safe_target(api, service, target):
    assert result(api, "ha_call_service", {"domain": "homeassistant", "service": service,
                                           "entity_id": target}) is None


@pytest.mark.parametrize("api", APIS)
@pytest.mark.parametrize("target", [["light.x", "valve.main"], "light.x,siren.x", " ACME.door "])
def test_generic_mixed_targets(api, target):
    assert result(api, "ha_call_service", {"domain": "homeassistant", "service": "turn_on",
                                           "entity_id": target})


SHAPES = ["single_object", "whitespace", "wire_prefix", "mixed_case", "nested_lift",
          "beside_lift", "nested_string_lift", "beside_string_lift"]


def wrapper(shape, call):
    entry = {"name": "ha_call_service", "arguments": call}
    if shape == "single_object":
        return {"calls": entry}
    if shape in {"whitespace", "wire_prefix", "mixed_case"}:
        entry["name"] = {"whitespace": "  ha_call_service \t", "wire_prefix": "mcp__ha_call_service",
                         "mixed_case": " MCP__HA_CALL_SERVICE "}[shape]
    else:
        inner = ({"name": "ha_call_service", "arguments": call}
                 if shape.startswith("nested") else dict(call, name="ha_call_service"))
        entry = {"arguments": json.dumps(inner) if "string" in shape else inner}
    return {"calls": [entry]}


@pytest.mark.parametrize("api", APIS)
@pytest.mark.parametrize("tool", ["tool_call", "mcp__tool_call"])
@pytest.mark.parametrize("shape", SHAPES)
@pytest.mark.parametrize("call", [{"domain": "lock", "service": "unlock"},
                                  {"domain": "valve", "service": "open_valve"}])
def test_nb1_unsafe_wrappers(api, tool, shape, call):
    assert result(api, tool, wrapper(shape, call))


@pytest.mark.parametrize("api", APIS)
@pytest.mark.parametrize("shape", SHAPES)
def test_nb1_safe_wrappers(api, shape):
    assert result(api, "tool_call", wrapper(shape, {"domain": "light", "service": "turn_on"})) is None


@pytest.mark.parametrize("api", APIS)
@pytest.mark.parametrize("entry", [
    {"arguments": {"summary": "HA_CALL_SERVICE"}},
    {"name": "  ", "arguments": "mcp__HA_CALL_SERVICE"},
    {"arguments": {"deeper": {"summary": "ha_call_service"}}},
])
def test_nameless_mentions_block(api, entry):
    assert result(api, "tool_call", {"calls": [entry]})


@pytest.mark.parametrize("api", APIS)
@pytest.mark.parametrize("shape", ["single_object", "batch", "legacy", "json_batch"])
def test_explicit_other_tool_mention_is_not_ha(api, shape):
    entry = {"name": "kanban_complete", "arguments": {"summary": "Fixed ha_call_service wrappers"}}
    args = {"single_object": {"calls": entry}, "batch": {"calls": [entry]}, "legacy": entry,
            "json_batch": {"calls": json.dumps([entry])}}[shape]
    assert result(api, "tool_call", args) is None


@pytest.mark.parametrize("api", APIS)
@pytest.mark.parametrize("args", [
    "HA_CALL_SERVICE garbage{", {"calls": '"HA_CALL_SERVICE"'},
    {"calls": ["HA_CALL_SERVICE"]},
    {"calls": [{"name": " MCP__HA_CALL_SERVICE ", "arguments": "not-json"}]},
])
def test_malformed_ha_spellings_block(api, args):
    assert result(api, "tool_call", args)


@pytest.mark.parametrize("api", APIS)
def test_json_single_object_retains_existing_refusal(api):
    args = {"calls": json.dumps({"name": "ha_call_service", "arguments":
                               {"domain": "light", "service": "turn_on"}})}
    assert result(api, "tool_call", args)
