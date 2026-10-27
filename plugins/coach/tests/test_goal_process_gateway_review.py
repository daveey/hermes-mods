"""Adversarial receipt routing regressions; isolated native fixture, no network."""
import importlib.util
from pathlib import Path

import pytest

from gateway.session_identity import replace_source
from tests.gateway.test_process_consumer_health import owned, health


def assert_public_gate_rejects(owned):
    path = Path(__file__).with_name("test_goal_process_gateway.py")
    spec = importlib.util.spec_from_file_location("review_gateway_goal", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    v, evidence, sid = module.args(owned)
    from gateway.run import _profile_runtime_scope
    snapshot = owned[2].ownership_snapshot(owned[3].id)
    with _profile_runtime_scope(Path(snapshot.home)):
        with pytest.raises(ValueError):
            module.gd.validate_disposition(v, evidence, session_id=sid)



@pytest.mark.asyncio
async def test_receipt_rejects_current_route_origin_replacement(owned):
    runner, _, _, _, entry, event, _ = owned
    assert await runner._inject_watch_notification("fixture", event) is True
    assert health(owned) is not None
    entry.origin = replace_source(entry.origin, chat_id="different-chat")
    # Future native completion resolution now uses a different destination.
    assert runner._build_process_event_source(event).chat_id == "different-chat"
    assert_public_gate_rejects(owned)
    assert health(owned) is None


@pytest.mark.asyncio
async def test_receipt_rejects_source_drift_during_admission(owned, monkeypatch):
    runner, adapter, _, _, _, event, _ = owned
    original = adapter.handle_message

    async def drift_after_acceptance(message):
        await original(message)
        assert message._gateway_accepted is True
        message.source.chat_id = "different-chat"

    monkeypatch.setattr(adapter, "handle_message", drift_after_acceptance)
    assert await runner._inject_watch_notification("fixture", event) is True
    assert_public_gate_rejects(owned)
    assert health(owned) is None


@pytest.mark.asyncio
async def test_source_resolution_failure_clears_prior_receipt(owned, monkeypatch):
    runner, _, _, _, _, event, _ = owned
    assert await runner._inject_watch_notification("fixture", event) is True
    assert health(owned) is not None
    monkeypatch.setattr(runner, "_build_process_event_source", lambda evt: None)
    assert await runner._inject_watch_notification("fixture", event) is None
    assert_public_gate_rejects(owned)
    assert health(owned) is None
