"""Source-review regressions; HA_GATE_SOURCE selects an unmodified RED control."""
import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

SOURCE = Path(os.environ.get("HA_GATE_SOURCE", Path(__file__).parents[1] / "__init__.py"))
spec = importlib.util.spec_from_file_location("ha_gate_review", SOURCE)
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


@pytest.mark.parametrize("domain", ["cover", "script", "scene", "automation", "button", "switch"])
@pytest.mark.parametrize("service", ["turn_on", "toggle"])
@pytest.mark.parametrize("location", ["entity_id", "data", "json_data"])
@pytest.mark.parametrize("failed_gate", [False, True])
def test_target_domains_fail_closed(domain, service, location, failed_gate):
    call = {"domain": "homeassistant", "service": service}
    target = domain + ".actuator"
    if location == "entity_id":
        call["entity_id"] = target
    else:
        data = {"entity_id": ["light.safe", target]}
        call["data"] = json.dumps(data) if location == "json_data" else data
    def fail(*a, **kw):
        raise OSError("offline")
    assert gate.decide(call, config={"command": ["unused"]} if failed_gate else {}, run=fail)


@pytest.mark.parametrize("domain", ["script", "scene", "automation", "button", "switch"])
def test_direct_opaque_actuators_fail_closed(domain):
    assert gate.decide({"domain": domain, "service": "activate"}, config={})


@pytest.mark.parametrize("target", [None, "all", "group.actuators", [], 42])
def test_unresolved_generic_target_fails_closed(target):
    assert gate.decide({"domain": "homeassistant", "service": "turn_on", "entity_id": target}, config={})


def test_agent_home_command_is_not_executed(tmp_path, monkeypatch):
    (tmp_path / gate.CONFIG_NAME).write_text(json.dumps({"command": ["/bin/true"]}))
    monkeypatch.setattr(gate, "_homes", lambda: [tmp_path])
    monkeypatch.delenv("HA_CRITICAL_GATE_COMMAND", raising=False)
    assert gate.gate_config() is None


def test_environment_command_is_not_authority(monkeypatch):
    monkeypatch.setenv("HA_CRITICAL_GATE_COMMAND", "/bin/true")
    assert gate.gate_config() is None


@pytest.mark.parametrize("mode,uid,writable", [(0o100644, 1000, False), (0o100664, 0, False),
                                               (0o100666, 0, False), (0o100644, 0, True)])
def test_untrusted_metadata_rejected(mode, uid, writable, monkeypatch):
    monkeypatch.setattr(gate.os, "access", lambda *a, **k: writable)
    with pytest.raises(ValueError):
        gate._check_metadata(SimpleNamespace(st_uid=uid, st_mode=mode), Path("/fixture"), directory=False)


def test_secure_metadata_accepted(monkeypatch):
    monkeypatch.setattr(gate.os, "access", lambda *a, **k: False)
    gate._check_metadata(SimpleNamespace(st_uid=0, st_mode=0o100644), Path("/fixture"), directory=False)


@pytest.mark.parametrize("kind", ["owned", "symlink", "writable_parent", "directory"])
def test_untrusted_paths_rejected(tmp_path, kind):
    target = tmp_path / "config"
    target.write_text('{}')
    if kind == "symlink":
        link = tmp_path / "link"
        link.symlink_to(target)
        target = link
    elif kind == "directory":
        target = tmp_path
    elif kind == "writable_parent":
        tmp_path.chmod(0o777)
    with pytest.raises((OSError, ValueError)):
        gate._read_trusted_config(target)


@pytest.mark.parametrize("config", [{}, {"command": []}, {"command": ["relative"]},
    {"command": [1]}, {"command": ["/usr/bin/python3", "/agent/gate.py"]},
    {"command": ["/bin/true"], "timeout": -1},
    {"command": ["/bin/true"], "timeout": True}, {"command": ["/bin/true"], "timeout": float("inf")}])
def test_malformed_trusted_config_fails_closed(config, monkeypatch):
    monkeypatch.setattr(gate, "_read_trusted_config", lambda path: config)
    assert gate.gate_config() is None


def test_trusted_config_success(monkeypatch):
    monkeypatch.setattr(gate, "_read_trusted_config", lambda path: {"command": ["/usr/bin/true"], "timeout": 5})
    monkeypatch.setattr(gate, "_check_trusted_path", lambda *a, **kw: None)
    assert gate.gate_config() == {"command": ["/usr/bin/true"], "timeout": 5}


def test_untrusted_executable_rejected(monkeypatch):
    monkeypatch.setattr(gate, "_read_trusted_config", lambda path: {"command": ["/fixture/gate"]})
    def reject(*a, **kw):
        raise ValueError("writable executable")
    monkeypatch.setattr(gate, "_check_trusted_path", reject)
    assert gate.gate_config() is None


def test_wrapper_exception_fails_closed(monkeypatch):
    def fail(*a, **kw):
        raise ValueError("config failure")
    monkeypatch.setattr(gate, "classify", fail)
    args = {"calls": [{"name": "ha_call_service", "arguments": {"domain": "script", "service": "run"}}]}
    assert gate._pre_tool_call(tool_name="tool_call", args=args)["action"] == "block"


@pytest.mark.parametrize("call", [
    {"domain": "homeassistant", "service": "turn_on", "entity_id": "light.safe"},
    {"domain": "cover", "service": "close_cover", "entity_id": "cover.garage"},
    {"domain": "homeassistant", "service": "turn_off", "entity_id": "light.safe"},
])
def test_known_safe_operations_stay_available(call):
    assert gate.decide(call, config={}) is None
