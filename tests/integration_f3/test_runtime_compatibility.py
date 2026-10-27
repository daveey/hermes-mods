"""Compatibility refusal through the real plugin loader, not direct hooks."""
import pytest


@pytest.mark.parametrize("missing", [None, "post_transform_release", "replace", "accepts_hold_budget", "needs_buffer"])
def test_old_runtime_does_not_load_partial_ask_gate(harness, monkeypatch, caplog, missing):
    from agent import pre_final_gate
    h = harness
    h.manager.unload()
    contract = dict(pre_final_gate.PRE_FINAL_GATE_CAPABILITIES)
    if missing is None:
        monkeypatch.delattr(pre_final_gate, "PRE_FINAL_GATE_CAPABILITIES")
    else:
        del contract[missing]
        monkeypatch.setattr(pre_final_gate, "PRE_FINAL_GATE_CAPABILITIES", contract)
    h.manager.discover_and_load()
    loaded = [p for p in h.manager._plugins.values() if p.manifest.name == "ask-gate"]
    assert len(loaded) == 1 and not loaded[0].enabled
    assert "registration refused" in loaded[0].error
    assert "Install a compatible Hermes core" in caplog.text
    for hook in ("pre_final_gate", "pre_llm_call", "pre_tool_call", "post_tool_call"):
        assert all("ask_gate" not in cb.__module__ for cb in h.manager.iter_hook_callbacks(hook))
    # user-steps needs buffering/publication, not replacement or hold budgets.
    steps = next(p for p in h.manager._plugins.values() if p.manifest.name == "user-steps")
    supported = missing in {"replace", "accepts_hold_budget"}
    assert steps.enabled is supported
    assert bool(list(h.manager.iter_hook_callbacks("transform_llm_output"))) is supported
    assert bool(list(h.manager.iter_hook_callbacks("pre_final_gate"))) is supported


def test_final_registration_failure_rolls_back_earlier_hooks(harness, monkeypatch, caplog):
    from hermes_cli.plugins import PluginContext
    h = harness
    h.manager.unload()
    original = PluginContext.register_hook

    def reject_final(self, name, callback, *args, **kwargs):
        if name == "pre_final_gate":
            raise RuntimeError("test final registry unavailable")
        return original(self, name, callback, *args, **kwargs)

    monkeypatch.setattr(PluginContext, "register_hook", reject_final)
    h.manager.discover_and_load()
    loaded = [p for p in h.manager._plugins.values() if p.manifest.name == "ask-gate"]
    assert len(loaded) == 1 and not loaded[0].enabled
    assert "test final registry unavailable" in loaded[0].error
    assert "test final registry unavailable" in caplog.text
    for hook in ("pre_llm_call", "pre_tool_call", "post_tool_call", "pre_final_gate"):
        assert all("ask_gate" not in cb.__module__ for cb in h.manager.iter_hook_callbacks(hook))
    assert list(h.manager.iter_hook_callbacks("transform_llm_output")) == []
    steps = next(p for p in h.manager._plugins.values() if p.manifest.name == "user-steps")
    assert not steps.enabled
    assert "test final registry unavailable" in steps.error
