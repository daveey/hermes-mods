"""Tests for ha-critical-gate. Run: python3 -m pytest -q -p no:cacheprovider tests_gate.py"""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("ha_critical_gate_plugin", HERE / "__init__.py")
plugin = importlib.util.module_from_spec(spec)
spec.loader.exec_module(plugin)


class FakeGate:
    def __init__(self, decision="allow", message=None, code=0, fail=None):
        self.calls, self.decision, self.message, self.code, self.fail = [], decision, message, code, fail

    def __call__(self, argv, input=None, **_):
        self.calls.append(json.loads(input))
        if self.fail:
            raise self.fail
        out = json.dumps({"decision": self.decision, **({"message": self.message} if self.message else {})})
        return subprocess.CompletedProcess(argv, self.code, stdout=out + "\n", stderr="")


CONFIG = {"command": ["gate"], "timeout": 5}
UNLOCK = {"domain": "lock", "service": "unlock", "entity_id": "lock.front_door"}
LIGHT = {"domain": "light", "service": "turn_on", "entity_id": "light.den", "data": {"brightness": 80}}


def test_held_unlock_is_blocked_with_the_gate_message_and_asked_once():
    gate = FakeGate("hold", "Held, waiting for a tap")
    assert plugin.classify("ha_call_service", UNLOCK, run=gate, config=CONFIG) == "Held, waiting for a tap"
    assert gate.calls == [dict(UNLOCK, data=None, agent=plugin.agent_name())]


def test_allowed_light_passes_through_unchanged_and_is_still_reported():
    gate = FakeGate("allow")
    assert plugin.classify("ha_call_service", LIGHT, run=gate, config=CONFIG) is None
    assert gate.calls[0]["data"] == {"brightness": 80}


def test_string_data_is_decoded_for_the_gate():
    gate = FakeGate("allow")
    plugin.classify("ha_call_service", dict(LIGHT, data='{"brightness": 10}'), run=gate, config=CONFIG)
    assert gate.calls[0]["data"] == {"brightness": 10}


def test_no_gate_blocks_only_opening_security_calls():
    for call in (UNLOCK, {"domain": "alarm_control_panel", "service": "alarm_disarm"},
                 {"domain": "cover", "service": "open_cover", "entity_id": "cover.garage"}):
        assert plugin.decide(call, config={}).startswith("BLOCKED"), call
    for call in (LIGHT, {"domain": "lock", "service": "lock"}, {"domain": "cover", "service": "close_cover"}):
        assert plugin.decide(call, config={}) is None, call


def test_a_failing_gate_fails_closed_for_critical_and_open_for_the_rest():
    for gate in (FakeGate(code=1), FakeGate(fail=subprocess.TimeoutExpired("gate", 5)), FakeGate("bogus")):
        assert plugin.classify("ha_call_service", UNLOCK, run=gate, config=CONFIG).startswith("BLOCKED")
        assert plugin.classify("ha_call_service", LIGHT, run=gate, config=CONFIG) is None


def test_deny_blocks():
    assert plugin.classify("ha_call_service", LIGHT, run=FakeGate("deny", "no such light"), config=CONFIG) == "no such light"


def test_wrapped_tool_call_is_checked():
    gate = FakeGate("hold", "held")
    args = {"calls": [{"name": "ha_get_state", "arguments": {}}, {"name": "ha_call_service", "arguments": UNLOCK}]}
    assert plugin.classify("tool_call", args, run=gate, config=CONFIG) == "held"


def test_other_tools_are_ignored():
    assert plugin._pre_tool_call(tool_name="terminal", args={"command": "ls"}) is None
    assert plugin._pre_tool_call(tool_name="ha_get_state", args={"entity_id": "lock.front_door"}) is None


def test_hook_blocks_through_env_command(tmp_path, monkeypatch):
    script = tmp_path / "gate.py"
    script.write_text("import json,sys\ncall=json.load(sys.stdin)\n"
                      "print(json.dumps({'decision': 'hold' if call['service']=='unlock' else 'allow', 'message': 'held'}))\n")
    monkeypatch.setenv("HA_CRITICAL_GATE_COMMAND", "%s %s" % (sys.executable, script))
    assert plugin._pre_tool_call(tool_name="ha_call_service", args=UNLOCK) == {"action": "block", "message": "held"}
    assert plugin._pre_tool_call(tool_name="ha_call_service", args=LIGHT) is None


def test_config_file_in_root_home_covers_profiles(tmp_path, monkeypatch):
    root = tmp_path / "home"
    (root / "profiles" / "bb-worker").mkdir(parents=True)
    (root / plugin.CONFIG_NAME).write_text(json.dumps({"command": ["x"]}))
    monkeypatch.delenv("HA_CRITICAL_GATE_COMMAND", raising=False)
    monkeypatch.setenv("HERMES_HOME", str(root / "profiles" / "bb-worker"))
    monkeypatch.setattr(plugin, "_homes", lambda: [root / "profiles" / "bb-worker", root])
    assert plugin.gate_config()["command"] == ["x"]
    assert plugin.agent_name() == "bb-worker"
