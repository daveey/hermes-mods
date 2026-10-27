"""Narrow advertisement tests; these do not prove end-to-end delivery."""
import builtins
import importlib.util
import sys
from pathlib import Path
from types import MappingProxyType, SimpleNamespace

import pytest


SPEC = importlib.util.spec_from_file_location(
    "ask_gate_runtime_contract_tested", Path(__file__).resolve().parents[1] / "runtime_contract.py"
)
contract_check = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(contract_check)


def complete_contract():
    # Explicit protocol fixture, not a claim about the installed core or revision.
    return {"version": 1, "replace": True, "accepts_hold_budget": True,
            "needs_buffer": True, "post_transform_release": True}


def advertise(monkeypatch, value):
    runtime = SimpleNamespace(PRE_FINAL_GATE_CAPABILITIES=value)
    requested = []

    def import_module(name):
        requested.append(name)
        assert name == "agent.pre_final_gate"
        return runtime

    monkeypatch.setattr(contract_check.importlib, "import_module", import_module)
    return requested


def test_absent_advertisement_rejected_despite_legacy_hook(monkeypatch):
    runtime = SimpleNamespace(HOOK="pre_final_gate", evaluate=lambda: None)
    monkeypatch.setattr(contract_check.importlib, "import_module", lambda name: runtime)
    with pytest.raises(contract_check.RuntimeContractError, match="registration refused.*missing"):
        contract_check.require_runtime_contract()


@pytest.mark.parametrize("value", [None, False, True, 1, "1", [], (), {"replace"}])
def test_malformed_contract_rejected(monkeypatch, value):
    advertise(monkeypatch, value)
    with pytest.raises(contract_check.RuntimeContractError, match="missing or malformed"):
        contract_check.require_runtime_contract()


@pytest.mark.parametrize("version", [None, False, True, 0, 2, -1, 1.0, "1", [], {}])
def test_wrong_or_malformed_version_rejected(monkeypatch, version):
    value = complete_contract()
    value["version"] = version
    advertise(monkeypatch, value)
    with pytest.raises(contract_check.RuntimeContractError, match="contract version"):
        contract_check.require_runtime_contract()


def test_missing_version_rejected(monkeypatch):
    value = complete_contract()
    del value["version"]
    advertise(monkeypatch, value)
    with pytest.raises(contract_check.RuntimeContractError, match="contract version"):
        contract_check.require_runtime_contract()


@pytest.mark.parametrize("name", contract_check.REQUIRED_CAPABILITIES)
def test_each_missing_capability_rejected(monkeypatch, name):
    value = complete_contract()
    del value[name]
    advertise(monkeypatch, value)
    with pytest.raises(contract_check.RuntimeContractError, match=name):
        contract_check.require_runtime_contract()


@pytest.mark.parametrize("name", contract_check.REQUIRED_CAPABILITIES)
@pytest.mark.parametrize("invalid", [False, None, 1, "true", [], {}])
def test_truthy_and_malformed_flags_rejected(monkeypatch, name, invalid):
    value = complete_contract()
    value[name] = invalid
    advertise(monkeypatch, value)
    with pytest.raises(contract_check.RuntimeContractError, match=name):
        contract_check.require_runtime_contract()


@pytest.mark.parametrize("readonly", [False, True])
def test_complete_contract_accepted_without_mutation(monkeypatch, readonly):
    value = complete_contract()
    advertised = MappingProxyType(value) if readonly else value
    requested = advertise(monkeypatch, advertised)
    assert contract_check.require_runtime_contract() is None
    assert requested == ["agent.pre_final_gate"]
    assert value == complete_contract()


def test_additive_capability_allowed(monkeypatch):
    value = {**complete_contract(), "future_extension": True}
    advertise(monkeypatch, value)
    contract_check.require_runtime_contract()


@pytest.mark.parametrize("error", [ModuleNotFoundError("old core"), RuntimeError("broken import")])
def test_import_failure_is_actionable_and_chained(monkeypatch, error):
    def fail(name):
        raise error

    monkeypatch.setattr(contract_check.importlib, "import_module", fail)
    with pytest.raises(contract_check.RuntimeContractError, match="Install a compatible Hermes core") as got:
        contract_check.require_runtime_contract()
    assert got.value.__cause__ is error


def test_contract_check_does_not_import_run_agent(monkeypatch):
    original_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        assert name != "run_agent" and not name.startswith("run_agent.")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    monkeypatch.delitem(sys.modules, "run_agent", raising=False)
    # Exercise the real lightweight core import, not a stand-in runtime module.
    runtime = contract_check.importlib.import_module("agent.pre_final_gate")
    monkeypatch.setattr(runtime, "PRE_FINAL_GATE_CAPABILITIES", complete_contract(), raising=False)
    contract_check.require_runtime_contract()
    assert "run_agent" not in sys.modules
