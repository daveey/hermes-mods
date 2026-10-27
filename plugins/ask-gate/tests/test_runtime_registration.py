"""Registration must not advertise partial F3 protection on an old runtime."""
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize("contract", [None, {"version": 1, "replace": True,
    "accepts_hold_budget": True, "needs_buffer": True}])
def test_incompatible_core_refuses_before_adapter_or_hook(gate, monkeypatch, contract):
    from agent import pre_final_gate
    monkeypatch.setattr(pre_final_gate, "PRE_FINAL_GATE_CAPABILITIES", contract, raising=False)
    effects = []
    monkeypatch.setattr(gate.approval_adapter_loading, "install", lambda *a: effects.append("adapter"))
    ctx = SimpleNamespace(register_hook=lambda *a: effects.append(a[0]))
    with pytest.raises(RuntimeError, match="registration refused"):
        gate.register(ctx)
    assert effects == []


def test_compatible_core_registers_all_hooks(gate, monkeypatch):
    effects = []
    monkeypatch.setattr(gate.approval_adapter_loading, "install", lambda *a: effects.append("adapter"))
    ctx = SimpleNamespace(register_hook=lambda *a: effects.append(a[0]))
    gate.register(ctx)
    assert effects == ["adapter", "pre_llm_call", "post_tool_call", "pre_tool_call", "pre_final_gate"]


def test_hook_registration_failure_is_not_silently_accepted(gate, monkeypatch):
    monkeypatch.setattr(gate.approval_adapter_loading, "install", lambda *a: None)
    def register(name, cb):
        if name == "pre_final_gate":
            raise RuntimeError("hook registry unavailable")
    with pytest.raises(RuntimeError, match="hook registry unavailable"):
        gate.register(SimpleNamespace(register_hook=register))
