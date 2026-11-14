"""Boolean config must control status freshness without an environment override."""
import importlib.util
from pathlib import Path

import pytest


HERE = Path(__file__).resolve().parents[1]
ASK = "why are we blocked and nothing running"
DRAFT = "The card is blocked on review."


@pytest.fixture
def configured_coach(monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location("coach_boolean_config", HERE / "__init__.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.delenv("COACH_STATUS_FRESH", raising=False)
    # Patch the config reader, not status_fresh_enabled: exercise the real lookup
    # through the same sibling cache used by the registered gate.
    settings = {"status_fresh": True}
    monkeypatch.setattr(mod._load("second_opinion"), "_config_settings", lambda: settings)
    monkeypatch.setenv("COACH_STATUS_PLAIN", "on")
    store = mod._load("core").Store(root=tmp_path / "coach")
    _, hooks = mod.build(store=store, checkout=None)
    return mod._load("reply_check"), settings, hooks


@pytest.mark.parametrize("enabled", [False, True])
def test_boolean_config_is_read(configured_coach, enabled):
    rc, settings, _ = configured_coach
    settings["status_fresh"] = enabled
    assert rc.status_fresh_enabled() is enabled


@pytest.mark.parametrize("enabled", [False, True])
def test_boolean_config_controls_buffer_and_gate(configured_coach, enabled):
    _, settings, hooks = configured_coach
    settings["status_fresh"] = enabled
    hooks["pre_llm_call"](session_id="boolean-config", user_message=ASK)
    gate = hooks["pre_final_gate:status_fresh"]
    assert gate.needs_buffer is enabled
    assert gate.needs_buffer_for_session(session_id="boolean-config") is enabled
    result = gate(
        session_id="boolean-config", platform="desktop", final_response=DRAFT,
        turn_messages=[{"role": "user", "content": ASK},
                       {"role": "assistant", "content": DRAFT}], attempt=0,
    )
    if enabled:
        assert result and result["hold"] is True
        assert "status_fresh" in result["nudge"]
    else:
        assert result is None


def test_boolean_false_disables_an_already_armed_gate(configured_coach):
    _, settings, hooks = configured_coach
    hooks["pre_llm_call"](session_id="changed-config", user_message=ASK)
    gate = hooks["pre_final_gate:status_fresh"]
    assert gate.needs_buffer_for_session(session_id="changed-config") is True
    settings["status_fresh"] = False
    assert gate.needs_buffer_for_session(session_id="changed-config") is False
    assert gate(
        session_id="changed-config", platform="desktop", final_response=DRAFT,
        turn_messages=[{"role": "user", "content": ASK}], attempt=0,
    ) is None
